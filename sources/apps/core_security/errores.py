"""
Excepciones de dominio de la seguridad de sesion.

Todas heredan de `apps.core.errores.ErrorDominio` y anaden dos datos que la capa HTTP
necesita para responder sin reconstruir nada: `codigo` (identificador estable del error) y
`http_status`.

El mensaje visible NUNCA detalla por que ha fallado la sesion ni si el usuario existe: esa
informacion solo viaja por la traza interna (`motivo`), jamas por la respuesta.
"""

from apps.core.errores import ErrorDominio
from apps.core_security import mensajes


class SesionInvalidaError(ErrorDominio):
    """
    La sesion presentada no sirve para autenticar la peticion (ausente, manipulada, revocada o caducada).

    Responde SIEMPRE 401 con el mismo texto (`mensajes.SESION_REQUERIDA`, AC-SES-04). El
    `motivo` es trazabilidad interna: se registra en el log tecnico y no se serializa en la
    respuesta, para no revelar el estado real de la sesion ni la existencia del recurso.
    """

    codigo = "AUTH_SESSION_INVALID"
    http_status = 401

    def __init__(self, mensaje: str = mensajes.SESION_REQUERIDA, *, motivo: str = "desconocido") -> None:
        self.motivo: str = motivo
        super().__init__(mensaje)


class CredencialesInvalidasError(ErrorDominio):
    """
    El inicio de sesion ha fallado: usuario inexistente, usuario inactivo o contrasenia incorrecta.

    Los tres casos comparten respuesta a proposito: la API no debe permitir enumerar cuentas.
    """

    codigo = "AUTH_BAD_CREDENTIALS"
    http_status = 401

    def __init__(self, mensaje: str = mensajes.CREDENCIALES_INVALIDAS) -> None:
        super().__init__(mensaje)


class CuentaBloqueadaError(ErrorDominio):
    """La cuenta esta bloqueada temporalmente (`usuario.locked_until` en el futuro): 423 Locked."""

    codigo = "AUTH_ACCOUNT_LOCKED"
    http_status = 423

    def __init__(self, mensaje: str = mensajes.CUENTA_BLOQUEADA) -> None:
        super().__init__(mensaje)
