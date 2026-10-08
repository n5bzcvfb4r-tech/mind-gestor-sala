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

import inspect
import json

import pytest
from django.db import connections
from django.db.models import Count
from django.http import HttpResponse
from django.test import RequestFactory

from apps.core.contexto import AlcanceDatos, ContextoSesion
from apps.core.models.catalogos import OperacionEntity, PermisoRolOperacionEntity, RolEntity
from apps.core.models.transaccional import SesionUsuarioEntity, UsuarioEntity
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
ROL_TECNICO = "TECNICO_MANTENIMIENTO"
OPERACION_LISTADO_PROPIO = "INCIDENT_LIST_OWN"
OPERACION_LISTADO_COMPLETO = "INCIDENT_LIST_ALL"

#: Acciones de gestion del ciclo de vida de la incidencia (REQ-030): autoasignacion, cambio de
#: estado y cierre con comentario de resolucion.
OPERACIONES_CICLO_DE_VIDA = ("INCIDENT_ASSIGN_SELF", "INCIDENT_STATUS_CHANGE", "INCIDENT_CLOSE_WITH_COMMENT")

# MATRIZ ACORDADA rol x operacion. Es el ORACULO del criterio AC-G-04: el acuerdo funcional
# ESCRITO AQUI, en el test, y no derivado de la base. Si se leyese de `permiso_rol_operacion`
# para compararla consigo misma, la prueba seria una tautologia y no detectaria jamas una semilla
# equivocada; justo al reves, lo que se contrasta es lo que el sistema DECIDE contra lo acordado.
# El alcance esperado es `OWN`/`ALL` para las celdas autorizadas y `None` para las DENEGADAS, de
# modo que las 9 operaciones x 2 roles = 18 celdas queden en una sola estructura legible.
MATRIZ_ACORDADA: dict[str, dict[str, str | None]] = {
    ROL_EMPLEADO: {
        "INCIDENT_CREATE": AlcanceDatos.OWN.value,
        OPERACION_LISTADO_PROPIO: AlcanceDatos.OWN.value,
        OPERACION_LISTADO_COMPLETO: None,
        "INCIDENT_VIEW": AlcanceDatos.OWN.value,
        "INCIDENT_HISTORY_VIEW": AlcanceDatos.OWN.value,
        "INCIDENT_ASSIGN_SELF": None,
        "INCIDENT_STATUS_CHANGE": None,
        "INCIDENT_CLOSE_WITH_COMMENT": None,
        "USER_MANAGE": None,
    },
    ROL_TECNICO: {
        "INCIDENT_CREATE": None,
        OPERACION_LISTADO_PROPIO: None,
        OPERACION_LISTADO_COMPLETO: AlcanceDatos.ALL.value,
        "INCIDENT_VIEW": AlcanceDatos.ALL.value,
        "INCIDENT_HISTORY_VIEW": AlcanceDatos.ALL.value,
        "INCIDENT_ASSIGN_SELF": AlcanceDatos.ALL.value,
        "INCIDENT_STATUS_CHANGE": AlcanceDatos.ALL.value,
        "INCIDENT_CLOSE_WITH_COMMENT": AlcanceDatos.ALL.value,
        "USER_MANAGE": None,
    },
}

#: Conjunto completo de operaciones funcionales del sistema, derivado de la matriz acordada.
OPERACIONES_FUNCIONALES = tuple(MATRIZ_ACORDADA[ROL_EMPLEADO])

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

    assert not PermisoRolOperacionEntity.objects.filter(role_code_id=ROL_EMPLEADO, operation_code_id=OPERACION_LISTADO_COMPLETO).exists(), (
        f"la matriz NO debe conceder {OPERACION_LISTADO_COMPLETO} al {ROL_EMPLEADO}: ese par es el que ejercita el deny by default"
    )

    # El rol SI tiene otras filas: asi queda claro que la denegacion es del PAR concreto y no el
    # efecto de una matriz vacia, que denegaria todo por accidente y no por regla.
    assert PermisoRolOperacionEntity.objects.filter(role_code_id=ROL_EMPLEADO, operation_code_id=OPERACION_LISTADO_PROPIO).exists(), (
        f"la matriz debe conceder {OPERACION_LISTADO_PROPIO} al {ROL_EMPLEADO}: faltan las semillas del changelog dml"
    )

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


@SALTAR_SIN_DOCKER
def test_AC_G_04_las_18_celdas_rol_operacion_coinciden_al_100_por_cien_con_la_matriz_acordada(db) -> None:
    """
    [AC-G-04] El conjunto COMPLETO de operaciones funcionales invocado con una sesion EMPLEADO y
    con una sesion TECNICO_MANTENIMIENTO produce exactamente el resultado de la matriz acordada
    -autorizado con alcance `OWN`/`ALL`, o 403-, sin ninguna desviacion y sin efecto lateral.

    ORACULO ESCRITO, NO DERIVADO. Lo esperado es `MATRIZ_ACORDADA`, el acuerdo funcional escrito
    en este modulo. No se lee de `permiso_rol_operacion` para compararlo consigo mismo: eso seria
    una tautologia y dejaria pasar una semilla equivocada sin que nadie se enterase.

    UNA SOLA PRUEBA PARA LAS 18 CELDAS. No se parametriza a proposito: el criterio exige recorrer
    el conjunto completo y acreditar que NO hay ninguna desviacion. Las discrepancias se acumulan
    y se falla UNA vez con todas listadas, que es mas util para el revisor que 18 fallos sueltos
    de los que solo se mira el primero.

    POR EL CAMINO PRODUCTIVO DE DECISION. Cada celda se ejercita con
    `ServicioPermisos().alcance_de(contexto, operation_code)`, que es la via que usa la aplicacion
    real, no una consulta propia de la prueba contra la tabla.

    NI FALTA NI SOBRA. Ademas de las 18 celdas se compara el conjunto de pares con fila en la
    matriz para estos dos roles con los pares autorizados del acuerdo: si la base concediera un
    par que el acuerdo no recoge, las 18 celdas podrian seguir verdes y el sistema estaria
    autorizando de mas. Ese es el sentido fuerte de "coincide al 100 %".
    """

    # --- Arrange: catalogos REALES; si no traen el conjunto acordado, la prueba falla a proposito ---
    operaciones_en_catalogo = set(OperacionEntity.objects.filter(pk__in=OPERACIONES_FUNCIONALES).values_list("operation_code", flat=True))
    assert operaciones_en_catalogo == set(OPERACIONES_FUNCIONALES), (
        f"cat_operacion no trae el conjunto completo de operaciones funcionales; faltan "
        f"{sorted(set(OPERACIONES_FUNCIONALES) - operaciones_en_catalogo)}: es un defecto real de las semillas del changelog dml"
    )

    roles_en_catalogo = set(RolEntity.objects.filter(pk__in=MATRIZ_ACORDADA).values_list("role_code", flat=True))
    assert roles_en_catalogo == set(MATRIZ_ACORDADA), (
        f"cat_rol no trae los roles del acuerdo; faltan {sorted(set(MATRIZ_ACORDADA) - roles_en_catalogo)}: defecto de las semillas"
    )

    # Un usuario por rol, sembrado ANTES de contar filas: lo que la prueba vigila despues es que el
    # RECORRIDO no escriba nada, no que la siembra no escriba (esa si debe hacerlo).
    contextos_de_partida: dict[str, ContextoSesion] = {}
    for role_code in MATRIZ_ACORDADA:
        usuario = sembrar_usuario(role_code, f"acg04-{role_code.lower()}")
        assert usuario.role_code_id == role_code, f"el usuario sembrado debe ser {role_code} y es {usuario.role_code_id}"
        contextos_de_partida[role_code] = ContextoSesion(
            user_id=usuario.user_id,
            role_code=usuario.role_code_id,
            session_id=None,
            display_name=usuario.full_name,
        )

    permisos_antes = PermisoRolOperacionEntity.objects.count()
    usuarios_antes = UsuarioEntity.objects.count()
    sesiones_antes = SesionUsuarioEntity.objects.count()
    alcances_de_partida = {role_code: contexto.data_scope for role_code, contexto in contextos_de_partida.items()}

    # --- Act + Assert: las 18 celdas, acumulando desviaciones ---
    desviaciones: list[str] = []
    for role_code, fila_acordada in MATRIZ_ACORDADA.items():
        contexto = contextos_de_partida[role_code]
        for operation_code, alcance_esperado in fila_acordada.items():
            try:
                contexto_resuelto = ServicioPermisos().alcance_de(contexto, operation_code)
            except PermisoDenegadoError as denegacion:
                if alcance_esperado is not None:
                    desviaciones.append(
                        f"({role_code}, {operation_code}): el acuerdo AUTORIZA con alcance {alcance_esperado} y el sistema deniega"
                    )
                    continue
                # Celda denegada: el 403 ha de ser el canonico del contrato, no un error cualquiera.
                if denegacion.codigo != CODIGO_PERMISO_DENEGADO:
                    desviaciones.append(
                        f"({role_code}, {operation_code}): codigo {denegacion.codigo!r} y se esperaba {CODIGO_PERMISO_DENEGADO!r}"
                    )
                if denegacion.http_status != 403:
                    desviaciones.append(f"({role_code}, {operation_code}): http_status {denegacion.http_status} y se esperaba 403")
                if denegacion.mensaje != mensajes.SIN_PERMISOS:
                    desviaciones.append(
                        f"({role_code}, {operation_code}): literal {denegacion.mensaje!r} y REQ-009 fija {mensajes.SIN_PERMISOS!r}"
                    )
                continue

            if alcance_esperado is None:
                desviaciones.append(
                    f"({role_code}, {operation_code}): el acuerdo lo DENIEGA y el sistema AUTORIZA con "
                    f"alcance {contexto_resuelto.data_scope}"
                )
            elif contexto_resuelto.data_scope != alcance_esperado:
                desviaciones.append(
                    f"({role_code}, {operation_code}): alcance {contexto_resuelto.data_scope!r} y el acuerdo fija {alcance_esperado!r}"
                )

    assert desviaciones == [], "la matriz rol x operacion del sistema NO coincide con la acordada:\n" + "\n".join(desviaciones)

    # --- Assert: ninguna denegacion ha tenido efecto lateral ---
    assert PermisoRolOperacionEntity.objects.count() == permisos_antes, (
        "el recorrido no escribe en permiso_rol_operacion: el camino de 403 no tiene efecto lateral en base"
    )
    assert UsuarioEntity.objects.count() == usuarios_antes, "el recorrido no da de alta ni de baja usuarios"
    assert SesionUsuarioEntity.objects.count() == sesiones_antes, "el recorrido no abre ni cierra sesiones"

    for role_code, contexto in contextos_de_partida.items():
        assert contexto.data_scope == alcances_de_partida[role_code], (
            f"el contexto de partida de {role_code} ha cambiado de alcance: `ContextoSesion` es frozen y `alcance_de` "
            "devuelve un objeto NUEVO, de modo que nadie puede haber ensanchado el alcance del original"
        )

    # --- Assert: ni falta ni sobra ningun par concedido para estos dos roles ---
    pares_en_base = set(
        PermisoRolOperacionEntity.objects.filter(role_code_id__in=MATRIZ_ACORDADA).values_list("role_code_id", "operation_code_id")
    )
    pares_acordados = {
        (role_code, operation_code)
        for role_code, fila in MATRIZ_ACORDADA.items()
        for operation_code, alcance in fila.items()
        if alcance is not None
    }
    assert pares_en_base == pares_acordados, (
        f"permiso_rol_operacion desvia del acuerdo para {sorted(MATRIZ_ACORDADA)}: "
        f"sobran {sorted(pares_en_base - pares_acordados)} y faltan {sorted(pares_acordados - pares_en_base)}"
    )


@SALTAR_SIN_DOCKER
def test_REQ_030_las_acciones_de_ciclo_de_vida_son_exclusivas_del_tecnico_sea_quien_sea_el_reportante(db) -> None:
    """
    [REQ-030] Autoasignacion, cambio de estado y cierre con comentario de resolucion son
    EXCLUSIVAS del rol TECNICO_MANTENIMIENTO: cualquier otro rol recibe 403 en las tres, con
    independencia de quien haya reportado la incidencia.

    CONTRA TODO `cat_rol`, NO CONTRA UNA LISTA FIJA. Los roles se LEEN de la base
    (`RolEntity.objects.all()`), de modo que si manana el catalogo incorpora un rol nuevo esta
    prueba lo cubre sola y denuncia que se le hayan concedido acciones de ciclo de vida. Una
    lista fija en el test dejaria ese rol nuevo sin vigilar justo el dia en que aparece.

    INDEPENDIENTEMENTE DE QUIEN HAYA REPORTADO. La decision sale UNICAMENTE de la matriz rol x
    operacion: en ella no interviene ningun identificador de incidencia ni de reportante. El
    oraculo de esa parte del requisito es la FIRMA del camino productivo, que se verifica al
    final: `alcance_de(contexto, operation_code)` no recibe -ni puede recibir- la incidencia ni
    su autor, asi que no existe ninguna via por la que ser el reportante ablande la denegacion.

    Las desviaciones se acumulan y se falla una sola vez con todas, igual que en AC-G-04.
    """

    # --- Arrange: catalogos reales ---
    for operation_code in OPERACIONES_CICLO_DE_VIDA:
        assert OperacionEntity.objects.filter(pk=operation_code).exists(), (
            f"cat_operacion no trae {operation_code}: la denegacion debe nacer de la AUSENCIA DE FILA en la matriz, "
            "no de un operation_code inexistente (defecto de las semillas del changelog dml)"
        )

    roles = list(RolEntity.objects.all())
    assert roles, "cat_rol esta vacio: sin roles el escenario no probaria nada"

    codigos_de_rol = [rol.role_code for rol in roles]
    assert ROL_TECNICO in codigos_de_rol, f"cat_rol no trae {ROL_TECNICO}, que es el rol al que el requisito reserva el ciclo de vida"
    assert [codigo for codigo in codigos_de_rol if codigo != ROL_TECNICO], (
        "el catalogo debe traer algun rol distinto del tecnico: si no, la exclusividad no seria comprobable"
    )

    # --- Act + Assert: tres operaciones x todos los roles del catalogo ---
    desviaciones: list[str] = []
    for role_code in codigos_de_rol:
        usuario = sembrar_usuario(role_code, f"req030-{role_code.lower()}")
        contexto = ContextoSesion(
            user_id=usuario.user_id,
            role_code=usuario.role_code_id,
            session_id=None,
            display_name=usuario.full_name,
        )
        exclusivo = role_code == ROL_TECNICO

        for operation_code in OPERACIONES_CICLO_DE_VIDA:
            try:
                contexto_resuelto = ServicioPermisos().alcance_de(contexto, operation_code)
            except PermisoDenegadoError as denegacion:
                if exclusivo:
                    desviaciones.append(f"({role_code}, {operation_code}): el rol titular del ciclo de vida NO deberia ser denegado")
                    continue
                if denegacion.http_status != 403 or denegacion.codigo != CODIGO_PERMISO_DENEGADO:
                    desviaciones.append(
                        f"({role_code}, {operation_code}): denegado con {denegacion.codigo!r}/{denegacion.http_status} "
                        f"y se esperaba {CODIGO_PERMISO_DENEGADO!r}/403"
                    )
                if denegacion.mensaje != mensajes.SIN_PERMISOS:
                    desviaciones.append(
                        f"({role_code}, {operation_code}): literal {denegacion.mensaje!r} y REQ-009 fija {mensajes.SIN_PERMISOS!r}"
                    )
                continue

            if not exclusivo:
                desviaciones.append(
                    f"({role_code}, {operation_code}): un rol distinto de {ROL_TECNICO} ha sido AUTORIZADO con alcance "
                    f"{contexto_resuelto.data_scope}; el ciclo de vida es exclusivo del tecnico"
                )
            elif contexto_resuelto.data_scope != AlcanceDatos.ALL.value:
                desviaciones.append(
                    f"({role_code}, {operation_code}): alcance {contexto_resuelto.data_scope!r} y el tecnico gestiona el ciclo de "
                    f"vida de TODAS las incidencias ({AlcanceDatos.ALL.value!r})"
                )

    assert desviaciones == [], "el ciclo de vida de la incidencia no es exclusivo del tecnico:\n" + "\n".join(desviaciones)

    # --- Assert: la decision no depende de quien reporto la incidencia ---
    # No hay forma de que el EMPLEADO "duenio" de la incidencia reciba un trato distinto del
    # EMPLEADO ajeno a ella, porque el camino de decision NO conoce la incidencia: su firma solo
    # admite el contexto de sesion (rol) y el codigo de operacion. Esto no es un detalle de
    # implementacion que la prueba espie, es el oraculo de "con independencia de quien haya
    # reportado": si alguien anadiera ahi un `incident_id` o un `reported_by`, existiria una via
    # para ablandar la denegacion y esta comprobacion lo detendria.
    parametros = [nombre for nombre in inspect.signature(ServicioPermisos.alcance_de).parameters if nombre != "self"]
    assert parametros == ["contexto", "operation_code"], (
        f"el camino de decision recibe {parametros}: la autorizacion del ciclo de vida sale SOLO de la matriz rol x operacion "
        "y no puede admitir ningun identificador de incidencia ni de reportante (REQ-030)"
    )
