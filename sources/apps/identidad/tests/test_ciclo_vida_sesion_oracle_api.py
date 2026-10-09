"""
Pruebas del ciclo de vida de la sesion de `apps.identidad` contra el Oracle 23ai REAL del proyecto.

Cubren el cierre explicito (EP-002, AC-SES-03), la reemision tras el cierre (AC-SES-03), la
expiracion por INACTIVIDAD (AC-SES-02) y la expiracion por VENCIMIENTO ABSOLUTO (AC-SES-04),
siempre sobre la tabla fisica `sesion_usuario` del esquema que gobierna Liquibase.

El oraculo de persistencia es un `SELECT` CRUDO por el driver `python-oracledb`, nunca la
instancia del ORM ni una cache del servicio: un `dict` en memoria no habria superado un SELECT
contra una tabla que no existiria detras de el. Si no hay engine Docker alcanzable las pruebas
se SALTAN (`SALTAR_SIN_DOCKER`); jamas se degradan a H2, SQLite ni a ningun doble en memoria.

Ninguna prueba parchea la aplicacion: la vigencia se manipula escribiendo en las COLUMNAS de la
sesion (que es lo que el producto lee), no sustituyendo settings ni metodos del servicio.
"""

from datetime import timedelta

import pytest
from django.db import connections
from rest_framework.test import APIClient

from apps.core.contexto import utc_now
from apps.core.models import SesionUsuarioEntity, UsuarioEntity
from apps.core_security import mensajes
from apps.identidad.sesiones import (
    MOTIVO_REVOCACION_CADUCIDAD,
    MOTIVO_REVOCACION_LOGOUT,
    ServicioCicloVidaSesion,
)
from apps.identidad.tests.conftest import CLAVE_DE_PRUEBA, SALTAR_SIN_DOCKER, cabecera_bearer

pytestmark = [pytest.mark.integration]

#: Rutas LITERALES del contrato (`openapi.yaml`), sin barra final: EP-001 y EP-002/EP-003.
RUTA_SESIONES = "/api/auth/sessions"
RUTA_SESION_ACTUAL = "/api/auth/sessions/current"

#: Codigo estable del 401 de sesion, el MISMO para cualquier modo de fallo (AC-SES-04).
CODIGO_SESION_INVALIDA = "AUTH_SESSION_INVALID"

#: SELECT crudo contra Oracle: el oraculo lee la FILA fisica, no la instancia del ORM.
SQL_SESION_POR_ID = (
    "SELECT session_id, expires_at, last_activity_at, revoked_at, revocation_reason "
    "FROM sesion_usuario WHERE session_id = %s"
)

# Posiciones de las columnas devueltas por `SQL_SESION_POR_ID`.
COL_EXPIRES_AT = 1
COL_LAST_ACTIVITY_AT = 2
COL_REVOKED_AT = 3
COL_REVOCATION_REASON = 4


def _leer_fila_de_sesion(session_id: str) -> tuple | None:
    """Lee la fila fisica de `sesion_usuario` con SQL crudo, sin pasar por el ORM ni por sus caches."""

    with connections["default"].cursor() as cursor:
        cursor.execute(SQL_SESION_POR_ID, [session_id])
        return cursor.fetchone()


def _afirmar_401_uniforme(respuesta, escenario: str) -> dict:
    """Comprueba el 401 uniforme de sesion (codigo y mensaje del contrato) y devuelve el cuerpo."""

    assert respuesta.status_code == 401, (
        f"El escenario `{escenario}` debe responder 401, no {respuesta.status_code}: {respuesta.content!r}"
    )
    cuerpo = respuesta.json()
    assert cuerpo["code"] == CODIGO_SESION_INVALIDA, f"Codigo inesperado en `{escenario}`: {cuerpo}"
    assert cuerpo["message"] == mensajes.SESION_REQUERIDA, f"Mensaje no uniforme en `{escenario}`: {cuerpo}"
    return cuerpo


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_SES_03_el_cierre_de_sesion_revoca_en_servidor_y_es_idempotente(
    db, cliente_api: APIClient, sesion_vigente: SesionUsuarioEntity
) -> None:
    """
    [AC-SES-03] El cierre explicito (EP-002) revoca la sesion EN SERVIDOR y repetirlo es inocuo.

    No basta con que el cliente olvide la credencial: la fila de `sesion_usuario` debe quedar
    sellada con `revoked_at` y `revocation_reason = "logout"` (REQ-057), seguir EXISTIENDO como
    evidencia del acceso (REQ-047, REQ-070) y dejar de abrir la API aunque su vencimiento
    absoluto siga en el futuro.
    """

    respuesta = cliente_api.delete(RUTA_SESION_ACTUAL, **cabecera_bearer(sesion_vigente.session_id))

    assert respuesta.status_code == 204, (
        f"EP-002 debe responder 204 al cerrar la sesion, no {respuesta.status_code}: {respuesta.content!r}"
    )
    assert respuesta.content == b"", f"El 204 de EP-002 no puede llevar cuerpo: {respuesta.content!r}"

    fila = _leer_fila_de_sesion(sesion_vigente.session_id)
    assert fila is not None, (
        "La sesion cerrada se ha BORRADO de `sesion_usuario`: debe revocarse y permanecer (REQ-047, REQ-070)"
    )
    assert fila[COL_REVOKED_AT] is not None, "El cierre no ha sellado `revoked_at` en la fila de Oracle"
    assert fila[COL_REVOCATION_REASON] == MOTIVO_REVOCACION_LOGOUT, (
        f"El motivo persistido debe ser `logout`, no `{fila[COL_REVOCATION_REASON]}`"
    )

    # La credencial deja de valer AUNQUE no haya vencido: el vencimiento absoluto sigue en el futuro.
    assert fila[COL_EXPIRES_AT] > utc_now(), (
        f"La sesion ya habia vencido por si sola ({fila[COL_EXPIRES_AT]}): el caso no probaria la revocacion"
    )

    posterior = cliente_api.get(RUTA_SESION_ACTUAL, **cabecera_bearer(sesion_vigente.session_id))
    _afirmar_401_uniforme(posterior, "credencial revocada")

    # IDEMPOTENCIA: se repite el cierre sobre el SERVICIO, no por HTTP. Por HTTP la credencial ya
    # esta revocada y el guardia la rechaza con 401 ANTES de llegar a la vista, asi que la llamada
    # HTTP no llegaria a ejercitar la segunda revocacion; la idempotencia que exige AC-SES-03 es la
    # del ciclo de vida: repetir el cierre no lanza y no pisa la marca de revocacion original.
    revocacion_original = fila[COL_REVOKED_AT]
    ServicioCicloVidaSesion().cerrar(sesion_vigente.session_id)

    fila_final = _leer_fila_de_sesion(sesion_vigente.session_id)
    assert fila_final is not None, "El segundo cierre ha hecho desaparecer la fila de `sesion_usuario`"
    assert fila_final[COL_REVOKED_AT] == revocacion_original, (
        f"El segundo cierre ha pisado la marca original: {fila_final[COL_REVOKED_AT]} != {revocacion_original}"
    )
    assert fila_final[COL_REVOCATION_REASON] == MOTIVO_REVOCACION_LOGOUT, (
        f"El segundo cierre ha cambiado el motivo persistido: `{fila_final[COL_REVOCATION_REASON]}`"
    )


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_SES_03_un_nuevo_inicio_de_sesion_emite_un_session_id_distinto(
    db, cliente_api: APIClient, usuario_activo: UsuarioEntity, sesion_vigente: SesionUsuarioEntity
) -> None:
    """
    [AC-SES-03] Tras cerrar sesion, volver a identificarse emite una sesion NUEVA y distinta.

    La credencial cerrada no revive ni se reutiliza: EP-001 crea otra fila con otro `session_id`
    sin revocar, mientras la anterior sigue sellada con `logout` en `sesion_usuario`.
    """

    cierre = cliente_api.delete(RUTA_SESION_ACTUAL, **cabecera_bearer(sesion_vigente.session_id))
    assert cierre.status_code == 204, f"EP-002 no ha cerrado la sesion de partida: {cierre.content!r}"

    reinicio = cliente_api.post(
        RUTA_SESIONES,
        {"username": usuario_activo.corporate_email, "password": CLAVE_DE_PRUEBA},
        format="json",
    )

    assert reinicio.status_code == 201, (
        f"EP-001 debe responder 201 al emitir la sesion, no {reinicio.status_code}: {reinicio.content!r}"
    )
    nueva_credencial = reinicio.json()["sessionId"]
    assert nueva_credencial != sesion_vigente.session_id, (
        "El nuevo inicio de sesion ha reutilizado el `session_id` cerrado: la credencial debe ser otra"
    )

    fila_nueva = _leer_fila_de_sesion(nueva_credencial)
    assert fila_nueva is not None, "EP-001 ha devuelto un `sessionId` que no existe como fila en `sesion_usuario`"
    assert fila_nueva[COL_REVOKED_AT] is None, "La sesion recien emitida no puede nacer revocada"

    fila_anterior = _leer_fila_de_sesion(sesion_vigente.session_id)
    assert fila_anterior is not None, "La sesion cerrada ha desaparecido de `sesion_usuario` (REQ-047, REQ-070)"
    assert fila_anterior[COL_REVOKED_AT] is not None, "La sesion anterior ha dejado de estar revocada tras el nuevo login"
    assert fila_anterior[COL_REVOCATION_REASON] == MOTIVO_REVOCACION_LOGOUT, (
        f"El motivo de la sesion anterior debe seguir siendo `logout`, no `{fila_anterior[COL_REVOCATION_REASON]}`"
    )


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_SES_02_la_sesion_caducada_por_inactividad_responde_401_uniforme(
    db, cliente_api: APIClient, sesion_vigente: SesionUsuarioEntity
) -> None:
    """
    [AC-SES-02] Superada la ventana de inactividad, la sesion deja de valer y queda sellada.

    El desfase se calcula con `ServicioCicloVidaSesion.inactividad_minutos`, que es parametro de
    entorno y no se codifica a mano. El vencimiento ABSOLUTO se deja en el futuro a proposito:
    asi lo unico que puede caducar la sesion es la inactividad.
    """

    servicio = ServicioCicloVidaSesion()
    sesion_vigente.last_activity_at = utc_now() - timedelta(minutes=servicio.inactividad_minutos + 1)
    sesion_vigente.save(update_fields=["last_activity_at"])

    assert sesion_vigente.expires_at > utc_now(), (
        f"El vencimiento absoluto ya habia pasado ({sesion_vigente.expires_at}): el caso no aislaria la inactividad"
    )

    respuesta = cliente_api.get(RUTA_SESION_ACTUAL, **cabecera_bearer(sesion_vigente.session_id))

    _afirmar_401_uniforme(respuesta, "sesion inactiva")

    fila = _leer_fila_de_sesion(sesion_vigente.session_id)
    assert fila is not None, "La sesion caducada por inactividad se ha borrado en vez de revocarse (REQ-047, REQ-070)"
    assert fila[COL_REVOKED_AT] is not None, "La inactividad no ha sellado `revoked_at` en la fila de Oracle"
    assert fila[COL_REVOCATION_REASON] == MOTIVO_REVOCACION_CADUCIDAD, (
        f"El motivo persistido debe ser `expired`, no `{fila[COL_REVOCATION_REASON]}`"
    )


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_SES_04_la_sesion_vencida_por_limite_absoluto_responde_401_uniforme(
    db, cliente_api: APIClient, sesion_vigente: SesionUsuarioEntity
) -> None:
    """
    [AC-SES-04] Vencido el limite absoluto, la sesion se deniega aunque la actividad sea reciente.

    La marca de actividad se pone en el instante actual para aislar que lo que vence es el limite
    absoluto. La denegacion debe ser INDISTINGUIBLE de la de una peticion sin credencial: si el
    cuerpo variase, la API seria un oraculo para saber que esa sesion existio.
    """

    ahora = utc_now()
    sesion_vigente.expires_at = ahora - timedelta(minutes=1)
    sesion_vigente.last_activity_at = ahora
    sesion_vigente.save(update_fields=["expires_at", "last_activity_at"])

    respuesta = cliente_api.get(RUTA_SESION_ACTUAL, **cabecera_bearer(sesion_vigente.session_id))
    cuerpo = _afirmar_401_uniforme(respuesta, "sesion vencida")

    sin_credencial = cliente_api.get(RUTA_SESION_ACTUAL)
    cuerpo_sin_credencial = _afirmar_401_uniforme(sin_credencial, "sin credencial")

    # El `traceId` SI cambia: es el correlador de cada peticion, no parte del mensaje uniforme.
    assert {clave: valor for clave, valor in cuerpo.items() if clave != "traceId"} == {
        clave: valor for clave, valor in cuerpo_sin_credencial.items() if clave != "traceId"
    }, f"La denegacion de la sesion vencida es distinguible de la de una peticion sin credencial: {cuerpo}"

    fila = _leer_fila_de_sesion(sesion_vigente.session_id)
    assert fila is not None, "La sesion vencida se ha borrado en vez de revocarse (REQ-047, REQ-070)"
    assert fila[COL_REVOKED_AT] is not None, "El vencimiento absoluto no ha sellado `revoked_at` en Oracle"
    assert fila[COL_REVOCATION_REASON] == MOTIVO_REVOCACION_CADUCIDAD, (
        f"El motivo persistido debe ser `expired`, no `{fila[COL_REVOCATION_REASON]}`"
    )
