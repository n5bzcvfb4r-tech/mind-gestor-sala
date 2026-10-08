"""
Clase de autenticacion de DRF para la sesion opaca del servicio.

NO duplica la validacion de sesion: el guardia `SesionRequeridaMiddleware` ya la ha hecho
por delante de la vista, y aqui nos limitamos a PUBLICAR en DRF (`request.user`,
`request.auth`) la MISMA sesion que aquel dejo en `request.contexto_sesion`.

Solo cuando la peticion llega sin pasar por el guardia (por ejemplo una vista montada en un
`APIClient` de pruebas, o una ruta exenta que aun asi quiera identificar al usuario) se
revalida la credencial, y entonces se delega otra vez en `ServicioSesiones.validar()`, que
sigue siendo el unico sitio donde una sesion se valida.

SECRETOS (REQ-063, REQ-076). `UsuarioAutenticado` expone identidad (usuario, rol, nombre
visible) y jamas `password_hash` ni la credencial presentada.
"""

from dataclasses import dataclass

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.core.contexto import ContextoSesion
from apps.core_security.errores import SesionInvalidaError
from apps.core_security.middleware import credencial_de_la_peticion
from apps.core_security.servicios.sesiones import ServicioSesiones


@dataclass(frozen=True, slots=True)
class UsuarioAutenticado:
    """
    Envoltorio minimo de la identidad de la peticion para que DRF lo ponga en `request.user`.

    Es inmutable y de solo lectura: expone el `ContextoSesion` ya resuelto y nada mas. No
    es un modelo de Django ni pretende serlo; la autorizacion por rol se resuelve contra la
    matriz de permisos, no contra este objeto.
    """

    contexto: ContextoSesion

    @property
    def is_authenticated(self) -> bool:
        """DRF e `IsAuthenticated` consultan este atributo: si existe este objeto, hay sesion valida."""

        return True

    @property
    def is_anonymous(self) -> bool:
        """Contrapartida de `is_authenticated`: este usuario nunca es anonimo."""

        return False

    @property
    def user_id(self) -> int:
        """Identificador del usuario de la sesion."""

        return self.contexto.user_id

    @property
    def role_code(self) -> str:
        """Codigo de rol congelado en la emision de la sesion."""

        return self.contexto.role_code

    @property
    def session_id(self) -> str | None:
        """Identificador opaco de la sesion; es un dato interno y NO debe registrarse en logs."""

        return self.contexto.session_id

    def __str__(self) -> str:
        return self.contexto.display_name or f"usuario {self.contexto.user_id}"


class AutenticacionSesionOpaca(BaseAuthentication):
    """
    Autenticacion DRF basada en la sesion opaca server-side (ARC-112).

    Reutiliza el contexto que el guardia de sesion dejo en la peticion y, en su ausencia,
    valida la credencial `Bearer` con `ServicioSesiones`. Sin credencial devuelve `None`:
    DRF deja continuar y es `IsAuthenticated` quien deniega, con lo que la decision de
    denegar sigue estando en un unico sitio.
    """

    def authenticate(self, request) -> tuple[object, object] | None:
        """
        Devuelve `(usuario, contexto)` si la peticion tiene sesion valida, o `None` si no trae credencial.

        Raises:
            AuthenticationFailed: si la credencial presentada no es una sesion valida. El texto
                es el mensaje uniforme de 401 (AC-SES-04), sin detallar el motivo.
        """

        contexto: ContextoSesion | None = getattr(request, "contexto_sesion", None)
        if contexto is not None:
            return (UsuarioAutenticado(contexto=contexto), contexto)

        credencial = credencial_de_la_peticion(request)
        if credencial is None:
            return None

        try:
            contexto = ServicioSesiones().validar(credencial)
        except SesionInvalidaError as exc:
            raise AuthenticationFailed(exc.mensaje) from exc

        return (UsuarioAutenticado(contexto=contexto), contexto)

    def authenticate_header(self, request) -> str:
        """Esquema del reto `WWW-Authenticate`, para que DRF responda 401 y no 403 al faltar credencial."""

        return "Bearer"
