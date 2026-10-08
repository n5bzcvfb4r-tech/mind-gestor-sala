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
from apps.core_security.errores import PermisoDenegadoError
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

        contexto = self._contexto_de(request)
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

    @staticmethod
    def _contexto_de(request: Request) -> ContextoSesion | None:
        """
        Resuelve el contexto de sesion de la peticion, o `None` si la peticion es anonima.

        Se busca primero en `request.contexto_sesion`, que es donde lo publica el
        `ContextoSesionMiddleware`, y en su defecto en `request.auth`, que es donde lo deja la
        clase de autenticacion de DRF. Los dos caminos existen porque el contexto puede haberse
        establecido antes de entrar en DRF (middleware) o durante su fase de autenticacion.

        Esta extraido como metodo auxiliar -y no repetido en cada guardia- para que TODAS las
        guardias de este modulo vean exactamente la misma identidad. Si cada subclase resolviera
        el contexto por su cuenta, bastaria una divergencia menor (mirar solo uno de los dos
        sitios) para que una guardia autorizase sobre un actor distinto del que audita el resto
        del sistema.

        No lanza ni decide: devolver `None` significa "no hay identidad que evaluar" y es la
        llamante quien decide que hacer con ello.
        """

        contexto = getattr(request, "contexto_sesion", None)
        if isinstance(contexto, ContextoSesion):
            return contexto

        auth = getattr(request, "auth", None)
        return auth if isinstance(auth, ContextoSesion) else None


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


#: Codigo del rol con potestad sobre el censo de usuarios, tal y como lo siembra Liquibase en
#: `cat_rol`. Es el UNICO literal de rol del servicio y vive aqui, junto a la guardia que lo
#: aplica, precisamente para que no se disperse por los cuerpos de los endpoints: ese reparto
#: -y no la existencia del literal- es el anti-patron que prohibe la guia del proyecto.
ROL_ADMINISTRADOR = "ADMINISTRADOR"


class PermisoAdministrador(PermisoOperacion):
    """
    Guardia de las operaciones reservadas al rol ADMINISTRADOR (REQ-044 RN-01, REQ-083 RN-03).

    QUE EXIGE. Las dos cosas, en este orden: que el rol vigente sea `ADMINISTRADOR` y, ademas,
    que la matriz `permiso_rol_operacion` autorice el par (rol, operacion). Las operaciones de
    alta, edicion, cambio de rol, desactivacion, listado y detalle de usuarios -y la vista de
    verificacion de destinatarios- solo son ejecutables por un ADMINISTRADOR CON INDEPENDENCIA
    DEL CANAL: como la decision se toma en la guardia de la API REST, la interfaz y cualquier
    cliente externo obtienen exactamente la misma respuesta, porque atraviesan el mismo cerrojo.

    POR QUE ESTO NO ES EL ANTI-PATRON QUE PROHIBE LA GUIA. La guia veta comprobar el rol DENTRO
    DEL CUERPO DEL ENDPOINT y codificar la matriz como cadenas de `if/elif` por rol. Aqui no
    ocurre ninguna de las dos cosas:

    * La matriz SIGUE SIENDO LA FUENTE UNICA DE VERDAD de que autoriza cada par rol x operacion.
      Esta guardia no la sustituye ni la replica: la ENCADENA. Quien decide si la operacion esta
      permitida, y con que `data_scope`, sigue siendo `ServicioPermisos` leyendo Oracle.
    * El literal `ADMINISTRADOR` aparece UNA SOLA VEZ en todo el servicio, en `ROL_ADMINISTRADOR`
      de este modulo de seguridad y declarado junto a la guardia que lo usa. Lo que la guia
      persigue es el literal repetido en veinte vistas, que es lo que hace imposible auditar
      quien puede que; una constante unica en el modulo de autorizacion es lo contrario de eso.
    * No hay cadena de condicionales por rol: hay UNA restriccion adicional, exigida de forma
      explicita por REQ-044 RN-01, que pide que la gestion del censo quede cerrada al rol
      ADMINISTRADOR sea cual sea el canal.

    POR QUE PRIMERO EL ROL Y DESPUES LA MATRIZ. El orden es deliberado. Al comprobar el rol
    ANTES de delegar en `super()`, una fila mal sembrada en la matriz -por ejemplo, una que
    concediera `USER_MANAGE` a `EMPLEADO`- NO puede abrir el censo: el segundo cerrojo la frena
    antes de que la matriz llegue a opinar. Es la defensa en profundidad que pide RN-06 para que
    un error de siembra no exponga datos personales.

    ESTA GUARDIA NUNCA CONCEDE, SOLO RESTRINGE. Ser ADMINISTRADOR no autoriza nada por si mismo:
    si la matriz deniega el par (rol, operacion), la peticion se deniega igual, sea cual sea el
    rol. El unico efecto posible de esta clase sobre el resultado es convertir un "permitido por
    la matriz" en un "denegado"; jamas al reves.

    POR QUE LANZA Y NO DEVUELVE `False`. La denegacion por rol se propaga como
    `PermisoDenegadoError` para que el manejador global la traduzca al 403 canonico
    `{code: "PERM_DENIED", ...}` con el mensaje uniforme. Devolver `False` daria el 403 generico
    de DRF, con otro `code` y otro texto, y la respuesta dejaria de ser indistinguible de la que
    produce la propia matriz (REQ-078): el cliente podria separar "no eres administrador" de "la
    matriz no te lo permite" y usar la diferencia como oraculo.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        """
        Deniega si el rol vigente no es ADMINISTRADOR; en caso contrario delega en la matriz.

        Sin contexto de sesion devuelve `False` sin lanzar: no hay identidad que evaluar, y es
        DRF quien resuelve el 401 por su camino habitual (clase de autenticacion +
        `IsAuthenticated`), que es el unico que sabe si falta la credencial o si es invalida.
        Lanzar aqui un 403 convertiria un "no te has identificado" en un "no puedes".

        Cuando el rol SI es ADMINISTRADOR, la decision la toma `super()`: es la base la que
        consulta la matriz y la que republica el contexto con el `data_scope` que esa operacion
        concede, de modo que el repositorio siga aplicando el alcance como predicado SQL.
        """

        contexto = self._contexto_de(request)
        if contexto is None:
            return False

        if contexto.role_code != ROL_ADMINISTRADOR:
            # El `operation_code` y el `role_code` viajan como TRAZA INTERNA de la excepcion; el
            # cuerpo de la respuesta lo compone el manejador global con el mensaje uniforme, que
            # no revela cual de los dos cerrojos ha denegado.
            raise PermisoDenegadoError(operation_code=self.operation_code, role_code=contexto.role_code)

        return super().has_permission(request, view)


def requiere_administrador(operation_code: str) -> type[PermisoAdministrador]:
    """
    Fabrica la clase de permiso que exige rol ADMINISTRADOR ADEMAS del `operation_code` indicado.

    Es la gemela de `requiere(...)` y comparte su mecanica: el codigo debe ser uno de
    `cat_operacion`, que siembra Liquibase, y no se valida contra ningun enum de Python porque
    el catalogo se lee de la base. Si el par (rol, operacion) no tiene fila en la matriz, la
    peticion se deniega igual (deny by default); el rol ADMINISTRADOR no suple la fila que falta.

    Uso en una `APIView` del repo::

        class UsuariosListView(APIView):
            permission_classes = [IsAuthenticated, requiere_administrador("USER_MANAGE")]

    Se reserva para las operaciones que REQ-044 RN-01 y REQ-083 RN-03 cierran al censo de
    usuarios y a la verificacion de destinatarios. El resto de endpoints usan `requiere(...)`:
    anadir el cerrojo de rol donde la norma no lo pide duplicaria la matriz en codigo, que es
    justo lo que este modulo existe para evitar.

    Devuelve una subclase NUEVA en cada llamada, de modo que dos vistas con operaciones
    distintas nunca comparten estado de clase.
    """

    return type(f"PermisoAdministrador{operation_code}", (PermisoAdministrador,), {"operation_code": operation_code})
