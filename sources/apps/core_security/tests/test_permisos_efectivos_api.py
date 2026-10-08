"""
Pruebas del recurso de PERMISOS EFECTIVOS (EP-004, `GET /api/auth/permissions`, REQ-020).

El motor de prueba de las dos pruebas que tocan datos es el Oracle Database 23ai Free REAL del
proyecto (`gvenzl/oracle-free:23-slim`), con el esquema y las semillas aplicados por Liquibase
sobre el changelog `sources/facilities/master.xml`. Esta PROHIBIDO sustituirlo por H2, SQLite o
cualquier doble en memoria: el ORACULO de este endpoint es la matriz `permiso_rol_operacion`
que siembra el changelog dml, y contra un `dict` en memoria un verde solo acreditaria que el
test se sabe de memoria lo que el test mismo ha escrito, no que la API publique lo que dice la
base. Si no hay engine Docker alcanzable, esas pruebas se SALTAN (`SALTAR_SIN_DOCKER`); nunca
se degradan a otro backend.

La cadena de fixtures de contenedor (`red_docker` -> `oracle_contenedor` -> `esquema_aplicado`
-> `django_db_setup`) y las semillas (`usuario_activo`, `sesion_vigente`, `cliente_api`) NO se
duplican aqui: se REUTILIZAN del conftest del paquete. El helper `sembrar_usuario` tampoco se
copia: se IMPORTA de `test_contexto_autorizacion_oracle`, para que la forma de dar de alta una
cuenta real viva en un unico sitio.

La tercera prueba (AC-SES-04) NO toca base de datos a proposito: el 401 por credencial ausente
se resuelve en el guardia de sesion ANTES de consultar Oracle, de modo que la superficie HTTP
protegida de EP-004 queda cubierta tambien cuando no hay contenedor.
"""

import pytest
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.models import PermisoRolOperacionEntity
from apps.core.models.transaccional import UsuarioEntity
from apps.core_security import mensajes
from apps.core_security.rutas_publicas import RUTAS_PUBLICAS, es_ruta_exenta
from apps.core_security.servicios.sesiones import ServicioSesiones
from apps.core_security.tests.conftest import SALTAR_SIN_DOCKER, cabecera_bearer
from apps.core_security.tests.test_contexto_autorizacion_oracle import sembrar_usuario


pytestmark = [pytest.mark.integration]


#: Ruta del contrato de EP-004: base `servers[0].url` (`/api`) del `openapi.yaml` mas el path.
RUTA_PERMISOS = "/api/auth/permissions"

#: Codigo canonico del 401 uniforme de sesion (AC-SES-04).
CODIGO_SESION_INVALIDA = "AUTH_SESSION_INVALID"

# LITERALES DE CATALOGO. Estos `role_code` y `operation_code` NO son datos que esta suite
# inserte: son los que siembra Liquibase en `cat_rol`, `cat_operacion` y
# `permiso_rol_operacion`. Aqui son unicamente lo que la prueba BUSCA en la base; si el
# changelog dml no los trajese, la prueba falla a proposito, porque eso seria un defecto real
# de las semillas y no algo que taparse con un valor por defecto.
ROL_EMPLEADO = "EMPLEADO"
ROL_TECNICO = "TECNICO_MANTENIMIENTO"

#: Lo que la matriz ACORDADA concede al EMPLEADO: cuatro operaciones, todas de alcance OWN.
OPERACIONES_DEL_EMPLEADO = frozenset({"INCIDENT_CREATE", "INCIDENT_LIST_OWN", "INCIDENT_VIEW", "INCIDENT_HISTORY_VIEW"})

#: Acciones «Asignarme», «Cambiar estado» y «Cerrar» de la pantalla del TECNICO_MANTENIMIENTO.
#: El renderizado es de la SPA y queda fuera de este backend; su equivalente verificable aqui
#: es que estos codigos NO figuren entre las capacidades que se le publican al EMPLEADO.
ACCIONES_DEL_TECNICO = ("INCIDENT_ASSIGN_SELF", "INCIDENT_STATUS_CHANGE", "INCIDENT_CLOSE_WITH_COMMENT")

#: Capacidades de OTROS roles que tampoco pueden asomar en la respuesta del EMPLEADO.
OPERACIONES_AJENAS = ("INCIDENT_LIST_ALL", "USER_MANAGE")

#: Claves EXACTAS del esquema `EffectivePermissions` (camelCase). Ni una mas: ni identificador
#: de usuario ni la matriz del sistema.
CLAVES_DEL_CONTRATO = {"roleCode", "allowedOperations", "dataScope"}


@pytest.fixture
def cliente() -> Client:
    """Cliente HTTP de Django sin credencial de sesion: toda peticion sale anonima."""

    return Client()


@SALTAR_SIN_DOCKER
def test_AC_ROL_04_el_empleado_recibe_solo_sus_capacidades_y_nunca_la_matriz_completa(db, cliente_api: APIClient) -> None:
    """
    [AC-ROL-04] Un EMPLEADO autenticado carga su contexto de permisos y recibe `roleCode`,
    `allowedOperations` y `dataScope = OWN` del rol de SU sesion, sin permisos de terceros y sin
    la matriz completa del sistema (REQ-020).

    La parte de "no se renderizan los controles «Asignarme», «Cambiar estado» ni «Cerrar»" es de
    la SPA Angular y no la decide este backend; lo que SI decide, y lo unico verificable aqui,
    es que esos tres codigos de operacion no viajen en `allowedOperations`: si la API no los
    publica, la pantalla no tiene con que pintar los botones.

    Se siembra ademas un TECNICO_MANTENIMIENTO con sesion propia como TERCERO de control: sus
    capacidades existen en la misma base y aun asi no deben asomar en la respuesta del EMPLEADO.
    """

    # --- Arrange: dos usuarios REALES con sesion REAL emitida por el codigo productivo ---
    empleado = sembrar_usuario(ROL_EMPLEADO, "perm-empleado")
    tecnico = sembrar_usuario(ROL_TECNICO, "perm-tecnico")
    assert empleado.user_id != tecnico.user_id, "el tercero debe ser otra fila real de la base"

    sesion_empleado = ServicioSesiones().emitir(empleado)
    # El tercero tiene su propia sesion vigente: asi se acredita que lo que acota la respuesta es
    # el rol de la sesion que consulta, y no que el otro usuario "no exista todavia".
    sesion_tecnico = ServicioSesiones().emitir(tecnico)
    assert sesion_tecnico.session_id != sesion_empleado.session_id

    # El montaje en el composition root se afirma de paso: la ruta del contrato es literal.
    assert reverse("core_security:auth-permissions") == RUTA_PERMISOS

    # --- Act ---
    respuesta = cliente_api.get(RUTA_PERMISOS, **cabecera_bearer(sesion_empleado.session_id))

    # --- Assert: contrato ---
    assert respuesta.status_code == 200, f"EP-004 responde 200 al usuario autenticado, no {respuesta.status_code}"

    cuerpo = respuesta.json()
    assert set(cuerpo) == CLAVES_DEL_CONTRATO, (
        "la respuesta publica EXACTAMENTE los tres campos de `EffectivePermissions`: ni el "
        f"identificador del usuario ni la matriz del sistema; llegaron {sorted(cuerpo)}"
    )
    assert cuerpo["roleCode"] == ROL_EMPLEADO, "el rol publicado es el del usuario de la sesion"
    assert cuerpo["dataScope"] == "OWN", f"la matriz concede al {ROL_EMPLEADO} unicamente el alcance propio"

    # --- Assert: las capacidades coinciden EXACTAMENTE con la matriz acordada del rol ---
    operaciones = set(cuerpo["allowedOperations"])
    assert operaciones == set(OPERACIONES_DEL_EMPLEADO), (
        f"`allowedOperations` debe coincidir con lo que `permiso_rol_operacion` concede al {ROL_EMPLEADO}; "
        f"sobran {sorted(operaciones - set(OPERACIONES_DEL_EMPLEADO))} y faltan {sorted(set(OPERACIONES_DEL_EMPLEADO) - operaciones)}"
    )

    # --- Assert: ni permisos de terceros ni matriz completa ---
    for accion in ACCIONES_DEL_TECNICO:
        assert accion not in operaciones, (
            f"{accion} es capacidad del {ROL_TECNICO}: publicarsela al {ROL_EMPLEADO} haria que la SPA "
            "renderizase un control que el backend va a denegar con 403"
        )
    for ajena in OPERACIONES_AJENAS:
        assert ajena not in operaciones, f"{ajena} pertenece a otro rol y no puede asomar en la respuesta del {ROL_EMPLEADO}"

    # La respuesta es una PROYECCION del rol propio, no un volcado de la matriz: estrictamente
    # menos operaciones que filas tiene `permiso_rol_operacion` en la base. Es el oraculo que
    # distingue "me devuelven lo mio" de "me devuelven el mapa entero del sistema".
    filas_de_la_matriz = PermisoRolOperacionEntity.objects.count()
    assert filas_de_la_matriz > 0, "permiso_rol_operacion esta vacia: faltan las semillas del changelog dml"
    assert len(operaciones) < filas_de_la_matriz, (
        f"la respuesta trae {len(operaciones)} operaciones y la matriz completa tiene {filas_de_la_matriz} filas: "
        "EP-004 publica las capacidades del rol vigente, jamas la matriz completa del sistema (AC-ROL-04)"
    )


@SALTAR_SIN_DOCKER
def test_REQ_020_el_identificador_de_usuario_recibido_se_ignora_y_solo_se_resuelve_la_sesion(db, cliente_api: APIClient) -> None:
    """
    [REQ-020] El recurso de permisos efectivos no admite NINGUN parametro de usuario: cualquier
    identificador que llegue en la query string se descarta y solo se resuelve el usuario de la
    sesion.

    El descarte es SILENCIOSO: no produce 400 ni error alguno. Un rechazo selectivo seria a su
    vez un oraculo sobre que identificadores existen; ignorarlo es mas seguro. Lo que se exige
    es que el cuerpo resultante sea IDENTICO al de la peticion sin parametros: el endpoint no
    sirve para espiar los permisos de otra persona.
    """

    # --- Arrange ---
    empleado = sembrar_usuario(ROL_EMPLEADO, "req020-empleado")
    tecnico = sembrar_usuario(ROL_TECNICO, "req020-tecnico")
    sesion_empleado = ServicioSesiones().emitir(empleado)

    # Peticion limpia: el cuerpo de referencia contra el que se compara el intento de suplantacion.
    referencia = cliente_api.get(RUTA_PERMISOS, **cabecera_bearer(sesion_empleado.session_id))
    assert referencia.status_code == 200

    # --- Act: el cliente intenta colar la identidad y el rol del TECNICO por la query string ---
    parametros = {
        "userId": str(tecnico.user_id),
        "user_id": str(tecnico.user_id),
        "roleCode": ROL_TECNICO,
    }
    respuesta = cliente_api.get(RUTA_PERMISOS, parametros, **cabecera_bearer(sesion_empleado.session_id))

    # --- Assert ---
    assert respuesta.status_code == 200, (
        f"el descarte es SILENCIOSO: la peticion manipulada se atiende con normalidad y no responde {respuesta.status_code}"
    )

    cuerpo = respuesta.json()
    assert cuerpo == referencia.json(), (
        "el cuerpo con parametros de usuario debe ser IDENTICO al de la peticion sin parametros: "
        "el identificador recibido no participa en ninguna decision"
    )
    assert cuerpo["roleCode"] == ROL_EMPLEADO, f"se resuelve el usuario de la SESION, nunca el {ROL_TECNICO} que pide la URL"
    assert cuerpo["dataScope"] == "OWN", "el alcance sigue siendo el del rol de la sesion"

    # El tercero sigue siendo otra fila viva de la base: lo que no se publica es su permiso, no
    # es que el usuario no existiese.
    assert UsuarioEntity.objects.filter(pk=tecnico.user_id).exists()


def test_AC_SES_04_la_ruta_de_permisos_efectivos_esta_montada_y_protegida_por_defecto(cliente: Client) -> None:
    """
    [AC-SES-04] EP-004 se sirve EXACTAMENTE en `/api/auth/permissions`, es ruta PROTEGIDA y sin
    credencial responde el 401 canonico del servicio.

    No toca base de datos a proposito: la denegacion por credencial ausente la resuelve el
    guardia de sesion ANTES de consultar Oracle, asi que este escenario corre sin contenedor y
    acredita que el endpoint quedo protegido POR DEFECTO, por no estar declarado en
    `RUTAS_PUBLICAS`, y no por una comprobacion que alguien tuviera que acordarse de escribir.
    """

    # La ruta del contrato es literal: base `/api` del `openapi.yaml` mas el path del recurso.
    assert reverse("core_security:auth-permissions") == RUTA_PERMISOS

    respuesta = cliente.get(RUTA_PERMISOS)

    assert respuesta.status_code == 401, f"EP-004 es ruta protegida: sin sesion responde 401, no {respuesta.status_code}"
    cuerpo = respuesta.json()
    assert cuerpo["code"] == CODIGO_SESION_INVALIDA
    assert cuerpo["message"] == mensajes.SESION_REQUERIDA
    assert cuerpo["details"] == []
    assert isinstance(cuerpo["traceId"], str)
    assert cuerpo["traceId"] != "", "el 401 uniforme siempre correlaciona con un traceId"

    # Protegida POR DEFECTO: no esta en la lista explicita de rutas publicas ni queda exenta.
    assert all(ruta.path != RUTA_PERMISOS for ruta in RUTAS_PUBLICAS), (
        "EP-004 no es una ruta publica: anadirla a RUTAS_PUBLICAS expondria los permisos sin sesion"
    )
    assert es_ruta_exenta(RUTA_PERMISOS, "GET") is False
