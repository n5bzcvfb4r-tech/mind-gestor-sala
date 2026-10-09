"""
Pruebas del inicio de sesion (EP-001, `POST /api/auth/sessions`) contra el Oracle 23ai Free REAL.

Ejercitan el servicio de autenticacion endurecido de `apps.identidad.autenticacion` a traves de
la frontera HTTP del contrato, con filas de verdad en las tablas `usuario` y `sesion_usuario`
del esquema T.5 aplicado por Liquibase. El oraculo de persistencia es un SELECT crudo contra la
base: un objeto del ORM en memoria no acredita que la sesion se haya escrito.

Si no hay engine Docker alcanzable las pruebas se SALTAN (`SALTAR_SIN_DOCKER`); nunca se
degradan a H2, SQLite ni a ningun doble en memoria.
"""

import json
import statistics
import time
from uuid import uuid4

import pytest
from django.db import connections
from rest_framework.test import APIClient

from apps.core.contexto import utc_now
from apps.core.models import MotivoDesactivacionEntity, UsuarioEntity
from apps.core_security import mensajes
from apps.identidad.tests.conftest import CLAVE_DE_PRUEBA, SALTAR_SIN_DOCKER, contexto_de_sesion_del_usuario

pytestmark = [pytest.mark.integration]

#: Ruta LITERAL del contrato para EP-001 (`servers[0].url` = `/api` mas el path `auth/sessions`).
RUTA_LOGIN = "/api/auth/sessions"

#: Codigo estable del 401 de credenciales: el MISMO para los tres casos de fallo (AC-AUT-02).
CODIGO_CREDENCIALES_INVALIDAS = "AUTH_BAD_CREDENTIALS"

#: Claves de material secreto que el cuerpo de EP-001 no puede publicar jamas (REQ-063).
CLAVES_SECRETAS_PROHIBIDAS = ("password", "passwordHash", "password_hash", "passwordSalt", "password_salt")

#: Terminos que delatarian el motivo real del fallo si asomasen en el cuerpo del 401 (AC-AUT-02).
TERMINOS_QUE_DELATARIAN_EL_MOTIVO = ("inexistente", "inactivo", "INACTIVO", "contrasenia", "password")

#: SELECT crudo contra Oracle: el oraculo de persistencia lee la FILA, no la instancia del ORM.
SQL_SESION_POR_ID = (
    "SELECT session_id, user_id, role_code, issued_at, expires_at, last_activity_at, revoked_at "
    "FROM sesion_usuario WHERE session_id = %s"
)

#: Recuento fisico de sesiones: un fallo de credenciales no puede dejar ninguna fila nueva.
SQL_CONTAR_SESIONES = "SELECT COUNT(*) FROM sesion_usuario"

#: Umbral CUANTITATIVO del criterio de aceptacion: diferencia de latencia MEDIA entre casos (AC-ACC-02).
UMBRAL_DIFERENCIA_MEDIA_MS = 50.0

#: Intentos por caso. Son TRES casos indistinguibles, asi que 34 x 3 = 102 >= los 100 intentos del criterio.
INTENTOS_POR_CASO = 34

#: Contrasenia que NO es la del usuario sembrado: el camino "existe pero la credencial falla".
CLAVE_INCORRECTA = "Esta-no-es-la-clave-2026"

#: Correo que no corresponde a ninguna fila de `usuario`: el camino "la cuenta no existe".
CORREO_INEXISTENTE = "no.existe.jamas@mind.local"


def _leer_fila_de_sesion(session_id: str) -> tuple | None:
    """Lee la fila fisica de `sesion_usuario` con SQL crudo, sin pasar por el ORM ni por sus caches."""

    with connections["default"].cursor() as cursor:
        cursor.execute(SQL_SESION_POR_ID, [session_id])
        return cursor.fetchone()


def _contar_sesiones() -> int:
    """Numero de filas de `sesion_usuario` leido de la base, no del ORM."""

    with connections["default"].cursor() as cursor:
        cursor.execute(SQL_CONTAR_SESIONES)
        return int(cursor.fetchone()[0])


def _actor_de_la_baja(usuario: UsuarioEntity) -> UsuarioEntity:
    """
    Da de alta una cuenta administradora que pueda figurar como `deactivated_by`.

    El alta va por `bulk_create` -que NO pasa por `save()` ni por el `AtribucionMixin`- porque la
    CHECK `ck_usuario_created_by_self` prohibe que un usuario sea su propio `created_by` y porque
    el `user_id` lo genera la IDENTITY de Oracle. Hace falta una cuenta DISTINTA del usuario que
    se da de baja: `ck_usuario_deactivated_by_self` prohibe que el actor de la baja sea el propio
    desactivado.
    """

    sufijo = uuid4().hex[:8]
    correo = f"baja.actor.{sufijo}@mind.local"
    UsuarioEntity.objects.bulk_create(
        [
            UsuarioEntity(
                full_name=f"Actor de la baja {sufijo}",
                corporate_email=correo,
                username=f"baja.actor.{sufijo}",
                role_code=usuario.role_code,
                status="ACTIVO",
                password_hash=usuario.password_hash,
                password_algorithm="argon2id",
                must_change_password="N",
                failed_password_attempts=0,
                created_at=utc_now(),
            )
        ]
    )
    return UsuarioEntity.objects.get(corporate_email=correo)


def _desactivar(usuario: UsuarioEntity) -> UsuarioEntity:
    """
    Deja al usuario con la cuenta DESACTIVADA (`status = "INACTIVO"`, el `is_active = false` del requisito).

    La baja logica NO es solo cambiar `status`: la CHECK `ck_usuario_coherencia_baja` del DDL obliga
    a informar a la vez el cuando, el quien y el porque, y quien lo rechaza es ORACLE, no la
    aplicacion. El `save()` va dentro del contexto de sesion del actor de la baja porque el
    `AtribucionMixin` exige identidad efectiva para sellar la atribucion (REQ-064).
    """

    actor = _actor_de_la_baja(usuario)
    motivo = MotivoDesactivacionEntity.objects.filter(is_active="Y").first()
    assert motivo is not None, "cat_motivo_desactivacion no tiene ningun motivo activo sembrado"

    usuario.status = "INACTIVO"
    usuario.deactivated_at = utc_now()
    usuario.deactivated_by = actor
    usuario.deactivation_reason_code = motivo
    with contexto_de_sesion_del_usuario(actor):
        usuario.save()
    return usuario


def _intentar_login(cliente_api: APIClient, username: str, password: str):
    """Envia un intento de inicio de sesion a la ruta literal del contrato."""

    return cliente_api.post(RUTA_LOGIN, {"username": username, "password": password}, format="json")


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_AUT_01_credenciales_validas_emiten_sesion(cliente_api: APIClient, usuario_activo: UsuarioEntity) -> None:
    """
    [AC-AUT-01] Usuario ACTIVO con credenciales correctas: EP-001 emite sesion y la persiste en Oracle.

    El estado esperado es 201 y NO 200: lo fija el contrato (`openapi.yaml`, EP-001 declara `'201'`)
    y el contrato es LEY. El DoD de la tarea menciona "200" de forma inexacta; manda el contrato,
    que es lo que firma el consumidor del API.
    """

    respuesta = _intentar_login(cliente_api, usuario_activo.corporate_email, CLAVE_DE_PRUEBA)

    assert respuesta.status_code == 201, f"EP-001 crea un recurso: el contrato declara 201, no {respuesta.status_code}"
    cuerpo = respuesta.json()

    for clave in ("sessionId", "userId", "roleCode", "issuedAt", "expiresAt"):
        assert clave in cuerpo, f"El cuerpo de EP-001 debe publicar `{clave}`; llego: {sorted(cuerpo)}"

    assert cuerpo["userId"] == usuario_activo.user_id
    assert cuerpo["roleCode"] == usuario_activo.role_code_id
    # Vencimiento ABSOLUTO posterior a la emision (REQ-056); las marcas ISO-8601 en UTC ordenan como texto.
    assert cuerpo["expiresAt"] > cuerpo["issuedAt"], (
        f"`expiresAt` ({cuerpo['expiresAt']}) debe ser posterior a `issuedAt` ({cuerpo['issuedAt']})"
    )

    filtradas = [clave for clave in CLAVES_SECRETAS_PROHIBIDAS if clave in cuerpo]
    assert not filtradas, f"El cuerpo de EP-001 no puede publicar material secreto (REQ-063): {filtradas}"

    # ORACULO DE PERSISTENCIA: la fila se lee de `sesion_usuario` con SQL crudo, no del ORM.
    fila = _leer_fila_de_sesion(cuerpo["sessionId"])
    assert fila is not None, f"No hay fila en `sesion_usuario` con session_id={cuerpo['sessionId']}"
    session_id, user_id, role_code, issued_at, expires_at, _ultima_actividad, revoked_at = fila
    assert session_id == cuerpo["sessionId"]
    assert user_id == usuario_activo.user_id
    assert role_code == usuario_activo.role_code_id
    assert issued_at is not None and expires_at is not None
    assert expires_at > issued_at, "La fila persistida debe tener vencimiento absoluto posterior a la emision"
    assert revoked_at is None, "La sesion recien emitida no puede nacer revocada"

    # El `username` se compara con TRIM y SIN distinguir mayusculas (REQ-053, REQ-045).
    respuesta_normalizada = _intentar_login(
        cliente_api, f"  {usuario_activo.corporate_email.upper()}  ", CLAVE_DE_PRUEBA
    )
    assert respuesta_normalizada.status_code == 201, (
        "El correo en MAYUSCULAS y rodeado de espacios identifica al MISMO usuario (REQ-045): "
        f"se esperaba 201 y llego {respuesta_normalizada.status_code}"
    )


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_AUT_02_los_tres_fallos_son_indistinguibles_en_cuerpo_y_codigo(
    cliente_api: APIClient, usuario_activo: UsuarioEntity
) -> None:
    """
    [AC-AUT-02, AC-ACC-02] Contrasenia incorrecta, usuario inexistente y cuenta desactivada: el MISMO 401.

    Si el cuerpo variase entre los tres casos, bastaria con probar un correo para saber si la
    cuenta existe o si esta de baja, y la API seria un oraculo de enumeracion de cuentas.
    """

    sesiones_antes = _contar_sesiones()

    respuesta_clave_mala = _intentar_login(cliente_api, usuario_activo.corporate_email, CLAVE_INCORRECTA)
    respuesta_inexistente = _intentar_login(cliente_api, CORREO_INEXISTENTE, CLAVE_DE_PRUEBA)

    # Tercer caso: la cuenta EXISTE y la credencial es CORRECTA, pero esta desactivada (`is_active = false`).
    _desactivar(usuario_activo)
    respuesta_inactivo = _intentar_login(cliente_api, usuario_activo.corporate_email, CLAVE_DE_PRUEBA)

    respuestas = {
        "contrasenia_incorrecta": respuesta_clave_mala,
        "usuario_inexistente": respuesta_inexistente,
        "usuario_desactivado": respuesta_inactivo,
    }

    for caso, respuesta in respuestas.items():
        assert respuesta.status_code == 401, f"El caso `{caso}` debe denegar con 401 y llego {respuesta.status_code}"
        cuerpo = respuesta.json()
        assert cuerpo["code"] == CODIGO_CREDENCIALES_INVALIDAS, f"`code` distinto en el caso `{caso}`: {cuerpo['code']}"
        assert cuerpo["message"] == mensajes.CREDENCIALES_INVALIDAS, f"`message` distinto en el caso `{caso}`"
        assert cuerpo["message"] == "Usuario o contraseña incorrectos", "El literal del 401 lo fija el requisito"

    # Los cuerpos deben ser IDENTICOS salvo el `traceId`, que es distinto por diseno (correlador por peticion).
    cuerpos_sin_traza = {caso: {k: v for k, v in r.json().items() if k != "traceId"} for caso, r in respuestas.items()}
    distintos = {caso: cuerpo for caso, cuerpo in cuerpos_sin_traza.items() if cuerpo != cuerpos_sin_traza["contrasenia_incorrecta"]}
    assert not distintos, f"Los tres fallos deben devolver el MISMO cuerpo salvo el `traceId`; difieren: {distintos}"

    trazas = {r.json()["traceId"] for r in respuestas.values()}
    assert len(trazas) == 3, "Cada peticion lleva su propio `traceId`: es el correlador de la traza interna"

    assert _contar_sesiones() == sesiones_antes, "Un intento denegado NO puede emitir ninguna sesion en `sesion_usuario`"

    for caso, respuesta in respuestas.items():
        serializado = json.dumps(respuesta.json(), ensure_ascii=False)
        delatores = [termino for termino in TERMINOS_QUE_DELATARIAN_EL_MOTIVO if termino in serializado]
        assert not delatores, f"El cuerpo del caso `{caso}` filtra el motivo real del fallo: {delatores}"


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_ACC_02_la_latencia_media_de_los_tres_fallos_no_los_distingue(
    cliente_api: APIClient, usuario_activo: UsuarioEntity
) -> None:
    """
    [AC-AUT-02, AC-ACC-02] La latencia media de los tres fallos no los distingue: diferencia < 50 ms.

    POR QUE IMPORTA: un mensaje identico sigue siendo un oraculo si el TIEMPO delata el caso.
    Verificar un hash Argon2id cuesta deliberadamente decenas de milisegundos, de modo que un
    camino que saliese antes -el usuario no existe, o esta inactivo- respondería en una fraccion
    de ese tiempo y permitiria enumerar cuentas aunque el cuerpo sea palabra por palabra el mismo.
    `apps.identidad.autenticacion.ServicioAutenticacion` lo evita verificando SIEMPRE un hash, con
    un senuelo del mismo coste cuando no hay usuario.

    El umbral de 50 ms y los 100 intentos son del criterio de aceptacion: si la medida resultase
    inestable por ruido de la maquina, se estabiliza el entorno de medida, NUNCA se baja el liston.
    """

    def _medir(username: str, password: str) -> list[float]:
        """Latencias en milisegundos de `INTENTOS_POR_CASO` intentos denegados."""

        muestras: list[float] = []
        for _ in range(INTENTOS_POR_CASO):
            inicio = time.perf_counter()
            respuesta = _intentar_login(cliente_api, username, password)
            muestras.append((time.perf_counter() - inicio) * 1000.0)
            assert respuesta.status_code == 401, "La medida de latencia solo vale sobre intentos DENEGADOS"
        return muestras

    latencias_clave_mala = _medir(usuario_activo.corporate_email, CLAVE_INCORRECTA)
    latencias_inexistente = _medir(CORREO_INEXISTENTE, CLAVE_DE_PRUEBA)

    # El tercer caso se mide al final: una vez desactivada, la cuenta ya no sirve para el caso 1.
    _desactivar(usuario_activo)
    latencias_inactivo = _medir(usuario_activo.corporate_email, CLAVE_DE_PRUEBA)

    total_de_intentos = len(latencias_clave_mala) + len(latencias_inexistente) + len(latencias_inactivo)
    assert total_de_intentos >= 100, f"El criterio exige 100 intentos como minimo y se han hecho {total_de_intentos}"

    medias = {
        "contrasenia_incorrecta": statistics.mean(latencias_clave_mala),
        "usuario_inexistente": statistics.mean(latencias_inexistente),
        "usuario_desactivado": statistics.mean(latencias_inactivo),
    }
    diferencia = max(medias.values()) - min(medias.values())

    assert diferencia < UMBRAL_DIFERENCIA_MEDIA_MS, (
        f"La diferencia de latencia media entre los tres fallos debe ser inferior a {UMBRAL_DIFERENCIA_MEDIA_MS} ms "
        f"sobre {total_de_intentos} intentos (AC-ACC-02); medido {diferencia:.2f} ms con medias {medias}"
    )
