"""
Capa DRF de consumo del alcance de datos: la UNICA via por la que una vista alcanza una incidencia.

Este modulo es a `servicios/alcance.py` lo que `permisos.py` es a `servicios/permisos.py`: la
cara HTTP de un motor que vive en el servicio. Aqui NO se decide nada y NO se reimplementa
nada; se delega siempre en `ServicioAlcance`, que es quien construye el predicado SQL y quien
lanza la denegacion uniforme.

TRES COSAS QUE ESTE MODULO DEJA ESCRITAS
----------------------------------------

1. EL 404 UNIFORME NO SE COMPONE AQUI (REQ-023, AC-PERM-04).
   Esta capa no construye NINGUNA respuesta de error. Cuando el recurso cae fuera del alcance
   se deja propagar `RecursoFueraDeAlcanceError` y es el MANEJADOR UNICO
   (`manejadores.manejador_excepciones`, declarado en `REST_FRAMEWORK["EXCEPTION_HANDLER"]`)
   quien la serializa al cuerpo canonico `{code, message, details, traceId}` con status 404.
   Asi el cuerpo, el `code` (`INC_NOT_FOUND`), el texto y las CABECERAS de una denegacion por
   alcance son identicos a los de cualquier otra respuesta del mismo codigo, y ninguna ruta
   puede desviarse por su cuenta.

   ANTI-PATRON PROHIBIDO: `return Response({...}, status=404)` escrito a mano en una vista.
   Un cuerpo compuesto en la vista se desincroniza del canonico en cuanto cambie el formato de
   error -le faltara el `traceId`, o traera un `code` distinto- y entonces el cliente podra
   DISTINGUIR por la forma de la respuesta el 404 de "no es tuya" del 404 de "no existe", que
   es exactamente el oraculo de enumeracion que REQ-023 cierra. Lo mismo vale para
   `get_object_or_404` y para `raise Http404`: ninguno pasa por el alcance.

2. EL ORDEN IMPORTA: PERMISO ANTES QUE CUERPO (REQ-022, validacion 1).
   DRF evalua `permission_classes` en `APIView.initial()`, es decir, ANTES de ejecutar el
   `get`/`post` de la vista. Por tanto la guardia `requiere("<OPERATION_CODE>")` decide contra
   la matriz `permiso_rol_operacion` y publica el `data_scope` de ESA operacion, y solo despues
   el cuerpo de la vista llega a este mixin a acotar. De ahi se siguen dos propiedades:

   * Al no autorizado se le responde 403 ANTES de que ninguna validacion de flujo se ejecute,
     de modo que nunca recibe un error de validacion de la maquina de estados ("no se puede
     cerrar una incidencia abierta sin asignar", "transicion no permitida") que le describiria
     gratis como funciona el motor que no puede usar.
   * Este mixin puede dar por hecho que el `data_scope` del contexto es el de la operacion en
     curso: no lo recalcula ni lo adivina.

3. ESTE MIXIN NO AUTORIZA: SOLO ACOTA (REQ-022, regla 2).
   ALCANCE DE DATOS NO ES ALCANCE DE GESTION. Que un usuario sea el reportante de una
   incidencia le da VISIBILIDAD sobre ella, no capacidad de gestionarla: autoasignarsela,
   cambiarle el estado o cerrarla se los deniega la MATRIZ a traves de la guardia
   `requiere(...)`, no este modulo. Es un reparto deliberado: si el mixin tambien decidiera
   quien puede gestionar, habria dos sitios donde se autoriza y acabarian discrepando. Aqui
   solo se recorta lo que YA fue autorizado.
"""

from __future__ import annotations

from django.db.models import Model, QuerySet
from rest_framework.request import Request

from apps.core.contexto import ContextoSesion
from apps.core.models.transaccional import IncidenciaAdjuntoEntity, IncidenciaEntity
from apps.core_security.errores import SesionInvalidaError
from apps.core_security.servicios.alcance import ServicioAlcance


#: Motivo tecnico de la traza cuando una vista llega a esta capa sin contexto publicado. Es
#: traza interna: el manejador unico responde el 401 con el texto uniforme y nunca este motivo.
MOTIVO_SIN_CONTEXTO = "contexto_ausente_en_peticion"


def contexto_de_la_peticion(request: Request) -> ContextoSesion:
    """
    Devuelve el `ContextoSesion` que la guardia publico en la peticion.

    Lo busca primero en `request.contexto_sesion` -donde lo dejan `SesionRequeridaMiddleware` y,
    ya con el `data_scope` de la operacion, la guardia `PermisoOperacion`- y, como respaldo, en
    `request.auth`, que es donde lo publica `AutenticacionSesionOpaca` cuando la vista se ejerce
    sin atravesar el middleware. Nunca del cuerpo, de la query string ni de una cabecera: el
    actor no se autodeclara (REQ-064).

    FAIL-CLOSED. Si no hay contexto NO se inventa uno, NO se asume un usuario anonimo y, sobre
    todo, NO se degrada el alcance: se lanza `SesionInvalidaError`, que el manejador unico
    traduce al 401 uniforme (`AUTH_SESSION_INVALID`, `mensajes.SESION_REQUERIDA`). La
    alternativa tentadora -seguir adelante sin acotar, o acotar a un `user_id` por defecto- es
    la que convierte un fallo de cableado en una fuga de datos.

    Una vista que llegue aqui sin contexto es una vista MAL CABLEADA (le falta la guardia, o la
    ruta se declaro publica por error), no una peticion anonima legitima: una peticion anonima
    de verdad ya habria muerto antes, en el guardia de sesion. Por eso el 401 es a la vez la
    respuesta correcta para el cliente y una senial inequivoca en el log para quien mantiene el
    enrutado.
    """

    contexto = getattr(request, "contexto_sesion", None)
    if not isinstance(contexto, ContextoSesion):
        auth = getattr(request, "auth", None)
        contexto = auth if isinstance(auth, ContextoSesion) else None

    if contexto is None:
        raise SesionInvalidaError(motivo=MOTIVO_SIN_CONTEXTO)

    return contexto


class AlcanceRecursoMixin:
    """
    Mixin de `APIView` que acota TODA lectura de incidencia al alcance del rol vigente.

    Es la UNICA via por la que una vista debe alcanzar una incidencia, su historial o su
    adjunto. Una vista que consulte `IncidenciaEntity.objects` por su cuenta se salta el
    predicado de propiedad y sirve datos ajenos: por eso el mixin expone los cuatro accesos que
    cualquier endpoint de lectura necesita (`incidencia_en_alcance`, `historial_en_alcance`,
    `adjunto_en_alcance` y `consulta_acotada`) y no deja ningun hueco que invite a improvisar
    otro.

    QUE HACE Y QUE NO HACE. Resuelve el contexto de la peticion y delega en `ServicioAlcance`;
    no construye predicados, no filtra en memoria, no compone respuestas de error y NO
    AUTORIZA. La autorizacion la decide antes la guardia `requiere(...)` contra la matriz
    `permiso_rol_operacion`; aqui solo se recorta lo ya autorizado (REQ-022, regla 2): ser el
    reportante da visibilidad, nunca capacidad de gestion.

    LA DENEGACION SE PROPAGA, NO SE RESPONDE. Fuera de alcance, inexistente e identificador mal
    formado lanzan `RecursoFueraDeAlcanceError` y salen por el manejador unico como el MISMO
    404 canonico `{code, message, details, traceId}` (REQ-023, AC-PERM-04). Esta PROHIBIDO
    capturarla en la vista para componer un `Response({...}, status=404)` propio.

    ESTA TAREA NO MONTA RUTAS. El contrato `openapi.yaml` asigna los endpoints de detalle,
    historial y adjunto (EP-028 / EP-029 / EP-030) a otras TSK; este mixin viaja como la pieza
    que esas tareas deben heredar. Uso esperado, con la guardia declarada::

        class IncidenciaDetalleView(AlcanceRecursoMixin, APIView):
            permission_classes = [IsAuthenticated, requiere("INCIDENT_VIEW")]

            def get(self, request, incident_id):
                incidencia = self.incidencia_en_alcance(request, incident_id)
                return Response(IncidenciaDetalleSerializer(incidencia).data)

    Dos detalles de ese ejemplo que NO son accidentales:

    * El `incident_id` llega por la URL en crudo, tal y como lo escribio el cliente, y no se
      valida en la vista: lo normaliza `identificador_normalizado` dentro del servicio. Un
      identificador no numerico NO produce un 400 ni un 404 por una via mas corta; recorre
      exactamente el mismo camino y la misma consulta que una incidencia ajena, para que el
      tiempo de respuesta no delate cual de los dos casos es (AC-PERM-04, PBT-006).
    * Un `reporterUserId` en la query string se IGNORA EN SILENCIO: el propietario contra el
      que se acota sale SIEMPRE de la sesion (`propietario_efectivo`, REQ-064). No se valida,
      no se compara y no provoca error, porque un 400 selectivo ya confirmaria que el parametro
      significa algo para el servidor.

    El orden de bases importa: `AlcanceRecursoMixin` va ANTES que `APIView` para que sus
    metodos no queden tapados por la clase base.
    """

    #: Instancia reutilizable del motor de alcance. `ServicioAlcance` NO guarda estado entre
    #: llamadas -toda la decision entra por el `ContextoSesion` de cada peticion-, asi que
    #: compartir una sola instancia a nivel de clase es seguro y evita construir una por
    #: peticion. Se declara como atributo para que una vista pueda sustituirla en pruebas sin
    #: parchear el modulo.
    servicio_alcance: ServicioAlcance = ServicioAlcance()

    def contexto(self, request: Request) -> ContextoSesion:
        """
        Devuelve el contexto de sesion de la peticion, con el `data_scope` ya resuelto.

        Es un unico punto de entrada a proposito: todos los accesos de este mixin pasan por
        aqui, de modo que la politica fail-closed de `contexto_de_la_peticion` se aplica igual
        al detalle, al historial, al adjunto y al listado. El `data_scope` que trae el contexto
        es el que la guardia obtuvo de la matriz para ESTA operacion; el mixin no lo recalcula.
        """

        return contexto_de_la_peticion(request)

    def incidencia_en_alcance(self, request: Request, identificador: object) -> IncidenciaEntity:
        """
        Devuelve la incidencia pedida SI esta dentro del alcance; si no, deja propagar el 404.

        Delega integramente en `ServicioAlcance.incidencia_en_alcance`, que resuelve identidad y
        propiedad en UNA SOLA consulta. La vista recibe la entidad ya acotada y puede
        serializarla sin volver a comprobar nada: no debe anadir un `if incidencia.reported_by_id
        != request.user.id` detras, porque esa comprobacion en memoria es justo lo que el
        predicado SQL vino a sustituir.
        """

        return self.servicio_alcance.incidencia_en_alcance(identificador, self.contexto(request))

    def historial_en_alcance(self, request: Request, identificador: object) -> QuerySet:
        """
        Devuelve el historial de la incidencia pedida, previa resolucion de SU alcance.

        El historial no tiene alcance propio: hereda el de su incidencia (REQ-023 regla 3). El
        servicio resuelve primero la incidencia y solo despues lee sus entradas, de forma que la
        trazabilidad de una incidencia ajena -que lleva nombres de actores y comentarios- no se
        puede extraer por la puerta de atras.

        Devuelve un QUERYSET sin evaluar: la ordenacion y la paginacion de la vista se resuelven
        en la base, encima del conjunto ya acotado. Un historial vacio NO es una denegacion.
        """

        return self.servicio_alcance.historial_en_alcance(identificador, self.contexto(request))

    def adjunto_en_alcance(self, request: Request, identificador: object) -> IncidenciaAdjuntoEntity:
        """
        Devuelve el adjunto de la incidencia pedida, previa resolucion de SU alcance.

        El adjunto NUNCA se busca por su propio identificador (REQ-027 regla 3): el servicio
        resuelve antes la incidencia y lee el adjunto por `incident_id`, de modo que un
        identificador de adjunto adivinado no sirve para saltarse el alcance. La vista recibe la
        entidad; si la incidencia esta en alcance pero no tiene adjunto, el servicio deniega con
        el MISMO 404 uniforme, por indistinguibilidad.
        """

        return self.servicio_alcance.adjunto_en_alcance(identificador, self.contexto(request))

    def consulta_acotada(self, request: Request, *, modelo: type[Model] | None = None) -> QuerySet:
        """
        Devuelve el queryset de incidencias YA acotado por el alcance de la sesion.

        Punto de partida obligatorio de cualquier listado: quien cuente, ordene o pagine sobre
        lo que devuelve esta operando sobre el conjunto acotado, nunca sobre el total del
        sistema (AC-ALC-01, AC-PERM-02). Filtrar despues en Python descuadraria el total y la
        paginacion, ademas de traer a memoria filas que el solicitante no puede ver.

        `modelo` permite reutilizar el motor con otra entidad que comparta el campo propietario;
        por defecto es `IncidenciaEntity`. El queryset sale PEREZOSO: la vista puede encadenarle
        `order_by`, `filter` adicionales y paginacion, y el predicado de alcance seguira estando
        en la misma sentencia SQL.
        """

        return self.servicio_alcance.consulta_acotada(self.contexto(request), modelo=modelo)
