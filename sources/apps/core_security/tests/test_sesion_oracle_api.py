"""
Pruebas de la credencial de sesion contra el Oracle Database 23ai Free REAL del proyecto.

Verifican que la validacion de la credencial opaca se resuelve consultando la tabla fisica
`sesion_usuario` del esquema T.5 a traves del driver `python-oracledb`, con el DDL aplicado
por Liquibase, y no contra una estructura paralela del servicio.

Un `dict` en memoria NO es evidencia de persistencia: si no hay engine Docker alcanzable las
pruebas que necesitan el motor se SALTAN (`SALTAR_SIN_DOCKER`), nunca se degradan a H2,
SQLite ni a ningun doble en memoria.
"""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connections
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.contexto import utc_now
from apps.core.models import SesionUsuarioEntity, UsuarioEntity
from apps.core_security import mensajes
from apps.core_security.servicios.sesiones import ServicioSesiones
from apps.core_security.tests.conftest import CLAVE_DE_PRUEBA, SALTAR_SIN_DOCKER, cabecera_bearer

pytestmark = [pytest.mark.integration]

#: Ruta protegida del contrato sobre la que se ejercita la denegacion uniforme.
RUTA_PROTEGIDA = "/api/incidents"

#: Codigo estable del 401 de sesion: el MISMO para los cuatro escenarios de AC-SES-04.
CODIGO_SESION_INVALIDA = "AUTH_SESSION_INVALID"

#: Motivos internos de `SesionInvalidaError`. Son traza, no respuesta: ninguno puede asomar en el cuerpo.
MOTIVOS_INTERNOS_QUE_NO_PUEDEN_FILTRARSE = ("revocada", "caducada", "no_encontrada", "ausente")

#: Valor del enumerado `ck_sesion_usuario_reason_enum` con el que se sella una sesion vencida.
MOTIVO_REVOCACION_POR_CADUCIDAD = "expired"

#: Columnas fisicas del DoD que la tabla `sesion_usuario` debe exponer con ESE nombre exacto.
COLUMNAS_FISICAS_T5 = {
    "session_id",
    "user_id",
    "role_code",
    "issued_at",
    "expires_at",
    "last_activity_at",
    "revoked_at",
}

#: Claves de material secreto que el cuerpo de EP-001 no puede publicar jamas (REQ-063).
CLAVES_SECRETAS_PROHIBIDAS = ("password", "passwordHash", "password_hash", "passwordSalt", "password_salt")

#: SELECT crudo contra Oracle: el oraculo de persistencia lee la FILA, no la instancia del ORM.
SQL_SESION_POR_ID = (
    "SELECT session_id, user_id, role_code, issued_at, expires_at, last_activity_at, revoked_at "
    "FROM sesion_usuario WHERE session_id = %s"
)


def _leer_fila_de_sesion(session_id: str) -> tuple | None:
    """Lee la fila fisica de `sesion_usuario` con SQL crudo, sin pasar por el ORM ni por sus caches."""

    with connections["default"].cursor() as cursor:
        cursor.execute(SQL_SESION_POR_ID, [session_id])
        return cursor.fetchone()


def test_la_sesion_se_persiste_con_los_nombres_fisicos_del_esquema_T5() -> None:
    """
    Oraculo anti-traduccion del esquema (T.5 schema-names): el ORM no renombra el DDL.

    No necesita contenedor porque interroga el MAPEO del modelo, no los datos: la tabla debe
    llamarse `sesion_usuario`, el DDL lo gobierna Liquibase (`managed = False`) y cada columna
    del DoD debe estar mapeada con exactamente su nombre fisico, sin traducciones al castellano
    ni abreviaturas inventadas por la capa Python.
    """

    assert SesionUsuarioEntity._meta.db_table == "sesion_usuario", (
        f"La sesion se persiste en `sesion_usuario`, no en `{SesionUsuarioEntity._meta.db_table}`"
    )
    assert SesionUsuarioEntity._meta.managed is False, (
        "El DDL de `sesion_usuario` lo gobierna Liquibase (ARC-016): `managed` debe ser False"
    )

    columnas_reales = {
        columna
        for campo in SesionUsuarioEntity._meta.get_fields()
        if (columna := getattr(campo, "column", None)) is not None
    }

    faltantes = COLUMNAS_FISICAS_T5 - columnas_reales
    assert not faltantes, (
        f"Columnas fisicas del DoD sin mapear con su nombre exacto en `sesion_usuario`: "
        f"{sorted(faltantes)}; mapeadas: {sorted(columnas_reales)}"
    )


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_SES_04_sin_sesion_manipulada_revocada_o_caducada_devuelve_el_mismo_401(
    cliente_api: APIClient, sesion_vigente: SesionUsuarioEntity, usuario_activo: UsuarioEntity
) -> None:
    """
    [AC-SES-04] Los cuatro modos de fallo de sesion responden una denegacion INDISTINGUIBLE.

    Credencial ausente, credencial manipulada, sesion revocada y sesion caducada se ejercitan
    contra el Oracle REAL (las dos ultimas con filas de verdad en `sesion_usuario`) y deben
    devolver el mismo `status_code`, el mismo `code` y el mismo `message`. Si alguna variase,
    la API seria un oraculo para distinguir una sesion que existio de una que nunca existio.

    La fixture `sesion_vigente` entra a proposito: garantiza que el usuario TIENE una sesion
    valida en la tabla, de modo que el 401 de los otros cuatro casos no pueda venir de una
    tabla vacia ni de un usuario sin sesiones.
    """

    servicio = ServicioSesiones()

    sesion_revocada = servicio.emitir(usuario_activo)
    servicio.revocar(sesion_revocada.session_id, "logout")

    sesion_caducada = servicio.emitir(usuario_activo)
    # `.update()` del queryset: escribe SOLO esa columna, sin pasar por la logica del modelo.
    SesionUsuarioEntity.objects.filter(pk=sesion_caducada.session_id).update(
        expires_at=utc_now() - timedelta(minutes=1)
    )

    respuestas = {
        "sin_credencial": cliente_api.get(RUTA_PROTEGIDA),
        # Al ser opaca, manipular la credencial solo puede producir un uuid que no esta en la tabla.
        "manipulada": cliente_api.get(RUTA_PROTEGIDA, **cabecera_bearer(str(uuid4()))),
        "revocada": cliente_api.get(RUTA_PROTEGIDA, **cabecera_bearer(sesion_revocada.session_id)),
        "caducada": cliente_api.get(RUTA_PROTEGIDA, **cabecera_bearer(sesion_caducada.session_id)),
    }
    cuerpos = {nombre: respuesta.json() for nombre, respuesta in respuestas.items()}

    for nombre, respuesta in respuestas.items():
        assert respuesta.status_code == 401, f"El escenario `{nombre}` no ha respondido 401"
        cuerpo = cuerpos[nombre]
        assert cuerpo["code"] == CODIGO_SESION_INVALIDA, f"Codigo distinto en el escenario `{nombre}`"
        assert cuerpo["message"] == mensajes.SESION_REQUERIDA, f"Mensaje distinto en el escenario `{nombre}`"
        assert cuerpo["details"] == [], f"El escenario `{nombre}` adjunta detalles del fallo"

    # Asercion CENTRAL de AC-SES-04: la respuesta no permite distinguir POR QUE se denego.
    huellas = {
        (respuestas[nombre].status_code, cuerpos[nombre]["code"], cuerpos[nombre]["message"]) for nombre in respuestas
    }
    assert len(huellas) == 1, f"Las denegaciones son distinguibles entre si: {sorted(huellas)}"

    # El `traceId` SI cambia: es el correlador de cada peticion, no parte del mensaje uniforme.
    correladores = {cuerpo["traceId"] for cuerpo in cuerpos.values()}
    assert len(correladores) == len(respuestas), f"Los cuatro `traceId` deben ser distintos: {sorted(correladores)}"

    for nombre, cuerpo in cuerpos.items():
        serializado = json.dumps(cuerpo, ensure_ascii=False)
        for motivo in MOTIVOS_INTERNOS_QUE_NO_PUEDEN_FILTRARSE:
            assert motivo not in serializado, (
                f"El escenario `{nombre}` filtra el motivo interno `{motivo}` en la respuesta: {serializado}"
            )


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_SES_04_la_sesion_caducada_queda_revocada_en_oracle_con_motivo_expired(
    cliente_api: APIClient, usuario_activo: UsuarioEntity
) -> None:
    """
    [AC-SES-04][REQ-070] Al denegar por caducidad, la fila se SELLA como revocada y no se borra.

    El oraculo es la fila REAL releida de `sesion_usuario` en Oracle, nunca la instancia en
    memoria: tras el 401, `revoked_at` debe estar sellado y `revocation_reason` valer `expired`,
    con la fila todavia presente como evidencia del acceso (REQ-047, REQ-070).
    """

    sesion = ServicioSesiones().emitir(usuario_activo)
    SesionUsuarioEntity.objects.filter(pk=sesion.session_id).update(expires_at=utc_now() - timedelta(minutes=1))

    respuesta = cliente_api.get(RUTA_PROTEGIDA, **cabecera_bearer(sesion.session_id))

    assert respuesta.status_code == 401

    assert SesionUsuarioEntity.objects.filter(pk=sesion.session_id).exists(), (
        "La sesion caducada se ha BORRADO: debe revocarse y permanecer como evidencia (REQ-047, REQ-070)"
    )
    persistida = SesionUsuarioEntity.objects.get(pk=sesion.session_id)
    assert persistida.revoked_at is not None, "La caducidad no ha sellado `revoked_at` en la fila de Oracle"
    assert persistida.revocation_reason == MOTIVO_REVOCACION_POR_CADUCIDAD, (
        f"El motivo de revocacion persistido debe ser `expired`, no `{persistida.revocation_reason}`"
    )


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_la_sesion_emitida_persiste_sus_columnas_en_oracle_y_no_en_memoria(
    sesion_vigente: SesionUsuarioEntity,
) -> None:
    """
    [DoD persistencia][REQ-056] La sesion emitida vive como FILA en `sesion_usuario` de Oracle.

    El oraculo es deliberadamente un SELECT crudo por el driver `python-oracledb`, no una lectura
    del ORM: un `dict` en memoria del servicio nunca habria superado este SELECT, porque no existe
    ninguna tabla que interrogar detras de el. Se comprueban ademas las invariantes de la emision:
    las tres marcas temporales informadas, el vencimiento absoluto SIEMPRE futuro respecto a la
    emision (REQ-056) y la sesion recien emitida sin revocar.
    """

    fila = _leer_fila_de_sesion(sesion_vigente.session_id)

    assert fila is not None, (
        "`sesion_usuario` no contiene la sesion emitida: el estado de sesion vive en memoria, no en la base"
    )

    session_id, user_id, role_code, issued_at, expires_at, last_activity_at, revoked_at = fila

    assert session_id == sesion_vigente.session_id, "El `session_id` persistido no es el de la sesion emitida"
    assert user_id == sesion_vigente.user_id, "La fila de Oracle no apunta al titular de la sesion emitida"
    assert role_code == sesion_vigente.role_code_id, "El rol congelado en la fila no es el de la sesion emitida"

    assert issued_at is not None, "`issued_at` no esta informado en la fila de Oracle"
    assert expires_at is not None, "`expires_at` no esta informado en la fila de Oracle"
    assert last_activity_at is not None, "`last_activity_at` no esta informado en la fila de Oracle"

    assert expires_at > issued_at, (
        f"El vencimiento absoluto debe ser posterior a la emision (REQ-056): {expires_at} <= {issued_at}"
    )
    assert revoked_at is None, "Una sesion recien emitida no puede llegar a la base con `revoked_at` sellado"


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_SES_01_una_peticion_aceptada_refresca_last_activity_at_en_la_base(
    cliente_api: APIClient, sesion_vigente: SesionUsuarioEntity
) -> None:
    """
    [AC-SES-01][REQ-056] Cada peticion aceptada refresca `last_activity_at` EN ORACLE.

    Se retrasa la marca de actividad cinco minutos con un `UPDATE` directo y se relee de la base el
    valor de referencia; despues de una peticion con la credencial vigente, la fila releida DESDE LA
    BASE debe traer una marca estrictamente posterior. El oraculo mira el `status_code` solo para
    confirmar que el guardia ACEPTO la sesion (no es 401): que la ruta protegida todavia no tenga
    vista y la peticion acabe en 404 es irrelevante aqui, porque el refresco ocurre en la
    autenticacion, antes del enrutado a la vista. La sesion sigue sin revocar: refrescar actividad
    no caduca ni sella nada.
    """

    SesionUsuarioEntity.objects.filter(pk=sesion_vigente.session_id).update(
        last_activity_at=utc_now() - timedelta(minutes=5)
    )
    fila_previa = _leer_fila_de_sesion(sesion_vigente.session_id)
    assert fila_previa is not None, "La sesion de la fixture no esta en `sesion_usuario`"
    actividad_de_referencia = fila_previa[5]

    respuesta = cliente_api.get(RUTA_PROTEGIDA, **cabecera_bearer(sesion_vigente.session_id))

    assert respuesta.status_code != 401, (
        f"El guardia ha rechazado una sesion vigente: {respuesta.status_code} {respuesta.content!r}"
    )

    fila_posterior = _leer_fila_de_sesion(sesion_vigente.session_id)
    assert fila_posterior is not None, "La sesion ha desaparecido de `sesion_usuario` tras la peticion"
    actividad_refrescada = fila_posterior[5]

    assert actividad_refrescada > actividad_de_referencia, (
        f"`last_activity_at` no se ha refrescado en la base: {actividad_refrescada} <= {actividad_de_referencia}"
    )
    assert fila_posterior[6] is None, "Una peticion aceptada no puede dejar la sesion revocada"


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_EP_001_el_login_emite_una_sesion_real_y_su_credencial_abre_la_api(
    cliente_api: APIClient, usuario_activo: UsuarioEntity
) -> None:
    """
    [EP-001][REQ-056] Recorrido completo de la unica ruta publica contra el Oracle REAL.

    Inicio de sesion con la credencial autentica del usuario sembrado (hash Argon2id de verdad, sin
    mockear hashers ni `ServicioSesiones`), contrato de salida en camelCase, fila efectivamente
    escrita en `sesion_usuario` y, sobre todo, el cierre del circuito: la credencial recien emitida
    ABRE el resto de la API (la peticion protegida ya no es 401). Se comprueba ademas que el cuerpo
    no filtra material secreto (REQ-063) y que una contrasenia incorrecta responde el 401 uniforme
    de credenciales, sin revelar si la cuenta existe.
    """

    url_login = reverse("core_security:auth-sessions")

    respuesta = cliente_api.post(url_login, {"username": usuario_activo.username, "password": CLAVE_DE_PRUEBA}, format="json")

    assert respuesta.status_code == 201, (
        f"EP-001 debe responder 201 al emitir la sesion, no {respuesta.status_code}: {respuesta.content!r}"
    )

    cuerpo = respuesta.json()
    for clave in ("sessionId", "userId", "roleCode", "issuedAt", "expiresAt", "lastActivityAt"):
        assert clave in cuerpo, f"El contrato de `SessionDetail` no publica `{clave}`: {sorted(cuerpo)}"
    assert cuerpo["userId"] == usuario_activo.user_id, "La sesion emitida no pertenece al usuario que inicio sesion"

    serializado = json.dumps(cuerpo, ensure_ascii=False)
    for clave_secreta in CLAVES_SECRETAS_PROHIBIDAS:
        assert clave_secreta not in cuerpo, f"El cuerpo de EP-001 publica la clave secreta `{clave_secreta}`"
        assert clave_secreta not in serializado, f"El cuerpo de EP-001 filtra `{clave_secreta}`: {serializado}"

    fila = _leer_fila_de_sesion(cuerpo["sessionId"])
    assert fila is not None, "EP-001 ha devuelto un `sessionId` que no existe como fila en `sesion_usuario`"
    assert fila[1] == usuario_activo.user_id, "La fila persistida en Oracle no apunta al usuario que inicio sesion"

    protegida = cliente_api.get(RUTA_PROTEGIDA, **cabecera_bearer(cuerpo["sessionId"]))
    assert protegida.status_code != 401, (
        f"La credencial recien emitida no abre la API: {protegida.status_code} {protegida.content!r}"
    )

    fallida = cliente_api.post(
        url_login, {"username": usuario_activo.username, "password": "Clave-incorrecta-2026"}, format="json"
    )
    assert fallida.status_code == 401, f"Una contrasenia incorrecta debe responder 401, no {fallida.status_code}"
    assert fallida.json()["code"] == "AUTH_BAD_CREDENTIALS", (
        f"El codigo del fallo de credenciales no es el del contrato: {fallida.json()}"
    )
