"""
Registro de la app de avisos y COMPOSITION ROOT del motor de cola de correo (ARC-014).

`ready()` es el unico punto del producto donde se arranca el planificador en proceso: es el primer
momento en que el registro de apps esta completo y los modelos se pueden importar.
"""

import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)

#: Comandos de `manage.py` que NO son un proceso servidor. Un hilo de fondo que empieza a consumir
#: la cola durante una migracion escribiria sobre un esquema a medio migrar, y durante la suite de
#: pruebas tomaria lotes por su cuenta haciendo los tests NO deterministas. En ninguno de estos
#: comandos se arranca el motor.
COMANDOS_SIN_MOTOR: frozenset[str] = frozenset(
    {
        "migrate",
        "makemigrations",
        "shell",
        "test",
        "collectstatic",
        "check",
        "verificar_catalogos",
        "createsuperuser",
    }
)

#: Formas de `sys.argv[0]` con las que el interprete NO esta ejecutando ningun programa servidor:
#: `python -c "..."`, `python - <<EOF` y la REPL interactiva. Son ejecuciones ad-hoc (diagnostico,
#: smoke de import) que tampoco deben ponerse a consumir la cola.
INTERPRETE_AD_HOC: frozenset[str] = frozenset({"-c", "-", ""})

TRAZA_OMITIDO = "Motor de avisos no arrancado: el proceso en curso no es un servidor"
TRAZA_FALLO_ARRANQUE = (
    "El planificador del motor de avisos no ha podido arrancar: el proceso continua sin consumir la cola de correo"
)


def _es_proceso_servidor() -> bool:
    """
    Indica si el proceso en curso debe consumir la cola de avisos.

    Devuelve `False` para los comandos de gestion de `COMANDOS_SIN_MOTOR`, para las invocaciones
    ad-hoc del interprete (`INTERPRETE_AD_HOC`) y para la ejecucion bajo pytest, que se detecta tanto
    por el modulo ya importado como por `PYTEST_CURRENT_TEST` (la variable que pytest informa en cada
    test, util cuando se invoca a traves de otro lanzador).

    El criterio es una LISTA NEGRA y no una lista blanca a proposito: el servidor de produccion se
    lanza con un WSGI externo cuyo nombre no conoce este modulo, y una lista blanca dejaria la cola
    sin consumir en el unico proceso donde si debe trabajar.
    """

    if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if (sys.argv[0] if sys.argv else "") in INTERPRETE_AD_HOC:
        return False
    comando = sys.argv[1] if len(sys.argv) > 1 else ""
    return comando not in COMANDOS_SIN_MOTOR


class AvisosConfig(AppConfig):
    name = "apps.avisos"
    verbose_name = "Avisos por correo"

    def ready(self) -> None:
        if not _es_proceso_servidor():
            logger.info(TRAZA_OMITIDO, extra={"data": {"argv": sys.argv[1:2]}})
            return

        # Import PEREZOSO: a nivel de modulo, `apps.py` se importa antes de que el registro de apps
        # este listo y arrastrar aqui el despachador romperia la carga de la aplicacion.
        from apps.avisos.motor.planificador import iniciar_planificador

        try:
            iniciar_planificador()
        except Exception:
            # Un fallo del motor de avisos NUNCA puede tumbar el arranque del proceso: el gate de
            # arranque del producto es la verificacion de catalogos de `apps.core.arranque`, no esto.
            # Gracias a esto el proceso tambien importa y arranca sin base de datos disponible.
            logger.exception(TRAZA_FALLO_ARRANQUE)


__all__ = ["COMANDOS_SIN_MOTOR", "INTERPRETE_AD_HOC", "AvisosConfig"]
