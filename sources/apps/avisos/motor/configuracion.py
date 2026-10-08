"""
Parametros de operacion del motor de cola de avisos (patron outbox sobre `aviso_correo`).

TODO lo que gobierna el comportamiento del motor vive aqui y se lee de `settings.AVISOS_MOTOR`,
que a su vez se construye en `config/settings.py` a partir del entorno (`${VAR:default}`). En el
camino de ejecucion no hay ningun valor incrustado: cada clave tiene un default documentado en
este modulo para que el motor siga siendo importable y operable aunque la clave falte en settings.

Gaps declarados del RFP
-----------------------
El RFP marca como `[gap]` tanto la ventana de recuperacion de avisos bloqueados como la franja de
horario laboral. No los resolvemos por decision propia: los exponemos como parametros con un valor
por defecto documentado (`ventana_recuperacion_minutos`, `hora_inicio_laboral`, `hora_fin_laboral`,
`dias_laborables`, `respetar_horario_laboral`), de modo que el negocio pueda fijarlos por entorno
sin tocar codigo y la decision quede visible en la configuracion del despliegue.

La politica de reintentos (`max_attempts_por_defecto=3`, `backoff_minutos=(1, 5, 15)`) NO es un gap:
es la propuesta literal de REQ-134 / AC-SMTP-04 («3 intentos a 1, 5 y 15 min»).
"""

import os
import socket
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from apps.avisos.motor.errores import ErrorMotorAvisos

# La columna `aviso_correo.locked_by` es VARCHAR2(60 CHAR): el identificador se trunca ahi.
LONGITUD_MAXIMA_IDENTIFICADOR_WORKER = 60

# Numero maximo de horas de la jornada, segun el convenio de `datetime.hour` (0..23).
HORA_MINIMA = 0
HORA_MAXIMA = 23

# Convenio de `datetime.weekday()`: 0 = lunes .. 6 = domingo.
DIA_MINIMO = 0
DIA_MAXIMO = 6

#: Valores por defecto de cada clave de `settings.AVISOS_MOTOR`.
DEFECTOS: dict[str, Any] = {
    "habilitado": True,
    "intervalo_despacho_segundos": 30,
    "intervalo_recuperacion_segundos": 60,
    "ventana_recuperacion_minutos": 15,
    "tamano_lote": 25,
    "max_attempts_por_defecto": 3,
    "backoff_minutos": (1, 5, 15),
    "smtp_timeout_segundos": 30,
    "respetar_horario_laboral": True,
    "hora_inicio_laboral": 8,
    "hora_fin_laboral": 20,
    "dias_laborables": (0, 1, 2, 3, 4),
}

_FALSOS = {"", "0", "false", "f", "no", "n", "off", "none", "[]", "()"}


@dataclass(frozen=True, slots=True)
class ConfiguracionMotorAvisos:
    """
    Fotografia inmutable de los parametros de operacion del motor de avisos.

    Es inmutable a proposito: el despachador y el recuperador comparten la misma instancia
    durante un ciclo y nadie debe poder reescribir el tamano de lote o la ventana a mitad.
    """

    habilitado: bool
    intervalo_despacho_segundos: int
    intervalo_recuperacion_segundos: int
    ventana_recuperacion_minutos: int
    tamano_lote: int
    max_attempts_por_defecto: int
    backoff_minutos: tuple[int, ...]
    smtp_timeout_segundos: int
    respetar_horario_laboral: bool
    hora_inicio_laboral: int
    hora_fin_laboral: int
    dias_laborables: tuple[int, ...]
    identificador_worker: str


def identificador_worker_por_defecto() -> str:
    """
    Identificador del proceso que toma el lock de un aviso (`aviso_correo.locked_by`).

    Combina host y PID para que, ante varias replicas del contenedor, el lock diga exactamente
    quien lo tiene. Se trunca a 60 caracteres por el ancho de la columna.
    """

    return f"{socket.gethostname()}:{os.getpid()}"[:LONGITUD_MAXIMA_IDENTIFICADOR_WORKER]


def _fallo(clave: str, detalle: str) -> ErrorMotorAvisos:
    """Construye el error de dominio con el mensaje en espanol que identifica la clave culpable."""

    return ErrorMotorAvisos(f"Configuracion invalida del motor de avisos en AVISOS_MOTOR['{clave}']: {detalle}.")


def _booleano(bruto: Any, clave: str) -> bool:
    """Interpreta un valor de settings como booleano, admitiendo tambien la cadena cruda del entorno."""

    if isinstance(bruto, bool):
        return bruto
    if isinstance(bruto, str):
        return bruto.strip().lower() not in _FALSOS
    if isinstance(bruto, int):
        return bruto != 0
    raise _fallo(clave, f"se esperaba un booleano y se recibio {type(bruto).__name__}")


def _entero(bruto: Any, clave: str, *, minimo: int, maximo: int | None = None) -> int:
    """Interpreta un valor de settings como entero y comprueba que cae en el rango admitido."""

    if isinstance(bruto, bool) or not isinstance(bruto, int | str):
        raise _fallo(clave, f"se esperaba un entero y se recibio {type(bruto).__name__}")
    try:
        valor = int(bruto)
    except ValueError as error:
        raise _fallo(clave, f"'{bruto}' no es un entero") from error
    if valor < minimo:
        raise _fallo(clave, f"{valor} es menor que el minimo admitido ({minimo})")
    if maximo is not None and valor > maximo:
        raise _fallo(clave, f"{valor} es mayor que el maximo admitido ({maximo})")
    return valor


def _tupla_de_enteros(bruto: Any, clave: str, *, minimo: int, maximo: int | None = None) -> tuple[int, ...]:
    """
    Interpreta un valor de settings como una tupla de enteros no vacia.

    Admite tupla, lista y cadena separada por comas (la forma natural de una variable de entorno).
    """

    if isinstance(bruto, str):
        elementos: list[Any] = [parte.strip() for parte in bruto.split(",") if parte.strip()]
    elif isinstance(bruto, list | tuple):
        elementos = list(bruto)
    else:
        raise _fallo(clave, f"se esperaba una secuencia de enteros y se recibio {type(bruto).__name__}")

    if not elementos:
        raise _fallo(clave, "la secuencia no puede estar vacia")
    return tuple(_entero(elemento, clave, minimo=minimo, maximo=maximo) for elemento in elementos)


def configuracion_motor() -> ConfiguracionMotorAvisos:
    """
    Construye la configuracion del motor leyendo `settings.AVISOS_MOTOR`.

    Cada clave ausente cae en su default documentado en `DEFECTOS`, asi que el motor arranca
    aunque el despliegue no parametrice nada. Un valor presente pero invalido NO se corrige en
    silencio: se levanta `ErrorMotorAvisos`, porque un lote de tamano cero o una franja laboral
    imposible dejarian la cola parada sin que nadie se enterase.

    Returns:
        ConfiguracionMotorAvisos: parametros validados e inmutables del motor.

    Raises:
        ErrorMotorAvisos: si alguna clave configurada tiene un valor fuera de rango o de tipo.
    """

    bruto: dict[str, Any] = getattr(settings, "AVISOS_MOTOR", {}) or {}

    def leer(clave: str) -> Any:
        return bruto.get(clave, DEFECTOS[clave])

    hora_inicio = _entero(leer("hora_inicio_laboral"), "hora_inicio_laboral", minimo=HORA_MINIMA, maximo=HORA_MAXIMA)
    hora_fin = _entero(leer("hora_fin_laboral"), "hora_fin_laboral", minimo=HORA_MINIMA, maximo=HORA_MAXIMA)
    if hora_inicio >= hora_fin:
        raise _fallo("hora_fin_laboral", f"la hora de fin ({hora_fin}) debe ser posterior a la de inicio ({hora_inicio})")

    identificador = str(bruto.get("identificador_worker") or identificador_worker_por_defecto())
    identificador = identificador.strip()[:LONGITUD_MAXIMA_IDENTIFICADOR_WORKER]
    if not identificador:
        raise _fallo("identificador_worker", "el identificador del worker no puede estar vacio")

    return ConfiguracionMotorAvisos(
        habilitado=_booleano(leer("habilitado"), "habilitado"),
        intervalo_despacho_segundos=_entero(leer("intervalo_despacho_segundos"), "intervalo_despacho_segundos", minimo=1),
        intervalo_recuperacion_segundos=_entero(leer("intervalo_recuperacion_segundos"), "intervalo_recuperacion_segundos", minimo=1),
        ventana_recuperacion_minutos=_entero(leer("ventana_recuperacion_minutos"), "ventana_recuperacion_minutos", minimo=1),
        tamano_lote=_entero(leer("tamano_lote"), "tamano_lote", minimo=1),
        max_attempts_por_defecto=_entero(leer("max_attempts_por_defecto"), "max_attempts_por_defecto", minimo=1),
        backoff_minutos=_tupla_de_enteros(leer("backoff_minutos"), "backoff_minutos", minimo=0),
        smtp_timeout_segundos=_entero(leer("smtp_timeout_segundos"), "smtp_timeout_segundos", minimo=1),
        respetar_horario_laboral=_booleano(leer("respetar_horario_laboral"), "respetar_horario_laboral"),
        hora_inicio_laboral=hora_inicio,
        hora_fin_laboral=hora_fin,
        dias_laborables=_tupla_de_enteros(leer("dias_laborables"), "dias_laborables", minimo=DIA_MINIMO, maximo=DIA_MAXIMO),
        identificador_worker=identificador,
    )


__all__ = [
    "DEFECTOS",
    "DIA_MAXIMO",
    "DIA_MINIMO",
    "HORA_MAXIMA",
    "HORA_MINIMA",
    "LONGITUD_MAXIMA_IDENTIFICADOR_WORKER",
    "ConfiguracionMotorAvisos",
    "configuracion_motor",
    "identificador_worker_por_defecto",
]
