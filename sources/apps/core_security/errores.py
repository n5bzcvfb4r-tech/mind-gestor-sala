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


class RolNoResolubleError(ErrorDominio):
    """
    El rol efectivo del usuario de la sesion no se puede resolver desde la base de datos.

    Ocurre cuando el usuario no tiene `role_code`, cuando su `role_code` no pertenece al
    catalogo `cat_rol`, o cuando el usuario esta inactivo. El comportamiento es FAIL-CLOSED
    (REQ-018, AC-ROL-02): se deniega con 403 y NUNCA se degrada a un permiso mas amplio.

    El `motivo` ("sin_rol", "rol_desconocido", "usuario_inactivo") es traza interna y no se
    serializa en la respuesta: los tres casos comparten el mismo texto para no servir de
    oraculo sobre el estado del usuario.
    """

    codigo = "PERM_DENIED"
    http_status = 403

    def __init__(self, mensaje: str = mensajes.SIN_PERMISOS, *, motivo: str = "desconocido") -> None:
        self.motivo: str = motivo
        super().__init__(mensaje)


class PermisoDenegadoError(ErrorDominio):
    """
    El par (rol vigente, operacion) no tiene fila en `permiso_rol_operacion`.

    Rige DENY BY DEFAULT (REQ-021, REQ-060): la AUSENCIA de fila es denegacion; no hay
    permisos implicitos escritos en codigo.

    `operation_code` y `role_code` son traza interna para el log, nunca parte del cuerpo de
    la respuesta.
    """

    codigo = "PERM_DENIED"
    http_status = 403

    def __init__(self, mensaje: str = mensajes.SIN_PERMISOS, *, operation_code: str | None = None, role_code: str | None = None) -> None:
        self.operation_code = operation_code
        self.role_code = role_code
        super().__init__(mensaje)


class RecursoFueraDeAlcanceError(ErrorDominio):
    """
    El recurso pedido no esta dentro del alcance de datos del solicitante (REQ-023, REQ-031).

    UNA SOLA RESPUESTA PARA TRES MOTIVOS. Los tres motivos reales -`ajeno` (la incidencia
    existe pero pertenece a otro usuario), `inexistente` (no hay fila con ese identificador) e
    `identificador_invalido` (el identificador ni siquiera es un numero valido)- producen
    EXACTAMENTE la misma respuesta: mismo codigo, mismo estado y mismo texto
    (`mensajes.RECURSO_NO_ENCONTRADO`). `motivo` es traza interna para el log tecnico y NUNCA
    se serializa en el cuerpo (AC-PERM-04): si viajara, el cliente podria distinguir "no es
    tuya" de "no existe" y enumerar incidencias ajenas probando identificadores.

    404 Y NO 403, POR DECISION EXPLICITA DE REQ-023. Un 403 seria tecnicamente mas descriptivo
    -"existe, pero no puedes"- y justo por eso esta prohibido aqui: confirmaria la EXISTENCIA
    del recurso. Fuera de alcance equivale a inexistente para quien pregunta.

    SIN EFECTO LATERAL (REQ-031): lanzar esta excepcion no escribe en base, no muta el contexto
    de sesion y no emite eventos.
    """

    codigo = "INC_NOT_FOUND"
    http_status = 404

    def __init__(self, mensaje: str = mensajes.RECURSO_NO_ENCONTRADO, *, motivo: str = "desconocido") -> None:
        self.motivo: str = motivo
        super().__init__(mensaje)
