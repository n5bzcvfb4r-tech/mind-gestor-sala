"""
Guardia declarativa de autorizacion de la superficie HTTP (REQ-018, AC-G-04).

Materializa la regla del arquetipo: CADA OPERACION DE LA API DECLARA SU `operation_code` del
catalogo `cat_operacion` MEDIANTE UNA GUARDIA DEL ROUTER. La decision se declara una sola vez,
junto a la vista, y la evalua `ServicioPermisos` contra la matriz `permiso_rol_operacion`.

ANTI-PATRON PROHIBIDO. No se comprueba el rol dentro del cuerpo del endpoint
(`if actor.role_code != "...": raise 403`) ni se codifica la matriz como cadenas de `if/elif`
en los servicios. Si una comprobacion de rol aparece en el cuerpo de una vista, la matriz deja
de ser la fuente unica de verdad y el 403 deja de coincidir con ella.

DENY BY DEFAULT EN DOS CAPAS:

* Una vista con guardia pero SIN `operation_code` declarado NO autoriza: se registra el
  defecto y se deniega. Una declaracion incompleta nunca abre un endpoint.
* Una vista SIN guardia y no declarada publica queda denegada por delante, en el guardia de
  sesion (`SesionRequeridaMiddleware` + `rutas_publicas`), que exige sesion valida en toda ruta
  no exenta.

ALCANCE DE ESTA TAREA. Este modulo NO monta rutas ni registra la guardia en ninguna vista: la
tabla rol x operacion viaja como CONTEXTO. La guardia la consumen las TSK propietarias de cada
endpoint, que son las que declaran su `operation_code` en `permission_classes`.
"""

import logging

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.core.contexto import ContextoSesion, establecer_contexto
from apps.core_security.servicios.permisos import ServicioPermisos


logger = logging.getLogger(__name__)


class PermisoOperacion(BasePermission):
    """
    Guardia base de autorizacion: sin `operation_code` declarado NO autoriza (deny-by-default).

    No se usa directamente: cada vista declara su operacion con la fabrica `requiere(...)`, que
    produce una subclase con el `operation_code` del catalogo `cat_operacion` ya fijado.

    Ademas de decidir, publica en la peticion el contexto de sesion CON el alcance de datos que
    la matriz concede a esa operacion, para que el repositorio lo aplique como predicado SQL.
    """

    operation_code: str | None = None

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        Autoriza la peticion contra la matriz `permiso_rol_operacion` y fija el alcance de datos.

        Deja propagar `PermisoDenegadoError`: el manejador global de DRF la traduce al 403
        canonico `{code: "PERM_DENIED", message, details, traceId}`. Por eso aqui NO se captura
        la denegacion ni se devuelve `False` en ese caso: devolver `False` daria el 403 generico
        de DRF, con otro `code`, y la respuesta dejaria de coincidir con la matriz.
        """

        if self.operation_code is None:
            logger.error(
                "Vista sin operation_code declarado en su guardia de autorizacion: se deniega (deny-by-default)",
                extra={"vista": type(view).__name__},
            )
            return False

        contexto = getattr(request, "contexto_sesion", None)
        if not isinstance(contexto, ContextoSesion):
            auth = getattr(request, "auth", None)
            contexto = auth if isinstance(auth, ContextoSesion) else None

        if contexto is None:
            # Sin identidad no hay nada que autorizar: DRF resuelve el 401/403 por su camino
            # habitual (clase de autenticacion + `IsAuthenticated`), que es quien sabe si falta
            # la credencial o si es invalida.
            return False

        contexto_con_alcance = ServicioPermisos().alcance_de(contexto, self.operation_code)

        # El `ContextoSesionMiddleware` publico el contexto SIN alcance, porque cuando corrio
        # todavia no se sabia que operacion se iba a ejecutar. Ahora que ya se conoce, se
        # republica el contexto con el alcance de ESA operacion. Es seguro: aquel middleware
        # guarda su token y restaura en un `finally`, asi que este `set` posterior se deshace al
        # terminar la peticion y no filtra contexto entre peticiones ni entre tareas.
        request.contexto_sesion = contexto_con_alcance
        establecer_contexto(contexto_con_alcance)

        return True


def requiere(operation_code: str) -> type[PermisoOperacion]:
    """
    Fabrica la clase de permiso de DRF que exige el `operation_code` indicado.

    El codigo debe ser uno de `cat_operacion`, que siembra Liquibase: aqui no se valida contra
    un enum de Python porque el catalogo se lee de la base. Si el codigo no existe en la matriz
    para el rol del actor, no habra fila y la peticion se denegara (deny by default).

    Uso en una `APIView` del repo::

        class IncidenciasListView(APIView):
            permission_classes = [IsAuthenticated, requiere("INCIDENT_LIST_ALL")]

    Devuelve una subclase NUEVA en cada llamada, de modo que dos vistas con operaciones
    distintas nunca comparten estado de clase.
    """

    return type(f"PermisoOperacion{operation_code}", (PermisoOperacion,), {"operation_code": operation_code})
