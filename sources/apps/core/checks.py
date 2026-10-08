"""
Checks del framework de Django sobre el estado de los catalogos maestros.

NOTA INTENCIONADA: el check va etiquetado con `Tags.database`, de modo que
`python manage.py check` (que no toca la base de datos) NO lo ejecuta, mientras que
`python manage.py check --database default` SI lo ejecuta. Asi el arranque y las tareas
de desarrollo no dependen de una conexion viva, pero el despliegue puede exigirla.
"""

from typing import Any

from django.core.checks import Error, Tags, register

from apps.core import mensajes
from apps.core.catalogos import catalogos_vacios


ID_CATALOGO_VACIO = "core.E001"
ID_ERROR_VERIFICACION = "core.E002"
PISTA_SEMILLAS = "Aplica el changelog dml de semillas (ARC-016) contra la base de datos."


@register(Tags.database)
def comprobar_catalogos_poblados(app_configs: Any, databases: Any = None, **kwargs: Any) -> list[Error]:
    """Devuelve un `Error` por cada catalogo maestro sin filas, o uno solo si la BBDD no responde.

    Convencion de los checks de base de datos de Django: cuando la orden no solicita
    ninguna base de datos (`manage.py check` a secas), `databases` llega a None y el
    check se abstiene. Con `manage.py check --database default` si se consulta Oracle.
    """

    if not databases:
        return []

    try:
        vacios = catalogos_vacios()
    except Exception as error:  # BBDD no alcanzable, esquema ausente o configuracion incompleta
        return [
            Error(
                mensajes.ERROR_VERIFICANDO_CATALOGOS.format(detalle=error),
                hint=PISTA_SEMILLAS,
                id=ID_ERROR_VERIFICACION,
            )
        ]

    return [Error(mensajes.CATALOGO_VACIO.format(tabla=tabla), hint=PISTA_SEMILLAS, id=ID_CATALOGO_VACIO) for tabla in vacios]
