"""
Soporte de trazas de la capa de seguridad.

El formateador `verbose` del arquetipo incluye `%(data)s` en su patron, de modo que un
registro de log SIN ese atributo rompe el formateo y la linea se pierde tras un
`--- Logging error ---` en stderr. Los registros del servicio SI lo aportan
(`extra={"data": {...}}`), pero los de Django y los de las librerias de terceros no: en
cuanto el guardia de sesion empezo a denegar peticiones, cada 401 generaba ademas el
`Unauthorized:` de `django.request`, sin `data`, y con el una traza de error de logging.

Este filtro cierra ese hueco: rellena `data` con un diccionario vacio cuando el registro no
lo trae, de forma que el patron siempre pueda formatearse. No altera ningun registro que ya
aporte sus datos estructurados.
"""

import logging


class DatosEstructuradosFilter(logging.Filter):
    """Garantiza que todo registro tenga el atributo `data` que exige el formateador `verbose`."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Rellena `data` si falta y deja pasar SIEMPRE el registro (nunca descarta trazas)."""

        if not hasattr(record, "data"):
            record.data = {}
        return True
