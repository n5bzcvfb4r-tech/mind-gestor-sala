"""
Fachada HTTP del rol funcional del usuario: cambio de rol (EP-011) e historico (EP-012).

Son DOS rutas distintas del contrato -`PUT /api/users/{userId}/role` y
`GET /api/users/{userId}/role-history`- y por eso son DOS vistas: a diferencia de `/api/users`,
aqui cada path publica una sola operacion, de modo que no hay nada que concentrar.

RUTAS PROTEGIDAS Y RESERVADAS AL ADMINISTRADOR. Ninguna de las dos se declara en
`apps.core_security.rutas_publicas`, de modo que el `SesionRequeridaMiddleware` responde 401 a
cualquier peticion sin sesion valida, y encima va la guardia declarativa
`requiere_administrador("USER_MANAGE")`, que exige el rol ADMINISTRADOR Y una fila en la matriz
`permiso_rol_operacion`. La autorizacion se declara AQUI, junto a cada vista, y NUNCA dentro del
cuerpo del endpoint: un `if actor.role_code != ...` es el anti-patron que prohibe la guia del
proyecto, porque convierte el codigo en una segunda fuente de verdad que se desalinea de la matriz.

ERRORES. Estas vistas NO componen cuerpo de error propio: dejan PROPAGAR los `ErrorDominio` de
`apps.usuarios.roles.errores` (400/404/409/422) y las `ValidationError` de DRF (400), y el manejador
unico (`apps.core_security.manejadores`) los traduce al cuerpo uniforme del servicio (`code`,
`message`, `details`, `traceId`).

LOS ESQUEMAS LOS FIJAN LOS SERIALIZADORES DE ESTE MODULO. `RoleChangeRequest`, `UserRoleDetail` y
`RoleHistoryPage` figuran en `openapi.yaml` como GERMENES (`x-mind-placeholder: true`) y el contrato
NO se edita: la ruta, el verbo y el status se transcriben al pie de la letra y el cuerpo lo fija
`apps.usuarios.roles.serializers`, de modo que lo que publica drf-spectacular y lo que devuelve el
endpoint son lo mismo.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.exceptions import NotAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.contexto import ContextoSesion, obtener_contexto
from apps.core_security.permisos import requiere_administrador

# `OPERACION_GESTION_USUARIOS` se IMPORTA del modulo de bloqueo en vez de redeclararse, exactamente
# igual que hace `apps/usuarios/views.py`: es el MISMO `operation_code` del catalogo `cat_operacion`
# (`USER_MANAGE`), ya sembrado por Liquibase y ya asociado unicamente al rol ADMINISTRADOR en
# `permiso_rol_operacion`. Cambiar el rol de un usuario y consultar su historico son operaciones de
# la misma potestad -la gestion del censo de usuarios-, y una segunda constante con el mismo literal
# solo crearia dos sitios donde corregir el dia que el catalogo cambie.
from apps.identidad.bloqueo.views import OPERACION_GESTION_USUARIOS
from apps.usuarios.roles.serializers import (
    RoleChangeRequestSerializer,
    RoleHistoryPageSerializer,
    RoleHistoryQuerySerializer,
    UserRoleDetailSerializer,
)
from apps.usuarios.roles.servicio import ServicioRolUsuario


__all__ = ["HistoricoRolUsuarioView", "RolUsuarioView"]


class VistaRolUsuarioBase(APIView):
    """
    Base comun de las dos vistas del rol de usuario: solo aporta `_actor_de`.

    EL HELPER SE DECLARA UNA SOLA VEZ Y AQUI, no copiado en cada vista. Las dos vistas del modulo
    resuelven la identidad del administrador EXACTAMENTE con la misma semantica, y duplicar ese
    codigo dejaria dos sitios donde cambiar el dia que cambie la forma de publicar el contexto, con
    el riesgo clasico de corregir uno y olvidar el otro. La clase no se exporta en `__all__` porque
    no es un punto de extension publico: es el sitio donde vive el helper compartido, nada mas, y no
    declara ni `http_method_names` ni `permission_classes`; cada vista concreta los declara EXPLICITOS
    para que leerla baste para saber que exige.
    """

    @staticmethod
    def _actor_de(request: Request) -> ContextoSesion:
        """
        Devuelve el `ContextoSesion` del administrador que ejecuta la operacion, o falla con 401.

        La identidad se lee SOLO de la peticion ya autenticada: primero de `request.auth`, que es
        donde la deja la clase de autenticacion de DRF, y en su defecto del contexto publicado en la
        peticion en curso (`apps.core.contexto.obtener_contexto`), que es donde la republica la
        guardia de autorizacion con el alcance de datos de la operacion. Jamas del cuerpo, de la
        query string ni de una cabecera: de ahi sale el `role_changed_by` del cambio de rol
        (REQ-048, REQ-064), y si el cliente pudiera influir en el, la auditoria dejaria de valer.

        Sin contexto se lanza `NotAuthenticated`, que el manejador global traduce al 401 uniforme;
        en la practica no deberia ocurrir, porque `IsAuthenticated` y la guardia de administrador ya
        han denegado antes, pero el caso de uso no asume su propia proteccion.
        """

        actor = request.auth if isinstance(request.auth, ContextoSesion) else obtener_contexto()
        if actor is None:
            raise NotAuthenticated()

        return actor


class RolUsuarioView(VistaRolUsuarioBase):
    """
    Cambia el rol funcional vigente de un usuario (EP-011, `PUT /api/users/{userId}/role`).

    RUTA PROTEGIDA Y RESERVADA AL ADMINISTRADOR. `users/<id>/role` NO esta en
    `apps.core_security.rutas_publicas`, asi que el `SesionRequeridaMiddleware` responde 401 sin
    sesion valida; encima va la guardia declarativa, que exige rol ADMINISTRADOR Y fila en la matriz
    `permiso_rol_operacion`. Un EMPLEADO o un TECNICO_MANTENIMIENTO reciben 403 con el mensaje
    uniforme ANTES de que el cuerpo se ejecute y, por tanto, ANTES de tocar la base de datos: la
    respuesta es identica exista o no el usuario pedido, de modo que no revela la existencia del
    recurso (AC-ROL-06).

    EL ROL NO SE COMPRUEBA EN EL CUERPO DEL ENDPOINT. `put` no mira `actor.role_code` en ningun
    punto: ese `if` duplicaria en codigo una decision que ya vive en la matriz y acabaria
    divergiendo de ella. La UNICA lectura del rol que hay aguas abajo es la regla de negocio de
    REQ-043 RN-03 -nadie se retira a si mismo la administracion-, que vive en el servicio y no es
    una comprobacion de permisos.

    ESTA VISTA NO COMPONE CUERPOS DE ERROR: deja PROPAGAR los `ErrorDominio` de `roles.errores`
    -`RolNoValidoError` 400, `UsuarioNoEncontradoError` 404, `UsuarioDeBajaError` y `MismoRolError`
    409, `AutorretiradaRolAdministracionError` 422- y las `ValidationError` de DRF (400), que el
    manejador unico traduce al cuerpo `{code, message, details, traceId}`.

    `http_method_names` se acota al UNICO verbo que el contrato publica en este path mas `options`:
    cualquier otro es 405 sin llegar al cuerpo. Sobre el rol de un usuario no hay borrado publicado
    en `openapi.yaml`, porque un usuario no puede quedarse sin rol: se le cambia por otro.

    LOS ESQUEMAS `RoleChangeRequest` y `UserRoleDetail` figuran en `openapi.yaml` como GERMENES
    (`x-mind-placeholder: true`): el contrato NO se edita y el modelo real lo fijan los
    serializadores de este modulo, de modo que lo que publica drf-spectacular y lo que devuelve el
    endpoint son lo mismo.
    """

    http_method_names = ["put", "options"]
    # La autenticacion la fija el default del proyecto (`AutenticacionSesionOpaca` en
    # `REST_FRAMEWORK`); los permisos se declaran EXPLICITOS para que leer la vista baste para saber
    # que exige sesion valida Y rol ADMINISTRADOR con `USER_MANAGE` en la matriz.
    permission_classes = [permissions.IsAuthenticated, requiere_administrador(OPERACION_GESTION_USUARIOS)]

    @extend_schema(
        operation_id="EP-011",
        summary="Cambia el rol funcional vigente de un usuario",
        tags=["ARC-012"],
        request=RoleChangeRequestSerializer,
        responses={200: UserRoleDetailSerializer},
    )
    def put(self, request: Request, user_id: int) -> Response:
        """
        Cambia el rol del usuario `user_id` y devuelve con 200 el desenlace (EP-011, REQ-005).

        EL `role_changed_by` SALE DEL CONTEXTO DE SESION Y JAMAS DEL PAYLOAD (REQ-048, REQ-064). Del
        cuerpo se lee exactamente un dato -el codigo del rol nuevo-, que es el unico que
        `RoleChangeRequestSerializer` declara; el administrador que ejecuta el cambio llega por otro
        camino, el `ContextoSesion` que resuelve `_actor_de`. Aceptar un `changedBy` del payload
        seria dejar que el cliente firme la auditoria con el nombre de otro.

        AQUI NO SE ABRE TRANSACCION. La frontera transaccional es del servicio, que escribe en UNA
        sola `transaction.atomic()` el asiento del historico, el rol vigente de la fila del usuario
        y la marca de recarga de las sesiones vivas: o caen los tres o no cae ninguno. Envolverlo
        tambien desde aqui no aniadiria atomicidad y solo repartiria entre dos capas una decision
        que es del caso de uso.

        LA VALIDACION DE FORMA OCURRE EN EL SERIALIZADOR y no aqui porque es la unica capa que puede
        atribuir el fallo a un CAMPO concreto y devolver su literal en el 400; las reglas que
        necesitan el ESTADO del usuario las decide el servicio dentro de su transaccion.

        Args:
            request: peticion ya autenticada; su cuerpo es el `RoleChangeRequest` del contrato.
            user_id: usuario al que se le cambia el rol, tomado del path.

        Returns:
            Response: 200 con el `UserRoleDetail` del cambio ya confirmado. El 200 es el de la
            columna Status del contrato para EP-011; la vista no elige codigo propio.
        """

        entrada = RoleChangeRequestSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        actor = self._actor_de(request)

        resultado = ServicioRolUsuario().cambiar(user_id, entrada.a_datos(), actor=actor)
        return Response(UserRoleDetailSerializer(resultado).data)


class HistoricoRolUsuarioView(VistaRolUsuarioBase):
    """
    Consulta el historico de rol de un usuario (EP-012, `GET /api/users/{userId}/role-history`).

    EL HISTORICO ES INMUTABLE, Y `http_method_names` MATERIALIZA LA MITAD ESTRUCTURAL DE AC-ROL-05.
    Al acotarlo a un UNICO verbo de lectura mas `options`, todo intento de modificarlo o borrarlo
    -`PUT`, `PATCH`, `POST` o `DELETE` sobre `users/<id>/role-history`- responde 405 SIN LLEGAR
    SIQUIERA AL CUERPO DE LA VISTA, de modo que el historico queda intacto. Y por delante del 405,
    para quien no es ADMINISTRADOR, la guardia responde 403 (AC-ROL-06). La garantia NO depende solo
    de esto: en la base, `RegistroInmutableMixin` en el modelo y el trigger
    `trg_usuario_historico_inmutable` rechazan con ORA-20001 cualquier `UPDATE` o `DELETE` sobre
    `usuario_historico`, de modo que ninguna via -ni esta ni un comando ni una consola- puede
    reescribir un movimiento ya ocurrido.

    RUTA PROTEGIDA Y RESERVADA AL ADMINISTRADOR. `users/<id>/role-history` NO esta en
    `apps.core_security.rutas_publicas`, asi que el `SesionRequeridaMiddleware` responde 401 sin
    sesion valida; encima va la guardia declarativa, que exige rol ADMINISTRADOR Y fila en la matriz
    `permiso_rol_operacion`. Un EMPLEADO o un TECNICO_MANTENIMIENTO reciben 403 con el mensaje
    uniforme ANTES de que el cuerpo se ejecute y, por tanto, ANTES de tocar la base de datos: la
    respuesta es identica exista o no el usuario pedido, de modo que no revela la existencia del
    recurso (AC-ROL-06).

    EL ROL NO SE COMPRUEBA EN EL CUERPO DEL ENDPOINT: `get` no mira `actor.role_code` en ningun
    punto, porque esa decision vive entera en la matriz. Esta vista TAMPOCO compone cuerpos de
    error: deja PROPAGAR el `UsuarioNoEncontradoError` del servicio (404) y las `ValidationError` de
    DRF (400, filtros o paginacion fuera de rango), que el manejador unico traduce al cuerpo
    `{code, message, details, traceId}`.

    EL ESQUEMA `RoleHistoryPage` figura en `openapi.yaml` como GERMEN (`x-mind-placeholder: true`):
    el contrato NO se edita y el modelo real lo fija `RoleHistoryPageSerializer`, de modo que lo que
    publica drf-spectacular y lo que devuelve el endpoint son lo mismo.
    """

    http_method_names = ["get", "options"]
    # La autenticacion la fija el default del proyecto (`AutenticacionSesionOpaca` en
    # `REST_FRAMEWORK`); los permisos se declaran EXPLICITOS para que leer la vista baste para saber
    # que exige sesion valida Y rol ADMINISTRADOR con `USER_MANAGE` en la matriz.
    permission_classes = [permissions.IsAuthenticated, requiere_administrador(OPERACION_GESTION_USUARIOS)]

    @extend_schema(
        operation_id="EP-012",
        summary="Consulta el histórico inmutable de asignaciones y cambios de rol de un usuario",
        tags=["ARC-012"],
        parameters=[RoleHistoryQuerySerializer],
        responses={200: RoleHistoryPageSerializer},
    )
    def get(self, request: Request, user_id: int) -> Response:
        """
        Valida la query string y devuelve con 200 la pagina pedida del historico (EP-012, REQ-008).

        LA VALIDACION DE LOS FILTROS OCURRE AQUI y no en el repositorio: esta es la unica capa que
        puede responder un 400 de CAMPO diciendo QUE parametro esta mal -un rango de fechas cruzado,
        un `pageSize` fuera de rango-. Aguas abajo, `CriteriosHistoricoRol` viaja ya validado e
        inmutable y ninguna capa lo retoca.

        CERO MOVIMIENTOS ES UN 200 CON `items` VACIO Y `emptyMessage`, NUNCA UN ERROR. La peticion es
        correcta y simplemente no hay asientos que mostrar -tampoco cuando la pagina pedida esta por
        encima del total-; un 404 ahi obligaria al cliente a distinguir «no existe el recurso» de «no
        hay resultados», que son cosas distintas. El 404 queda reservado a lo que de verdad no
        existe: el usuario.

        Args:
            request: peticion ya autenticada; su query string es la de EP-012 en el contrato.
            user_id: usuario cuyo historico de rol se consulta, tomado del path.

        Returns:
            Response: 200 con el `RoleHistoryPage` correspondiente a los criterios recibidos.
        """

        consulta = RoleHistoryQuerySerializer(data=request.query_params)
        consulta.is_valid(raise_exception=True)

        pagina = ServicioRolUsuario().consultar_historico(user_id, consulta.a_criterios())
        return Response(RoleHistoryPageSerializer(pagina).data)
