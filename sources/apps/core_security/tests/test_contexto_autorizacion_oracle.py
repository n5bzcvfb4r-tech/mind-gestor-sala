"""
Pruebas de la resolucion del CONTEXTO de identidad y ROL VIGENTE contra Oracle REAL.

El motor de prueba de este modulo es el Oracle Database 23ai Free REAL del proyecto
(`gvenzl/oracle-free:23-slim`), con el esquema aplicado por Liquibase sobre el changelog
`sources/facilities/master.xml`. Esta PROHIBIDO sustituirlo por H2, SQLite o cualquier doble en
memoria: lo que se acredita aqui es que el rol efectivo se RELEE de la base en cada peticion
(REQ-018) y que el permiso se decide con los catalogos `cat_rol`, `cat_operacion` y
`permiso_rol_operacion` que siembra el changelog dml; contra un `dict` en memoria un verde no
probaria nada de eso.

Si no hay engine Docker alcanzable, las pruebas que tocan la base se SALTAN mediante
`SALTAR_SIN_DOCKER`, nunca se degradan a otro backend.

La cadena de fixtures de contenedor (`red_docker` -> `oracle_contenedor` -> `esquema_aplicado`
-> `django_db_setup`) y las semillas (`usuario_activo`, `sesion_vigente`, `cliente_api`) NO se
duplican aqui: se REUTILIZAN del conftest del paquete (`apps/core_security/tests/conftest.py`),
de modo que no puedan desincronizarse del DDL ni de las credenciales reales.
"""

import json
from uuid import uuid4

import pytest
from django.contrib.auth.hashers import make_password
from django.http import HttpResponse
from django.test import RequestFactory

from apps.core.contexto import ContextoSesion, utc_now
from apps.core.models import RolEntity, SesionUsuarioEntity, UsuarioEntity
from apps.core_security.errores import PermisoDenegadoError
from apps.core_security.middleware import SesionRequeridaMiddleware
from apps.core_security.servicios.contexto import identidad_suministrada_por_el_cliente
from apps.core_security.servicios.permisos import ServicioPermisos
from apps.core_security.tests.conftest import SALTAR_SIN_DOCKER, cabecera_bearer


pytestmark = [pytest.mark.integration]


#: Ruta protegida del contrato sobre la que se ejercita la resolucion del contexto.
RUTA_PROTEGIDA = "/api/incidents"

#: Codigo canonico del 403 de permiso denegado.
CODIGO_PERMISO_DENEGADO = "PERM_DENIED"

# LITERALES DE CATALOGO. Los `role_code` y `operation_code` de abajo NO son datos de prueba que
# este modulo inserte: se LEEN de los catalogos que siembra Liquibase (`cat_rol`,
# `cat_operacion`, `permiso_rol_operacion`). Estas constantes son unicamente los literales que
# la prueba BUSCA en la base; si el catalogo no los trae, la prueba falla a proposito, porque
# eso seria un defecto real de las semillas y no algo que deba taparse con valores por defecto.
ROL_EMPLEADO = "EMPLEADO"
ROL_TECNICO = "TECNICO_MANTENIMIENTO"
ROL_ADMINISTRADOR = "ADMINISTRADOR"
OPERACION_LISTADO_PROPIO = "INCIDENT_LIST_OWN"
OPERACION_LISTADO_COMPLETO = "INCIDENT_LIST_ALL"


def sembrar_usuario(role_code: str, etiqueta: str) -> UsuarioEntity:
    """
    Siembra en el Oracle real un usuario ACTIVO con el `role_code` indicado y lo devuelve releido.

    El alta se hace con `bulk_create`, que NO pasa por `save()` y por tanto no invoca al
    `AtribucionMixin`: es la UNICA via posible aqui, porque el `user_id` lo genera la IDENTITY de
    Oracle (no existe antes del INSERT) y porque la CHECK `ck_usuario_created_by_self` del DDL
    prohibe que un usuario sea su propio `created_by`. En Oracle `bulk_create` no devuelve la PK
    generada, asi que la fila se RELEE por `corporate_email` con `select_related("role_code")`.

    `password_hash` es un hash REAL generado con `make_password` (Argon2id), nunca un literal de
    relleno; la contrasenia en claro no se persiste en ninguna columna (REQ-063). El correo y el
    `username` llevan sufijo aleatorio para no colisionar con los indices unicos insensibles a
    mayusculas `ux_usuario_email_ci` y `ux_usuario_username_ci` (REQ-045).
    """

    assert RolEntity.objects.filter(pk=role_code).exists(), f"cat_rol no trae el rol {role_code}: faltan las semillas del changelog dml"

    sufijo = uuid4().hex[:8]
    correo = f"{etiqueta}.{sufijo}@mind.local"

    UsuarioEntity.objects.bulk_create(
        [
            UsuarioEntity(
                full_name=f"Usuario {etiqueta} de prueba {sufijo}",
                corporate_email=correo,
                username=f"{etiqueta}.{sufijo}",
                role_code=RolEntity.objects.get(pk=role_code),
                status="ACTIVO",
                password_hash=make_password(f"Clave-{etiqueta}-2026"),
                password_algorithm="argon2id",
                must_change_password="N",
                failed_password_attempts=0,
                created_at=utc_now(),
            )
        ]
    )

    return UsuarioEntity.objects.select_related("role_code").get(corporate_email=correo)


def resolver_por_http(credencial, *, parametros=None, cuerpo=None, cabeceras=None):
    """Devuelve `(request, respuesta)` tras atravesar el guardia de sesion REAL.

    El contexto resuelto queda en `request.contexto_sesion` cuando el guardia deja pasar;
    si deniega, `respuesta` trae el cuerpo canonico de error y `request` no lleva contexto.

    No hay mocks: la peticion recorre el camino de produccion completo (middleware
    `SesionRequeridaMiddleware` -> `ServicioSesiones.validar()` -> resolutor contra Oracle).
    El `get_response` devuelve un 204 vacio precisamente para que lo unico observable sea lo
    que el guardia dejo en la peticion.
    """

    factory = RequestFactory()
    extra = {**cabecera_bearer(credencial), **(cabeceras or {})}
    if cuerpo is not None:
        request = factory.post(RUTA_PROTEGIDA, data=json.dumps(cuerpo), content_type="application/json", **extra)
    else:
        request = factory.get(RUTA_PROTEGIDA, data=parametros or {}, **extra)

    middleware = SesionRequeridaMiddleware(lambda _peticion: HttpResponse(status=204))
    respuesta = middleware(request)
    return request, respuesta


def test_los_literales_de_catalogo_de_esta_suite_no_estan_vacios() -> None:
    """Prueba de arranque del modulo: no toca base de datos y acredita que el runner lo recoge."""

    assert ROL_EMPLEADO and ROL_TECNICO and ROL_ADMINISTRADOR
    assert OPERACION_LISTADO_PROPIO != OPERACION_LISTADO_COMPLETO


@SALTAR_SIN_DOCKER
def test_AC_ROL_01_el_rol_y_la_identidad_se_leen_de_la_base_y_el_dato_del_cliente_se_descarta(
    usuario_activo: UsuarioEntity, sesion_vigente: SesionUsuarioEntity
) -> None:
    """
    [AC-ROL-01] Un EMPLEADO que envia role=TECNICO_MANTENIMIENTO, data_scope=ALL y un user_id
    ajeno en query y cabecera obtiene el rol y la identidad de BASE DE DATOS, y su alcance sigue
    siendo el propio.

    El descarte del dato del cliente es SILENCIOSO (REQ-018): no produce 400 ni ningun otro
    error. La peticion se atiende con absoluta normalidad; lo unico que ocurre es que el rol, el
    alcance y el identificador recibidos del cliente NO participan en ninguna decision, porque
    el rol efectivo se relee de la tabla `usuario` y la identidad sale de la fila de sesion.
    """

    # Arrange
    assert usuario_activo.role_code_id == ROL_EMPLEADO, (
        f"la fixture debe sembrar un {ROL_EMPLEADO} y ha sembrado {usuario_activo.role_code_id}: "
        "si el catalogo cambia, este escenario debe fallar y no colarse por otro camino"
    )

    ajeno = sembrar_usuario(ROL_TECNICO, "ajeno")
    assert ajeno.user_id != usuario_activo.user_id, "el usuario ajeno debe ser otra fila real de la base"

    # El cliente intenta colar rol, alcance e identidad ajena por la query string...
    parametros = {"role": ROL_TECNICO, "data_scope": "ALL", "session_user_id": str(ajeno.user_id)}
    # ...y, por si acaso, tambien por cabecera.
    cabeceras = {"HTTP_X_ROLE": ROL_TECNICO, "HTTP_X_USER_ID": str(ajeno.user_id)}

    # Act
    request, respuesta = resolver_por_http(sesion_vigente.session_id, parametros=parametros, cabeceras=cabeceras)

    # Assert
    assert respuesta.status_code == 204, (
        "el descarte es SILENCIOSO (REQ-018): la peticion manipulada se atiende con normalidad y "
        f"llega al get_response de prueba, no se responde {respuesta.status_code}"
    )

    contexto = getattr(request, "contexto_sesion", None)
    assert isinstance(contexto, ContextoSesion), "el guardia debe dejar el contexto resuelto en la peticion"

    assert contexto.user_id == usuario_activo.user_id, "el user_id sale de la sesion, nunca del cliente (REQ-064)"
    assert contexto.user_id != ajeno.user_id, "el user_id sale de la sesion, nunca del cliente (REQ-064)"

    assert contexto.role_code == ROL_EMPLEADO, "el rol efectivo se relee de la tabla usuario; el role_code del cliente se descarta (REQ-018)"
    assert contexto.role_code != ROL_TECNICO, "el rol efectivo se relee de la tabla usuario; el role_code del cliente se descarta (REQ-018)"

    # El alcance NO es ALL por el hecho de que el cliente lo haya pedido: se resuelve contra la
    # matriz REAL `permiso_rol_operacion` que siembra Liquibase.
    alcance_propio = ServicioPermisos().alcance_de(contexto, OPERACION_LISTADO_PROPIO)
    assert alcance_propio.data_scope == "OWN", (
        f"la matriz concede al {ROL_EMPLEADO} unicamente el alcance propio sobre "
        f"{OPERACION_LISTADO_PROPIO}, pese al data_scope=ALL enviado por el cliente"
    )

    with pytest.raises(PermisoDenegadoError) as excinfo:
        ServicioPermisos().alcance_de(contexto, OPERACION_LISTADO_COMPLETO)
    assert excinfo.value.codigo == CODIGO_PERMISO_DENEGADO, "el listado completo se deniega con el codigo canonico"
    assert excinfo.value.http_status == 403, f"el listado completo le esta vedado al {ROL_EMPLEADO} (deny by default)"

    # Acredita que el dato del cliente SI viajo en la peticion y, aun asi, SI se ignoro: sin esta
    # comprobacion el escenario podria estar verde simplemente porque el parametro nunca llego.
    # `identidad_suministrada_por_el_cliente` es SOLO DIAGNOSTICO: su valor no participa en
    # ninguna decision del producto, solo deja constancia de lo que se descarto.
    recibidos = identidad_suministrada_por_el_cliente(request)
    for nombre in ("role", "data_scope", "session_user_id", "HTTP_X_ROLE"):
        assert nombre in recibidos, f"el dato {nombre} debia viajar en la peticion para poder acreditar que se ignora"

    # Un payload manipulado no altera estado: la sesion sigue viva y con el rol real del usuario.
    sesion_releida = SesionUsuarioEntity.objects.get(pk=sesion_vigente.session_id)
    assert sesion_releida.revoked_at is None, "un intento de suplantacion no revoca la sesion: se ignora en silencio"
    assert sesion_releida.role_code_id == ROL_EMPLEADO, "la sesion conserva el rol real del usuario; el rol enviado por el cliente no se escribe en base"


@SALTAR_SIN_DOCKER
def test_AC_PERM_05_el_cambio_de_rol_surte_efecto_en_la_siguiente_peticion_sin_relogin(
    usuario_activo: UsuarioEntity, sesion_vigente: SesionUsuarioEntity
) -> None:
    """
    [AC-PERM-05] Promocion y degradacion de rol con la MISMA credencial de sesion: el rol efectivo
    lo manda la tabla usuario, no el congelado al emitir la sesion.
    """

    # --- 1. Situacion de partida: EMPLEADO ---
    assert usuario_activo.role_code_id == ROL_EMPLEADO, (
        f"la fixture debe sembrar un {ROL_EMPLEADO} y ha sembrado {usuario_activo.role_code_id}"
    )

    administrador = sembrar_usuario(ROL_ADMINISTRADOR, "admin")

    request, _ = resolver_por_http(sesion_vigente.session_id)
    contexto = request.contexto_sesion
    assert contexto.role_code == ROL_EMPLEADO, "la peticion de partida se resuelve con el rol que hoy tiene el usuario en base"

    # El listado completo le esta vedado hoy.
    with pytest.raises(PermisoDenegadoError):
        ServicioPermisos().alcance_de(contexto, OPERACION_LISTADO_COMPLETO)

    credencial = sesion_vigente.session_id
    sesion_de_partida = SesionUsuarioEntity.objects.get(pk=credencial)
    assert sesion_de_partida.permissions_refreshed_at is None, (
        "sin cambio de rol no hay recarga que sellar: el instante de refresco solo se escribe cuando el rol cambia"
    )

    # --- 2. El ADMINISTRADOR promueve el usuario a TECNICO_MANTENIMIENTO ---
    # El endpoint de cambio de rol (EP-011) es propiedad de otra TSK; lo que se reproduce aqui es
    # su EFECTO EN BASE DE DATOS, que es exactamente lo que REQ-011 exige que observe la siguiente
    # peticion. El `update()` de queryset no pasa por `save()` y por tanto no exige contexto de
    # atribucion publicado. `role_changed_by` es una ForeignKey con `db_column="role_changed_by"`,
    # asi que en un `update()` de queryset el nombre correcto del atributo es `role_changed_by_id`.
    UsuarioEntity.objects.filter(pk=usuario_activo.user_id).update(
        role_code_id=ROL_TECNICO,
        role_changed_at=utc_now(),
        role_changed_by_id=administrador.user_id,
    )

    # --- 3. SIGUIENTE peticion con la MISMA credencial, sin relogin ---
    request_2, respuesta_2 = resolver_por_http(credencial)
    assert respuesta_2.status_code == 204, "la sesion sigue viva: el cambio de rol no obliga a autenticarse de nuevo"

    contexto_2 = request_2.contexto_sesion
    assert contexto_2.role_code == ROL_TECNICO, (
        "la siguiente peticion se evalua con el rol NUEVO, nunca con el anterior (REQ-011 regla 2)"
    )

    assert ServicioPermisos().alcance_de(contexto_2, OPERACION_LISTADO_COMPLETO).data_scope == "ALL", (
        f"el {ROL_TECNICO} alcanza el listado completo en cuanto el rol nuevo esta vigente en base"
    )

    sesion_tras_promocion = SesionUsuarioEntity.objects.get(pk=credencial)
    assert sesion_tras_promocion.revoked_at is None, (
        "un cambio de rol NO invalida la sesion (REQ-011 regla 3): no se exige relogin"
    )
    assert sesion_tras_promocion.permissions_refreshed_at is not None, (
        "se sella el instante de la recarga de rol y capacidades"
    )
    assert sesion_tras_promocion.role_code_id == ROL_TECNICO, (
        "las capacidades cacheadas en la sesion se han recargado (REQ-011 regla 1)"
    )

    # --- 4. Degradacion: el ADMINISTRADOR devuelve el usuario a EMPLEADO ---
    UsuarioEntity.objects.filter(pk=usuario_activo.user_id).update(
        role_code_id=ROL_EMPLEADO,
        role_changed_at=utc_now(),
        role_changed_by_id=administrador.user_id,
    )

    request_3, _ = resolver_por_http(credencial)
    contexto_3 = request_3.contexto_sesion
    assert contexto_3.role_code == ROL_EMPLEADO, (
        "la degradacion surte efecto en la siguiente peticion, con la misma credencial de sesion"
    )

    # La MISMA peticion que hace un momento le daba acceso ahora se deniega.
    with pytest.raises(PermisoDenegadoError) as excinfo:
        ServicioPermisos().alcance_de(contexto_3, OPERACION_LISTADO_COMPLETO)
    assert excinfo.value.codigo == CODIGO_PERMISO_DENEGADO, "sin ventana de privilegio residual tras la degradacion"
    assert excinfo.value.http_status == 403, "sin ventana de privilegio residual tras la degradacion"
