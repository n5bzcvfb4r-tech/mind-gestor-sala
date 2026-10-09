"""
Vistas del restablecimiento administrativo de credencial (EP-017) y del listado de estado de
credencial (EP-019).

RUTAS PROTEGIDAS Y RESERVADAS AL ADMINISTRADOR. Ninguna se declara en `rutas_publicas`, de modo
que el guardia de sesion (`SesionRequeridaMiddleware`) exige sesion valida por defecto, y encima
llevan la guardia declarativa `requiere_administrador("USER_MANAGE")`, que ademas de exigir el
rol consulta la matriz `permiso_rol_operacion`. La autorizacion se declara AQUI, junto a cada
vista, y NUNCA dentro del cuerpo del endpoint: un `if actor.role_code != ...` es el anti-patron
que prohibe la guia del proyecto, porque convierte el codigo en una segunda fuente de verdad que
se desalinea de la matriz.

ERRORES. Ninguna de las dos vistas compone cuerpo de error propio: dejan PROPAGAR los
`ErrorDominio` del modulo y el manejador unico (`apps.core_security.manejadores`) los traduce al
cuerpo uniforme del servicio (`code`, `message`, `details`, `traceId`).
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.exceptions import NotAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.contexto import ContextoSesion, obtener_contexto
from apps.core.models import UsuarioEntity
from apps.core_security.permisos import requiere_administrador

# `OPERACION_GESTION_USUARIOS` se IMPORTA del modulo de bloqueo en vez de redeclararse: es el
# MISMO `operation_code` del catalogo `cat_operacion` (`USER_MANAGE`), ya sembrado por Liquibase y
# ya asociado unicamente al rol ADMINISTRADOR en `permiso_rol_operacion`. Restablecer una
# credencial y desbloquear una cuenta son dos operaciones de la misma potestad -la gestion del
# censo de usuarios-, asi que una segunda constante con el mismo literal solo crearia dos sitios
# donde corregir el dia que el catalogo cambie, con el riesgo de corregir uno y olvidar el otro.
from apps.identidad.bloqueo.views import OPERACION_GESTION_USUARIOS
from apps.identidad.reposicion.consulta import ServicioEstadoCredencial
from apps.identidad.reposicion.errores import UsuarioNoEncontradoError
from apps.identidad.reposicion.serializers import (
    CredentialStatusPageSerializer,
    CredentialStatusQuerySerializer,
    PasswordResetRequestSerializer,
    PasswordResetResultSerializer,
)
from apps.identidad.reposicion.servicio import ServicioReposicionCredencial


__all__ = ["EstadoCredencialView", "RestablecimientoPasswordView"]


class RestablecimientoPasswordView(APIView):
    """
    Restablece la credencial de un usuario activo emitiendole una temporal (EP-017, REQ-073).

    LO QUE EL RESTABLECIMIENTO NO ALTERA (AC-RST-08). `role_code`, `full_name`,
    `corporate_email`, `status` y el resto de atributos del usuario quedan IDENTICOS antes y
    despues de la operacion: lo unico que cambia son las columnas de CREDENCIAL
    (`password_hash`, `password_salt`, `password_algorithm`, `password_updated_at`,
    `password_expires_at`, `must_change_password`) y las SESIONES vivas, que se revocan
    (AC-PWD-06). Esa garantia no depende de que nadie se acuerde: la da el `update_fields`
    acotado de `ServicioCustodiaCredenciales`, que escribe exclusivamente esas columnas, de modo
    que un UPDATE sobre el rol o el correo no podria colarse ni por descuido.

    ERRORES QUE DEJA PROPAGAR, y lo que responde el manejador unico con cada uno:

    * `UsuarioNoEncontradoError` -> 404: el identificador del path no corresponde a ningun
      usuario.
    * `CuentaInactivaError` -> 409: el usuario existe pero esta de baja logica; emitirle una
      credencial no le devolveria el acceso, asi que responder 201 seria mentir sobre el efecto.
    * `AutorrestablecimientoNoPermitidoError` -> 409: el administrador se lo aplica a si mismo
      (REQ-073 validacion 3); su camino es EP-005, que SI exige la contrasenia actual.
    * `EntregaCredencialFallidaError` -> 502: el correo con el acceso temporal no salio y, por
      tanto, EL RESTABLECIMIENTO NO SE CONFIRMA: la credencial anterior del usuario SIGUE SIENDO
      LA VIGENTE (AC-RST-03, REQ-073 regla 7). El 502 -y no un 200 con aviso- le dice al
      administrador que la accion es reintentable tal cual.
    * `PermisoDenegadoError` -> 403: lo lanza la guardia ANTES de entrar aqui, cuando el actor no
      es ADMINISTRADOR o el par (rol, operacion) no tiene fila en la matriz (AC-RST-02).

    EL 201 ES EL DEL CONTRATO. `openapi.yaml` publica 201 en la columna Status de EP-017 y es el
    que se devuelve; la vista no elige codigo propio.

    SOLO `post`. `http_method_names` se acota para que cualquier otro verbo sea 405: el recurso
    es una ACCION sobre la cuenta, no un recurso consultable ni modificable parcialmente.
    """

    http_method_names = ["post", "options"]
    # La autenticacion la fija el default del proyecto (`AutenticacionSesionOpaca` en
    # `REST_FRAMEWORK`); los permisos se declaran EXPLICITOS para que leer la vista baste para
    # saber que exige sesion valida Y rol ADMINISTRADOR con `USER_MANAGE` en la matriz.
    permission_classes = [permissions.IsAuthenticated, requiere_administrador(OPERACION_GESTION_USUARIOS)]

    @extend_schema(
        operation_id="EP-017",
        summary="Restablece la contraseña de un usuario activo generando una credencial temporal",
        tags=["ARC-012"],
        request=PasswordResetRequestSerializer,
        responses={201: PasswordResetResultSerializer},
    )
    def post(self, request: Request, user_id: int) -> Response:
        """
        Emite una credencial temporal al usuario `user_id` y devuelve con 201 el desenlace.

        Del cuerpo solo se lee `resetReason`, y es opcional: los otros dos datos de entrada de
        REQ-073 llegan por otro camino -el usuario destino por el PATH y el administrador por el
        CONTEXTO DE SESION-. El `reset_by_user_id` sale de ahi y jamas del payload: el actor no
        se autodeclara (REQ-064).

        AQUI NO SE ABRE TRANSACCION, y esto es DISTINTO de `DesbloqueoCuentaView`, que si envuelve
        todo su caso de uso en un `transaction.atomic()`. `ServicioReposicionCredencial` gestiona
        sus PROPIAS fronteras transaccionales porque su orden es la regla de negocio: encola el
        aviso en una transaccion que CONFIRMA, entrega por SMTP ya fuera de toda transaccion y
        solo despues abre la transaccion que hace vigente la credencial. Envolverlo desde la
        vista mantendria las filas bloqueadas durante todo el timeout del servidor de correo -el
        anti-patron que el handbook prohibe- y ademas romperia AC-RST-03, porque la entrega
        quedaria dentro de la misma unidad de trabajo que la escritura que debe esperarla.

        Por el mismo motivo la busqueda del usuario destino tambien va fuera de transaccion: solo
        resuelve el 404 antes de entrar al caso de uso.
        """

        entrada = PasswordResetRequestSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        actor = self._actor_de(request)

        # `select_related("role_code")` resuelve el rol en el mismo SELECT: el servicio y la
        # entrega leen datos del usuario y sin el habria una consulta extra por el ForeignKey.
        usuario = UsuarioEntity.objects.select_related("role_code").filter(user_id=user_id).first()
        if usuario is None:
            raise UsuarioNoEncontradoError()

        resultado = ServicioReposicionCredencial().restablecer(
            usuario,
            actor=actor,
            motivo=entrada.validated_data.get("reset_reason"),
        )
        return Response(PasswordResetResultSerializer(resultado).data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _actor_de(request: Request) -> ContextoSesion:
        """
        Devuelve el `ContextoSesion` del administrador que ejecuta la operacion, o falla con 401.

        La identidad se lee SOLO de la peticion ya autenticada: primero de `request.auth`, que
        es donde la deja la clase de autenticacion de DRF, y en su defecto del contexto
        publicado en la peticion en curso (`apps.core.contexto.obtener_contexto`), que es donde
        la republica la guardia de autorizacion con el alcance de datos de la operacion. Jamas
        del cuerpo, de la query string ni de una cabecera.

        Sin contexto se lanza `NotAuthenticated`, que el manejador global traduce al 401
        uniforme; en la practica no deberia ocurrir, porque `IsAuthenticated` y la guardia de
        administrador ya han denegado antes, pero el caso de uso no asume su propia proteccion.
        """

        actor = request.auth if isinstance(request.auth, ContextoSesion) else obtener_contexto()
        if actor is None:
            raise NotAuthenticated()

        return actor


class EstadoCredencialView(APIView):
    """
    Lista el estado de credencial de los usuarios con filtros y paginacion (EP-019, REQ-074).

    QUIEN NO ES ADMINISTRADOR NO VE NI UNA FILA (AC-RST-05). Un tecnico o un empleado reciben 403
    y NINGUN dato de otros usuarios, porque la denegacion la produce la guardia declarativa ANTES
    de que esta vista se ejecute y, por tanto, antes de tocar la base de datos: no hay consulta
    que filtrar luego ni respuesta parcial que recortar. El cuerpo del endpoint no comprueba el
    rol en ningun punto.

    NINGUNA RESPUESTA LLEVA `password_hash` (AC-RST-04). El repositorio no selecciona columnas de
    credencial como dato publicable, la proyeccion `EstadoCredencialUsuario` no tiene atributo
    donde alojarlas y `CredentialStatusItemSerializer` no declara ningun campo de ese tipo: la
    garantia es estructural en las tres capas.

    UNA PAGINA POR ENCIMA DEL TOTAL DEVUELVE 200 CON `items` VACIO, no un error (REQ-074). La
    peticion es valida y simplemente no quedan filas; un 404 ahi obligaria al cliente a
    distinguir «no existe el recurso» de «me pase de pagina», que son cosas distintas.

    SOLO LECTURA. `http_method_names` se acota a `get` y `options`: cualquier otro verbo es 405.
    Este recurso consulta el censo, no lo modifica.
    """

    http_method_names = ["get", "options"]
    # La autenticacion la fija el default del proyecto (`AutenticacionSesionOpaca` en
    # `REST_FRAMEWORK`); los permisos se declaran EXPLICITOS para que leer la vista baste para
    # saber que exige sesion valida Y rol ADMINISTRADOR con `USER_MANAGE` en la matriz.
    permission_classes = [permissions.IsAuthenticated, requiere_administrador(OPERACION_GESTION_USUARIOS)]

    @extend_schema(
        operation_id="EP-019",
        summary="Lista el estado de credencial de los usuarios (cambio pendiente, bloqueo, último acceso)",
        tags=["ARC-012"],
        parameters=[CredentialStatusQuerySerializer],
        responses={200: CredentialStatusPageSerializer},
    )
    def get(self, request: Request) -> Response:
        """
        Valida la query string y devuelve con 200 la pagina de estado de credencial pedida.

        La validacion de los filtros ocurre AQUI y no en el repositorio: esta es la unica capa
        que puede responder un 400 de campo diciendo que parametro esta mal -por ejemplo, un
        texto de busqueda mas corto que el minimo de REQ-074-. Aguas abajo, `CriteriosEstadoCredencial`
        viaja ya validado e inmutable.
        """

        consulta = CredentialStatusQuerySerializer(data=request.query_params)
        consulta.is_valid(raise_exception=True)

        pagina = ServicioEstadoCredencial().listar(consulta.a_criterios())
        return Response(CredentialStatusPageSerializer(pagina).data)
