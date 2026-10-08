"""
Pruebas de la MATRIZ UNICA rol x operacion (`permiso_rol_operacion`) contra Oracle REAL.

El motor de prueba de este modulo es el Oracle Database 23ai Free REAL del proyecto
(`gvenzl/oracle-free:23-slim`), con el esquema aplicado por Liquibase sobre el changelog
`sources/facilities/master.xml`. Esta PROHIBIDO sustituirlo por H2, SQLite o cualquier doble en
memoria: lo que se acredita aqui es que la autorizacion la decide la FILA que vive en la base
(REQ-021, REQ-002, ARC-102) y que su AUSENCIA es la denegacion; contra un `dict` en memoria un
verde no probaria nada de eso.

Si no hay engine Docker alcanzable, las pruebas que tocan la base se SALTAN mediante
`SALTAR_SIN_DOCKER`, nunca se degradan a otro backend.

La cadena de fixtures de contenedor (`red_docker` -> `oracle_contenedor` -> `esquema_aplicado`
-> `django_db_setup`) NO se duplica aqui: se REUTILIZA del conftest del paquete
(`apps/core_security/tests/conftest.py`). El sembrado de usuarios tampoco se reescribe: se
REUTILIZA `sembrar_usuario` del modulo hermano `test_contexto_autorizacion_oracle`, de modo que
no pueda desincronizarse del DDL (CHECK `ck_usuario_created_by_self`, IDENTITY del `user_id` e
indices unicos insensibles a mayusculas).
"""

import json

import pytest
from django.db import connections
from django.db.models import Count
from django.http import HttpResponse
from django.test import RequestFactory

from apps.core.contexto import AlcanceDatos, ContextoSesion
from apps.core.models.catalogos import OperacionEntity, PermisoRolOperacionEntity, RolEntity
from apps.core.models.transaccional import UsuarioEntity
from apps.core_security import mensajes
from apps.core_security.errores import PermisoDenegadoError
from apps.core_security.middleware import SesionRequeridaMiddleware
from apps.core_security.servicios.permisos import ServicioPermisos
from apps.core_security.servicios.sesiones import ServicioSesiones
from apps.core_security.tests.conftest import SALTAR_SIN_DOCKER, cabecera_bearer
from apps.core_security.tests.test_contexto_autorizacion_oracle import sembrar_usuario


pytestmark = [pytest.mark.integration]


#: Ruta protegida del contrato sobre la que se ejercita el guardia de sesion.
RUTA_PROTEGIDA = "/api/incidents"

#: Codigo canonico del 403 de permiso denegado (`PermisoDenegadoError` y `RolNoResolubleError`).
CODIGO_PERMISO_DENEGADO = "PERM_DENIED"

# LITERALES DE CATALOGO. Los `role_code` y `operation_code` de abajo NO son datos de prueba que
# este modulo inserte: se LEEN de los catalogos que siembra Liquibase (`cat_rol`,
# `cat_operacion`, `permiso_rol_operacion`). Estas constantes son unicamente los literales que
# la prueba BUSCA en la base; si el catalogo no los trae, la prueba falla a proposito, porque
# eso seria un defecto real de las semillas y no algo que deba taparse con valores por defecto.
ROL_EMPLEADO = "EMPLEADO"
OPERACION_LISTADO_PROPIO = "INCIDENT_LIST_OWN"
OPERACION_LISTADO_COMPLETO = "INCIDENT_LIST_ALL"

#: `role_code` que NO pertenece a `cat_rol`. La prueba comprueba antes que no esta en el
#: catalogo: si algun dia el changelog dml lo sembrase, el escenario debe fallar, no colarse.
ROL_FUERA_DE_CATALOGO = "ROL_INEXISTENTE_AC_PERM_06"

#: Nombre REAL de la clave ajena `usuario.role_code -> cat_rol.role_code` en el DDL de Liquibase.
RESTRICCION_ROL_DE_USUARIO = "fk_usuario_role_code"


def _ejecutar_sql(sentencia: str, parametros: list | None = None) -> None:
    """Ejecuta una sentencia contra el Oracle REAL del contenedor, sin pasar por el ORM."""

    with connections["default"].cursor() as cursor:
        cursor.execute(sentencia, parametros or [])


@SALTAR_SIN_DOCKER
def test_AC_PERM_01_un_par_rol_operacion_sin_fila_en_la_matriz_se_deniega_con_403_y_sin_efecto(db) -> None:
    """
    [AC-PERM-01] Un `operation_code` sin entrada explicita para el rol de la sesion se deniega con
    403, no modifica ningun dato y la decision procede de la matriz UNICA.

    DENY BY DEFAULT (REQ-021, ARC-102). El escenario elige a proposito un par cuya denegacion
    nace de la AUSENCIA DE FILA y no de un codigo inexistente: antes de invocar nada se acredita
    contra la base que `INCIDENT_LIST_ALL` SI esta en `cat_operacion` y que el par
    (EMPLEADO, INCIDENT_LIST_ALL) NO tiene fila en `permiso_rol_operacion`. Sin esa doble
    comprobacion un 403 podria estar verde simplemente porque la operacion no existe.

    SIN EFECTO LATERAL. La denegacion no escribe en la matriz (el recuento de filas es el mismo
    antes y despues) y no ensancha el alcance del contexto recibido: `ContextoSesion` es
    `frozen`, de modo que `alcance_de` construye uno NUEVO y el original queda intacto.

    SIN ENTRADAS CONTRADICTORIAS. La ultima parte del criterio exige que no haya dos decisiones
    distintas para un mismo par rol/operacion: se agrupa la tabla entera por
    (`role_code`, `operation_code`) y se exige que ningun grupo tenga mas de una fila. La
    unicidad real la garantiza la PK compuesta de Oracle; esto lo VERIFICA sobre el dato.
    """

    # --- Arrange: el escenario se acredita LEYENDO los catalogos reales ---
    empleado = sembrar_usuario(ROL_EMPLEADO, "perm01-empleado")
    assert empleado.role_code_id == ROL_EMPLEADO, f"el usuario sembrado debe ser {ROL_EMPLEADO} y es {empleado.role_code_id}"

    assert OperacionEntity.objects.filter(pk=OPERACION_LISTADO_COMPLETO).exists(), (
        f"cat_operacion no trae {OPERACION_LISTADO_COMPLETO}: la denegacion debe nacer de la AUSENCIA DE FILA en la "
        "matriz, no de un operation_code que no existe (defecto de las semillas del changelog dml)"
    )

    assert not PermisoRolOperacionEntity.objects.filter(
        role_code_id=ROL_EMPLEADO, operation_code_id=OPERACION_LISTADO_COMPLETO
    ).exists(), f"la matriz NO debe conceder {OPERACION_LISTADO_COMPLETO} al {ROL_EMPLEADO}: ese par es el que ejercita el deny by default"

    # El rol SI tiene otras filas: asi queda claro que la denegacion es del PAR concreto y no el
    # efecto de una matriz vacia, que denegaria todo por accidente y no por regla.
    assert PermisoRolOperacionEntity.objects.filter(
        role_code_id=ROL_EMPLEADO, operation_code_id=OPERACION_LISTADO_PROPIO
    ).exists(), f"la matriz debe conceder {OPERACION_LISTADO_PROPIO} al {ROL_EMPLEADO}: faltan las semillas del changelog dml"

    filas_antes = PermisoRolOperacionEntity.objects.count()

    contexto = ContextoSesion(
        user_id=empleado.user_id,
        role_code=empleado.role_code_id,
        session_id=None,
        display_name=empleado.full_name,
    )
    alcance_antes = contexto.data_scope
    assert alcance_antes == AlcanceDatos.OWN.value, "el contexto parte del alcance mas restrictivo del dominio"

    # --- Act + Assert: la decision vinculante se toma contra la matriz ---
    with pytest.raises(PermisoDenegadoError) as excinfo:
        ServicioPermisos().resolver(ROL_EMPLEADO, OPERACION_LISTADO_COMPLETO)

    assert excinfo.value.codigo == CODIGO_PERMISO_DENEGADO, "el 403 de la matriz usa el codigo canonico del contrato"
    assert excinfo.value.http_status == 403, "la ausencia de fila responde 403, nunca 404 ni 200 degradado"
    assert excinfo.value.mensaje == mensajes.SIN_PERMISOS, "el literal del 403 lo fija REQ-009 palabra por palabra"

    # El mismo par, por la via que enriquece el contexto de sesion: tampoco concede nada.
    with pytest.raises(PermisoDenegadoError) as excinfo_alcance:
        ServicioPermisos().alcance_de(contexto, OPERACION_LISTADO_COMPLETO)

    assert excinfo_alcance.value.codigo == CODIGO_PERMISO_DENEGADO
    assert excinfo_alcance.value.http_status == 403
    assert excinfo_alcance.value.mensaje == mensajes.SIN_PERMISOS

    # --- Assert: la denegacion NO ha modificado ningun dato ---
    assert PermisoRolOperacionEntity.objects.count() == filas_antes, (
        "una denegacion no escribe en la matriz: el camino de 403 no tiene efecto lateral en base (AC-G-04)"
    )
    assert contexto.data_scope == alcance_antes, (
        "el contexto recibido es inmutable: una denegacion no puede haber ensanchado el alcance de nadie"
    )

    # --- Assert: matriz UNICA, sin entradas contradictorias para un mismo par ---
    contradictorias = list(
        PermisoRolOperacionEntity.objects.values("role_code", "operation_code").annotate(n=Count("permiso_id")).filter(n__gt=1)
    )
    assert contradictorias == [], (
        f"permiso_rol_operacion tiene pares rol/operacion duplicados y por tanto decisiones contradictorias: {contradictorias}"
    )


@SALTAR_SIN_DOCKER
def test_AC_PERM_06_un_usuario_sin_rol_vigente_recibe_403_y_ninguna_operacion_se_ejecuta(db) -> None:
    """
    [AC-PERM-06] Un usuario AUTENTICADO cuyo rol no se puede resolver en base recibe 403 al
    invocar una operacion funcional, y la peticion NO llega a la vista.

    POR QUE SE MATERIALIZA COMO ROL FUERA DE `cat_rol`. El DDL de Liquibase declara
    `usuario.role_code VARCHAR2(32 CHAR) NOT NULL` con la clave ajena `fk_usuario_role_code`
    contra `cat_rol`, y en la instancia Oracle no es DEFERRABLE ni esta deshabilitada. Por eso
    "sin rol vigente" NO puede materializarse dejando el `role_code` a nulo: el unico escenario
    realista contra este esquema es el que el propio codigo productivo documenta como "una fila
    escrita por fuera del ORM", es decir, un `role_code` que no pertenece al catalogo. Para
    reproducirlo se DESHABILITA momentaneamente esa unica restriccion, se escribe la fila y se
    RESTAURA la restriccion con `ENABLE VALIDATE` en el `finally`, junto con el borrado de las
    filas sembradas por la prueba: el esquema queda exactamente como estaba. El `ALTER TABLE` de
    Oracle lleva COMMIT implicito, asi que la limpieza se hace ANTES del `ENABLE` para que quede
    confirmada y no dependa del rollback de la transaccion de prueba.

    POR EL CAMINO HTTP REAL. No se llama al servicio a pelo: lo que se acredita es que el
    guardia `SesionRequeridaMiddleware` -que va POR DELANTE de la vista- responde 403 de verdad.
    El `get_response` de prueba lleva un CONTADOR y la prueba exige que siga a cero: ese es el
    oraculo de "ninguna operacion se ejecuta".
    """

    # --- Arrange ---
    assert not RolEntity.objects.filter(pk=ROL_FUERA_DE_CATALOGO).exists(), (
        f"{ROL_FUERA_DE_CATALOGO} no debe existir en cat_rol: es justamente el rol NO vigente del escenario"
    )

    usuario = sembrar_usuario(ROL_EMPLEADO, "perm06-sin-rol")
    sesion = ServicioSesiones().emitir(usuario)

    # La sesion es VALIDA y el usuario esta ACTIVO: si no, el guardia respondria 401 por sesion y
    # el 403 que se quiere acreditar no seria el de rol no resoluble.
    assert sesion.revoked_at is None, "la sesion debe estar vigente: el escenario es de autorizacion, no de autenticacion"
    assert usuario.status == "ACTIVO", "el usuario debe estar ACTIVO: un usuario inactivo responderia 401 por sesion invalida"

    invocaciones_de_la_vista = {"n": 0}

    def _vista_de_prueba(peticion) -> HttpResponse:
        """Vista de prueba que CUENTA sus invocaciones: el oraculo de "ninguna operacion se ejecuta"."""

        invocaciones_de_la_vista["n"] += 1
        return HttpResponse(status=204)

    _ejecutar_sql(f"ALTER TABLE usuario DISABLE CONSTRAINT {RESTRICCION_ROL_DE_USUARIO}")
    try:
        _ejecutar_sql("UPDATE usuario SET role_code = %s WHERE user_id = %s", [ROL_FUERA_DE_CATALOGO, usuario.user_id])

        # Se releen las dos filas de la base: el escenario tiene que estar REALMENTE sembrado.
        assert UsuarioEntity.objects.get(pk=usuario.user_id).role_code_id == ROL_FUERA_DE_CATALOGO, (
            "el usuario debe quedar con un role_code que no pertenece a cat_rol"
        )
        assert not RolEntity.objects.filter(pk=ROL_FUERA_DE_CATALOGO).exists(), "el rol del usuario no esta en el catalogo: no es vigente"

        # --- Act: operacion funcional por el camino HTTP real, con credencial valida ---
        request = RequestFactory().get(RUTA_PROTEGIDA, **cabecera_bearer(sesion.session_id))
        respuesta = SesionRequeridaMiddleware(_vista_de_prueba)(request)

        # --- Assert ---
        assert respuesta.status_code == 403, (
            f"un usuario sin rol vigente se deniega FAIL-CLOSED con 403 y no con {respuesta.status_code}: "
            "jamas se degrada a un permiso mas amplio (REQ-018, REQ-002)"
        )

        cuerpo = json.loads(respuesta.content)
        assert cuerpo["code"] == CODIGO_PERMISO_DENEGADO, "el cuerpo canonico trae el codigo unico de denegacion"
        assert cuerpo["message"] == mensajes.SIN_PERMISOS, "el literal del 403 lo fija REQ-009 palabra por palabra"
        assert cuerpo["details"] == [], "el cuerpo no revela el motivo interno de la denegacion"
        assert isinstance(cuerpo["traceId"], str) and cuerpo["traceId"] != "", "el correlador es real, no un hueco fijo"

        assert invocaciones_de_la_vista["n"] == 0, (
            "NINGUNA operacion se ejecuta: el guardia corta por delante de la vista y el get_response nunca se invoca"
        )
        assert getattr(request, "contexto_sesion", None) is None, "sin rol resoluble no se publica contexto en la peticion"
    finally:
        # Limpieza ANTES de restaurar la restriccion: el ALTER lleva COMMIT implicito y confirma
        # los borrados, de modo que no queda ninguna fila huerfana con un rol fuera de catalogo.
        _ejecutar_sql("DELETE FROM sesion_usuario WHERE user_id = %s", [usuario.user_id])
        _ejecutar_sql("DELETE FROM usuario WHERE user_id = %s", [usuario.user_id])
        _ejecutar_sql(f"ALTER TABLE usuario ENABLE VALIDATE CONSTRAINT {RESTRICCION_ROL_DE_USUARIO}")
