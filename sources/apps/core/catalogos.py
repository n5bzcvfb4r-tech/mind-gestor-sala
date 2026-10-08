"""
Verificacion de que los catalogos maestros estan poblados con las semillas (ARC-016).

El servicio no tiene valores por defecto en codigo para los catalogos: las filas las aporta
el changelog dml de semillas. Por eso la comprobacion consulta SIEMPRE la base de datos real.
"""

from typing import Any


# Las 8 tablas de catalogo maestro que el servicio exige pobladas, en orden.
CATALOGOS_OBLIGATORIOS: tuple[str, ...] = (
    "cat_rol",
    "cat_operacion",
    "cat_estado_incidencia",
    "cat_transicion_incidencia",
    "cat_categoria_incidencia",
    "cat_oficina",
    "cat_sala",
    "cat_motivo_desactivacion",
)


def _modelos_de_catalogo() -> dict[str, type]:
    """Mapa tabla -> modelo del ORM. El import es perezoso: los modelos no existen antes de `django.setup()`."""

    from apps.core.models import (
        CategoriaIncidenciaEntity,
        EstadoIncidenciaEntity,
        MotivoDesactivacionEntity,
        OficinaEntity,
        OperacionEntity,
        RolEntity,
        SalaEntity,
        TransicionIncidenciaEntity,
    )

    return {
        "cat_rol": RolEntity,
        "cat_operacion": OperacionEntity,
        "cat_estado_incidencia": EstadoIncidenciaEntity,
        "cat_transicion_incidencia": TransicionIncidenciaEntity,
        "cat_categoria_incidencia": CategoriaIncidenciaEntity,
        "cat_oficina": OficinaEntity,
        "cat_sala": SalaEntity,
        "cat_motivo_desactivacion": MotivoDesactivacionEntity,
    }


def catalogos_vacios() -> list[str]:
    """
    Devuelve los nombres de los catalogos obligatorios que no tienen ni una fila.

    Cada comprobacion es un `COUNT(*)` contra la base de datos; no hay cache ni estructura
    en memoria que pueda enmascarar unas semillas no aplicadas.
    """

    modelos: dict[str, Any] = _modelos_de_catalogo()
    vacios: list[str] = []
    for tabla in CATALOGOS_OBLIGATORIOS:
        modelo = modelos[tabla]
        if modelo.objects.count() == 0:
            vacios.append(tabla)
    return vacios
