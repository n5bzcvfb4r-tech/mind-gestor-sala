"""
Ventana de servicio del DESPACHADOR de la cola de avisos (REQ-132 regla 6).

Que acota el horario laboral
----------------------------
El horario laboral acota el PROCESAMIENTO de la cola, NUNCA el ENCOLADO. Encolar una solicitud
de aviso esta siempre permitido, a cualquier hora y cualquier dia: la insercion en `aviso_correo`
forma parte de la transaccion de negocio que la origina (patron outbox) y bloquearla equivaliaria
a perder el hecho de negocio. Lo unico que se detiene fuera de la ventana es el consumo: el
despachador no toma lotes, los avisos permanecen pendientes y se procesan al reanudar respetando
el orden FIFO de la cola, tal y como exige el DoD de la tarea.

Tension declarada en el RFP
---------------------------
REQ-142 regla 6 dice que «la hora de generacion de un aviso no restringe su ventana de envio»,
mientras que REQ-132 regla 6 impone la franja laboral al tratamiento de las solicitudes. Ambas
reglas estan en tension en el RFP y no la resolvemos por nuestra cuenta: aqui manda el DoD de la
tarea (se respeta la ventana en el procesamiento) y, para que el negocio pueda decantarse por la
lectura de REQ-142 sin tocar codigo, la ventana es desactivable con `respetar_horario_laboral=False`
(variable de entorno; ver `apps.avisos.motor.configuracion`).

Todos los `datetime` de este modulo son NAIVE en UTC, como el resto del proyecto: el `datetime`
aware esta prohibido por la matriz de tipos. La marca temporal se obtiene de `utc_now()`.
"""

from datetime import datetime, timedelta

from apps.avisos.motor.configuracion import ConfiguracionMotorAvisos
from apps.avisos.motor.errores import ErrorMotorAvisos
from apps.core.contexto import utc_now

#: Horas que se exploran como maximo al buscar la proxima reanudacion (8 dias).
#: Con una configuracion valida (al menos un dia laborable y una franja no vacia) siempre
#: se encuentra una hora habil dentro de una semana; el octavo dia es solo margen.
MAXIMO_HORAS_EXPLORADAS = 192


def en_horario_laboral(momento: datetime, config: ConfiguracionMotorAvisos) -> bool:
    """
    Indica si `momento` cae dentro de la ventana de servicio del despachador.

    Args:
        momento (datetime): instante a evaluar, NAIVE en UTC.
        config (ConfiguracionMotorAvisos): parametros del motor.

    Returns:
        bool: `True` si la ventana esta desactivada (`respetar_horario_laboral=False`) o si el
        instante cae en un dia laborable dentro de la franja `[hora_inicio, hora_fin)`.
    """

    if not config.respetar_horario_laboral:
        return True
    if momento.weekday() not in config.dias_laborables:
        return False
    return config.hora_inicio_laboral <= momento.hour < config.hora_fin_laboral


def procesamiento_permitido(config: ConfiguracionMotorAvisos, *, ahora: datetime | None = None) -> bool:
    """
    Indica si el despachador puede consumir la cola en este momento.

    Args:
        config (ConfiguracionMotorAvisos): parametros del motor.
        ahora (datetime | None): instante a evaluar; si es `None` se usa `utc_now()`.

    Returns:
        bool: `True` si el procesamiento esta permitido ahora mismo.
    """

    return en_horario_laboral(ahora if ahora is not None else utc_now(), config)


def proxima_reanudacion(momento: datetime, config: ConfiguracionMotorAvisos) -> datetime:
    """
    Primer instante igual o posterior a `momento` en el que el despachador vuelve a tener servicio.

    Sirve para trazar cuando se reanudara la cola cuando el motor se detiene fuera de horario.
    La busqueda es deliberadamente simple y determinista: se avanza hora a hora normalizando
    minutos, segundos y microsegundos a cero, hasta dar con la primera hora habil.

    Args:
        momento (datetime): instante de partida, NAIVE en UTC.
        config (ConfiguracionMotorAvisos): parametros del motor.

    Returns:
        datetime: `momento` si ya esta dentro de la ventana (o si la ventana esta desactivada);
        en caso contrario, el comienzo de la primera hora habil posterior.

    Raises:
        ErrorMotorAvisos: si la configuracion no deja ninguna hora habil en 8 dias.
    """

    if not config.respetar_horario_laboral or en_horario_laboral(momento, config):
        return momento

    candidato = momento.replace(minute=0, second=0, microsecond=0)
    for _ in range(MAXIMO_HORAS_EXPLORADAS):
        candidato += timedelta(hours=1)
        if en_horario_laboral(candidato, config):
            return candidato

    raise ErrorMotorAvisos(
        "La configuracion del horario laboral del motor de avisos no deja ninguna hora habil: la cola nunca se reanudaria."
    )


__all__ = [
    "MAXIMO_HORAS_EXPLORADAS",
    "en_horario_laboral",
    "procesamiento_permitido",
    "proxima_reanudacion",
]
