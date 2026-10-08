"""
Excepciones de dominio de la resolucion de destinatarios (REQ-089, REQ-140).

Heredan de `ErrorDominio` para que las capas superiores serialicen `self.mensaje` sin
reconstruir el texto. El detalle tecnico viaja en atributos propios y NUNCA dentro del
mensaje de usuario: lo necesita la traza, no quien recibe la respuesta.
"""

from apps.avisos.resolucion import mensajes
from apps.core.errores import ErrorDominio


class DirectorioNoDisponibleError(ErrorDominio):
    """
    El directorio de usuarios no ha respondido y la resolucion no se ha podido completar.

    Es un fallo tecnico, no funcional: quien la captura decide si degrada la operacion
    (REQ-089) en vez de dar el aviso por enviado.
    """

    def __init__(self, detalle: str) -> None:
        self.detalle: str = detalle
        super().__init__(mensajes.DIRECTORIO_NO_DISPONIBLE)


class ColectivoDesconocidoError(ErrorDominio):
    """El colectivo solicitado no pertenece al catalogo cerrado de colectivos notificables."""

    def __init__(self, colectivo: str) -> None:
        self.colectivo: str = colectivo
        super().__init__(mensajes.COLECTIVO_DESCONOCIDO)


class ConsultaNoAutorizadaError(ErrorDominio):
    """La consulta de resolucion de destinatarios esta reservada al administrador (deny-by-default)."""

    def __init__(self) -> None:
        super().__init__(mensajes.CONSULTA_SOLO_ADMINISTRADOR)
