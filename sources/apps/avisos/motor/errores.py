"""
Excepciones de dominio del motor de cola de avisos por correo (ARC-115, REQ-132, REQ-142).

Toda excepcion de este modulo hereda de `apps.core.errores.ErrorDominio`, de modo que expone su
texto en espanol (REQ-050) a traves de `self.mensaje` y las capas superiores pueden serializarlo
sin reconstruirlo. Los literales viven arriba, junto al resto de mensajes del motor, para que el
texto visible no quede enterrado dentro de los constructores.
"""

from apps.core.errores import ErrorDominio

# --- Literales del motor de avisos ---------------------------------------
TRANSICION_AVISO_NO_PERMITIDA = "No se puede pasar el aviso de «{origen}» a «{destino}»."
AVISO_YA_ENTREGADO = "El aviso «{notification_id}» ya esta ENVIADO: es un estado final y no admite reactivacion a «{destino}» (REQ-142)."
REINTENTO_NO_PROGRAMABLE = "No se puede programar un reintento de entrega para el aviso «{notification_id}»: {motivo}"
INTENTOS_AGOTADOS = "El aviso «{notification_id}» ha agotado sus intentos de entrega: {attempt_count} de {max_attempts} permitidos."
CLAVE_AVISO_INVALIDA = "La clave de aviso «{clave}» no es valida: {motivo}"


class ErrorMotorAvisos(ErrorDominio):
    """Raiz de la jerarquia de errores del motor de cola de avisos."""


class TransicionAvisoNoPermitidaError(ErrorMotorAvisos):
    """
    La transicion de estado de envio solicitada no esta en el grafo del catalogo cerrado (REQ-142 validacion 5).

    El grafo vive en `apps.avisos.motor.estados`: aqui solo se reporta el par rechazado.
    """

    def __init__(self, origen: str, destino: str) -> None:
        self.origen: str = origen
        self.destino: str = destino
        super().__init__(TRANSICION_AVISO_NO_PERMITIDA.format(origen=origen, destino=destino))


class AvisoYaEntregadoError(ErrorMotorAvisos):
    """
    Se ha intentado sacar de `ENVIADO` un aviso ya entregado.

    Es el caso particular de `TransicionAvisoNoPermitidaError` que cubre la regla 2 de REQ-142
    («ENVIADO es un estado final: un aviso enviado no pasa a FALLIDO ni a DESCARTADO») y que la
    base de datos defiende ademas con el trigger `trg_aviso_correo_estado_final` (ORA-20060).
    """

    def __init__(self, notification_id: str, destino: str) -> None:
        self.notification_id: str = notification_id
        self.destino: str = destino
        super().__init__(AVISO_YA_ENTREGADO.format(notification_id=notification_id, destino=destino))


class ReintentoNoProgramableError(ErrorMotorAvisos):
    """No procede programar un reintento automatico para el aviso (error permanente, estado terminal...)."""

    def __init__(self, notification_id: str, motivo: str) -> None:
        self.notification_id: str = notification_id
        self.motivo: str = motivo
        super().__init__(REINTENTO_NO_PROGRAMABLE.format(notification_id=notification_id, motivo=motivo))


class IntentosAgotadosError(ErrorMotorAvisos):
    """El aviso ha alcanzado el maximo de intentos configurado (REQ-142 regla 3; AC-SMTP-03)."""

    def __init__(self, notification_id: str, attempt_count: int, max_attempts: int) -> None:
        self.notification_id: str = notification_id
        self.attempt_count: int = attempt_count
        self.max_attempts: int = max_attempts
        super().__init__(
            INTENTOS_AGOTADOS.format(
                notification_id=notification_id,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
            )
        )


class ClaveAvisoInvalidaError(ErrorMotorAvisos):
    """La `notification_key` de idempotencia no se ha podido construir o no cumple su formato (ARC-115)."""

    def __init__(self, clave: str, motivo: str) -> None:
        self.clave: str = clave
        self.motivo: str = motivo
        super().__init__(CLAVE_AVISO_INVALIDA.format(clave=clave, motivo=motivo))


__all__ = [
    "AVISO_YA_ENTREGADO",
    "CLAVE_AVISO_INVALIDA",
    "INTENTOS_AGOTADOS",
    "REINTENTO_NO_PROGRAMABLE",
    "TRANSICION_AVISO_NO_PERMITIDA",
    "AvisoYaEntregadoError",
    "ClaveAvisoInvalidaError",
    "ErrorMotorAvisos",
    "IntentosAgotadosError",
    "ReintentoNoProgramableError",
    "TransicionAvisoNoPermitidaError",
]
