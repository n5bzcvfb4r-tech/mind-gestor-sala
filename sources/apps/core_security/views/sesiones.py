"""
Vista del inicio de sesion (EP-001, `POST /api/auth/sessions`).

UNICA RUTA PUBLICA (REQ-058). Es el unico endpoint que se atiende SIN sesion previa, por una
razon evidente: no se puede exigir una sesion para crearla. La exencion se declara aqui de
forma EXPLICITA (`authentication_classes` vacio y `AllowAny`) en vez de heredarla del default
del proyecto, para que leer la vista baste para saber que esta abierta.

FRONTERA TRANSACCIONAL. El caso de uso abre la transaccion: el sello de `last_login_at` del
usuario y el alta de la fila de `sesion_usuario` se confirman juntos o no se confirma ninguno.

SECRETOS (REQ-063). La contrasenia no se registra, no se traza y no vuelve en la respuesta.
Los fallos de credencial (`CredencialesInvalidasError`, `CuentaBloqueadaError`) se dejan
PROPAGAR: el manejador unico de excepciones (`apps.core_security.manejadores`) los traduce
al mismo cuerpo de error del resto del servicio (`code`, `message`, `details`, `traceId`),
sin revelar si la cuenta existe. La vista no compone ningun cuerpo de error propio.
"""

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core_security.serializers.sesiones import LoginRequestSerializer, SessionDetailSerializer
from apps.core_security.servicios.sesiones import ServicioSesiones


class IniciarSesionView(APIView):
    """Inicia sesion con usuario y contrasenia propios y devuelve la sesion con el rol vigente (EP-001)."""

    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        operation_id="EP-001",
        summary="Inicia sesión con usuario y contraseña propios y devuelve la sesión con el rol vigente",
        tags=["ARC-012"],
        request=LoginRequestSerializer,
        responses={201: SessionDetailSerializer},
    )
    def post(self, request: Request) -> Response:
        """Valida las credenciales, emite la sesion y la devuelve con 201."""

        entrada = LoginRequestSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        servicio = ServicioSesiones()
        with transaction.atomic():
            usuario = servicio.autenticar(
                entrada.validated_data["username"],
                entrada.validated_data["password"],
            )
            sesion = servicio.emitir(usuario)

        return Response(SessionDetailSerializer(sesion).data, status=status.HTTP_201_CREATED)
