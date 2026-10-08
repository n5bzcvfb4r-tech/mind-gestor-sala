"""
Vista de los permisos efectivos (EP-004, `GET /api/auth/permissions`).

RUTA PROTEGIDA. No se declara en `rutas_publicas`, de modo que el guardia de sesion
(`SesionRequeridaMiddleware`) la exige por defecto: sin sesion valida no se llega aqui.

La vista no evalua la matriz: se limita a resolver la identidad del contexto de sesion y a
delegar en `ServicioPermisos`, que es el UNICO evaluador de `permiso_rol_operacion` (ARC-102).
"""

from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.exceptions import NotAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.contexto import ContextoSesion
from apps.core_security.serializers.permisos import EffectivePermissionsSerializer
from apps.core_security.servicios.permisos import ServicioPermisos


class PermisosEfectivosView(APIView):
    """
    Devuelve las operaciones permitidas y el alcance de datos del rol vigente (EP-004).

    SOLO EL USUARIO DE LA SESION (REQ-020). El recurso NO admite ningun parametro de usuario:
    cualquier `userId` que llegue en la query string se ignora EN SILENCIO, sin 400 y sin
    error, porque el rol se toma del contexto de sesion y la vista no tiene forma ESTRUCTURAL
    de leer otro. No hay rama que consulte `request.query_params`, `request.data` ni ninguna
    cabecera para decidir de quien son los permisos. Asi el endpoint no se puede usar para
    espiar los permisos de otra persona: ignorar el parametro es mas seguro que rechazarlo,
    porque un 400 selectivo ya seria un oraculo sobre que identificadores existen.

    NUNCA LA MATRIZ COMPLETA (AC-ROL-04). La respuesta se limita a `role_code`,
    `allowed_operations` y `data_scope` del rol vigente de la sesion. No se publican permisos
    de terceros ni el contenido completo de `permiso_rol_operacion`: conocer la matriz entera
    es conocer la superficie de ataque del sistema, y nadie necesita eso para pintar su
    propia pantalla.

    USABILIDAD, NO SEGURIDAD (REQ-009, REQ-013, REQ-032). La SPA usa esta respuesta para
    OCULTAR o DESHABILITAR controles, nada mas. La decision VINCULANTE la sigue tomando el
    backend en cada peticion contra la matriz: una operacion que no aparezca aqui se deniega
    igualmente con 403 si se invoca por API saltandose la SPA, y que aparezca aqui no
    autoriza nada por si solo. Tratar este cuerpo como una concesion de permisos seria
    convertir al cliente en el punto de control, que es justo lo que ARC-102 prohibe.

    SIN `operation_code` PROPIO, Y POR QUE. Las demas vistas del servicio declaran su guardia
    `requiere("<OPERATION_CODE>")` contra la matriz. Esta NO lo hace porque el catalogo
    `cat_operacion` que siembra Liquibase (`INCIDENT_CREATE`, `INCIDENT_LIST_OWN`,
    `INCIDENT_LIST_ALL`, `INCIDENT_VIEW`, `INCIDENT_HISTORY_VIEW`, `INCIDENT_ASSIGN_SELF`,
    `INCIDENT_STATUS_CHANGE`, `INCIDENT_CLOSE_WITH_COMMENT`, `USER_MANAGE`) NO contiene
    ninguna operacion para consultar los permisos propios, y aqui NO se inventan codigos de
    catalogo: un `operation_code` que no exista en la matriz denegaria SIEMPRE, y sembrar uno
    nuevo por cuenta de esta vista romperia que los catalogos los manda la base de datos.

    Es, por tanto, un recurso de AUTOSERVICIO sobre la propia sesion, disponible para
    cualquier usuario autenticado con rol resoluble: el 401 lo da el guardia de sesion y el
    403 lo da la resolucion fail-closed del rol (usuario inactivo, sin rol o con rol fuera de
    `cat_rol`), ambos ya existentes y POR DELANTE de esta vista.

    SOLO LECTURA. `http_method_names` se acota a `get` y `options`: cualquier otro verbo es
    405. Este recurso no crea, no modifica y no borra nada; los permisos se administran contra
    la matriz, no contra este endpoint.
    """

    http_method_names = ["get", "options"]
    # La autenticacion la fija el default del proyecto (`AutenticacionSesionOpaca` en
    # `REST_FRAMEWORK`); el permiso se declara EXPLICITO para que leer la vista baste para
    # saber que exige sesion.
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="EP-004",
        summary="Devuelve las operaciones permitidas y el alcance de datos del rol vigente",
        tags=["ARC-012"],
        responses={200: EffectivePermissionsSerializer},
    )
    def get(self, request: Request) -> Response:
        """Resuelve el rol vigente del contexto de sesion y devuelve sus permisos efectivos con 200."""

        contexto = self._contexto_de_la_sesion(request)
        permisos = ServicioPermisos().efectivos(contexto.role_code)
        return Response(EffectivePermissionsSerializer(permisos).data, status=status.HTTP_200_OK)

    def _contexto_de_la_sesion(self, request: Request) -> ContextoSesion:
        """
        Devuelve el `ContextoSesion` que dejo el guardia de sesion, o falla con 401.

        La identidad se lee SOLO de la peticion ya autenticada: primero de
        `request.contexto_sesion`, que es donde la publica `SesionRequeridaMiddleware`, y en su
        defecto de `request.auth`, que es donde la deja `AutenticacionSesionOpaca` cuando la
        vista se ejerce sin pasar por el middleware. Jamas del cuerpo, de la query string ni
        de una cabecera: el actor no se autodeclara (REQ-064).

        Sin contexto se lanza `NotAuthenticated`, que el manejador global traduce al 401
        uniforme (`AUTH_SESSION_INVALID`, `mensajes.SESION_REQUERIDA`); la vista no compone
        ningun cuerpo de error propio.
        """

        contexto = getattr(request, "contexto_sesion", None)
        if not isinstance(contexto, ContextoSesion):
            contexto = request.auth if isinstance(request.auth, ContextoSesion) else None

        if contexto is None:
            raise NotAuthenticated()

        return contexto
