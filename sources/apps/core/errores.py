"""
Excepciones de dominio del nucleo transversal.

Toda excepcion de dominio expone su texto en espanol (REQ-050) a traves de `self.mensaje`,
para que las capas superiores puedan serializarlo sin reconstruirlo.
"""

from apps.core import mensajes


class ErrorDominio(Exception):
    """Base de todas las excepciones de dominio del servicio."""

    def __init__(self, mensaje: str) -> None:
        super().__init__(mensaje)
        self.mensaje: str = mensaje


class BorradoFisicoNoPermitidoError(ErrorDominio):
    """El borrado fisico esta prohibido en todo el modelo de datos: las bajas son logicas (REQ-047)."""

    def __init__(self, mensaje: str = mensajes.BORRADO_FISICO_NO_PERMITIDO) -> None:
        super().__init__(mensaje)


class RegistroInmutableError(ErrorDominio):
    """Los historicos y la auditoria son append-only: se insertan, nunca se modifican (REQ-015, REQ-065)."""

    def __init__(self, mensaje: str = mensajes.REGISTRO_HISTORICO_INMUTABLE) -> None:
        super().__init__(mensaje)


class ContextoSesionNoDisponibleError(ErrorDominio):
    """No hay contexto de sesion: se deniega por defecto en vez de inventar un actor (REQ-064)."""

    def __init__(self, mensaje: str = mensajes.SIN_CONTEXTO_DE_SESION) -> None:
        super().__init__(mensaje)


class CatalogosVaciosError(ErrorDominio):
    """Faltan semillas de catalogos maestros: el proceso servidor no debe arrancar."""

    def __init__(self, tablas_vacias: list[str]) -> None:
        self.tablas_vacias: list[str] = list(tablas_vacias)
        detalle = ", ".join(self.tablas_vacias)
        super().__init__(f"{mensajes.CATALOGOS_VACIOS_ARRANQUE} Tablas vacias: {detalle}.")
