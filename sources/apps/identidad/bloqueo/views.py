"""
Vista del desbloqueo administrativo de cuenta (EP-018, `POST /api/users/{userId}/unlock`).

RUTA PROTEGIDA Y RESERVADA AL ADMINISTRADOR. No se declara en `rutas_publicas`, de modo que el
guardia de sesion (`SesionRequeridaMiddleware`) exige sesion valida por defecto, y encima lleva
la guardia declarativa `requiere_administrador("USER_MANAGE")`, que ademas de exigir el rol
consulta la matriz `permiso_rol_operacion`. La autorizacion se declara AQUI, junto a la vista,
y NUNCA dentro del cuerpo del endpoint: un `if actor.role_code != ...` es el anti-patron que
prohibe la guia del proyecto, porque convierte el codigo en una segunda fuente de verdad que se
desalinea de la matriz.

FRONTERA TRANSACCIONAL. La pone el caso de uso -esta vista-, porque es quien sabe que
escrituras forman una sola unidad de trabajo; `ServicioBloqueoCuenta` NO abre transaccion a
proposito (ver su docstring de modulo).

ERRORES. La vista no compone ningun cuerpo de error propio: deja PROPAGAR
`UsuarioNoEncontradoError` (404), `CuentaNoBloqueadaError` (409) y `CuentaInactivaError` (409),
y el manejador unico (`apps.core_security.manejadores`) los traduce al cuerpo uniforme del
servicio (`code`, `message`, `details`, `traceId`).
"""

from __future__ import annotations

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.exceptions import NotAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.contexto import ContextoSesion, obtener_contexto, utc_now
from apps.core.models import UsuarioEntity
from apps.core_security.permisos import requiere_administrador
from apps.identidad.bloqueo.errores import UsuarioNoEncontradoError
from apps.identidad.bloqueo.serializers import (
    AccountLockStatusSerializer,
    AccountUnlockRequestSerializer,
    ResultadoDesbloqueo,
)
from apps.identidad.bloqueo.servicio import ServicioBloqueoCuenta


#: `operation_code` del catalogo `cat_operacion` (enumerado del esquema T.5) bajo el que la
#: matriz autoriza la gestion del censo de usuarios, a la que pertenece el desbloqueo.
OPERACION_GESTION_USUARIOS = "USER_MANAGE"


__all__ = ["DesbloqueoCuentaView", "OPERACION_GESTION_USUARIOS"]


class DesbloqueoCuentaView(APIView):
    """
    Desbloquea por decision administrativa una cuenta bloqueada por intentos fallidos (EP-018).

    EL EFECTO ES EXACTAMENTE EL DE AC-RST-06 Y NINGUNO MAS. Se pone `locked_until` a `null` y
    `failed_password_attempts` a `0`, y a partir de ese momento el usuario puede volver a
    entrar con SU CONTRASENIA DE SIEMPRE. Deliberadamente NO ocurre nada de lo siguiente:

    * NO se altera `status` ni ningun indicador de actividad de la cuenta: el bloqueo temporal
      y la baja logica son estados independientes (REQ-055 RN-03, AC-AUT-05).
    * NO se cambia la contrasenia ni se fuerza su cambio: el desbloqueo no es un
      restablecimiento de credencial (REQ-075 RN-03).
    * NO se revocan sesiones: AC-RST-06 dice literalmente que «sus sesiones no son revocadas»,
      asi que `sesion_usuario` no se consulta ni se escribe desde aqui.
    * NO se borra `last_failed_attempt_at`: la traza del ultimo fallo sobrevive al desbloqueo.

    SOBRE UNA CUENTA QUE NO ESTA BLOQUEADA NO SE ESCRIBE NADA (AC-RST-07). El servicio responde
    con el 409 `USR_ACCOUNT_NOT_LOCKED` sin tocar la fila ni dejar traza de auditoria, tambien
    cuando el bloqueo ya habia vencido solo por el paso del tiempo.

    SOLO `post`. `http_method_names` se acota para que cualquier otro verbo sea 405: el recurso
    es una ACCION sobre la cuenta, no un recurso consultable ni modificable parcialmente.
    """

    http_method_names = ["post", "options"]
    # La autenticacion la fija el default del proyecto (`AutenticacionSesionOpaca` en
    # `REST_FRAMEWORK`); los permisos se declaran EXPLICITOS para que leer la vista baste para
    # saber que exige sesion valida Y rol ADMINISTRADOR con `USER_MANAGE` en la matriz.
    permission_classes = [permissions.IsAuthenticated, requiere_administrador(OPERACION_GESTION_USUARIOS)]

    @extend_schema(
        operation_id="EP-018",
        summary="Desbloquea una cuenta bloqueada por intentos fallidos sin alterar su contraseña",
        tags=["ARC-012"],
        request=AccountUnlockRequestSerializer,
        responses={201: AccountLockStatusSerializer},
    )
    def post(self, request: Request, user_id: int) -> Response:
        """
        Levanta el bloqueo de la cuenta `user_id` y devuelve con 201 su estado resultante.

        El cuerpo se valida aunque no aporte ningun dato: `AccountUnlockRequest` no tiene
        campos porque los dos datos de entrada de REQ-075 llegan por otro camino -la cuenta
        objetivo por el PATH y el administrador por el CONTEXTO DE SESION-. El
        `unlocked_by_user_id` sale de ahi y jamas del payload: el actor no se autodeclara
        (REQ-064).

        La transaccion envuelve la lectura de la fila y su actualizacion, de modo que el UPDATE
        de las columnas de bloqueo y la traza de auditoria del servicio se confirman juntos o
        no se confirma ninguno.
        """

        entrada = AccountUnlockRequestSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        actor = self._actor_de(request)

        with transaction.atomic():
            usuario = UsuarioEntity.objects.filter(user_id=user_id).first()
            if usuario is None:
                raise UsuarioNoEncontradoError()

            estado = ServicioBloqueoCuenta().desbloquear(usuario, actor=actor)

        resultado = ResultadoDesbloqueo(
            user_id=usuario.user_id,
            # Tras el desbloqueo no queda bloqueo vigente, pero el booleano se DERIVA del
            # estado devuelto en vez de escribirse a `False` a mano: asi la respuesta no puede
            # contradecir a lo que la politica acaba de decidir.
            locked=estado.bloqueada_hasta is not None,
            bloqueada_hasta=estado.bloqueada_hasta,
            intentos_fallidos=estado.intentos_fallidos,
            ultimo_fallo_en=estado.ultimo_fallo_en,
            unlocked_at=utc_now(),
            unlocked_by=actor.user_id,
        )
        return Response(AccountLockStatusSerializer(resultado).data, status=status.HTTP_201_CREATED)

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
