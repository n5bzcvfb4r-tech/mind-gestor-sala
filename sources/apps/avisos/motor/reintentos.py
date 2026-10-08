"""
Politica de reintentos del motor de cola de avisos: clasificacion del fallo y proxima ventana (REQ-134, REQ-141, REQ-142).

Este modulo decide UNA sola cosa: dado el resultado de un intento de entrega, si procede reintentar,
cuando y con que estado se cierra la solicitud si no procede. Todas las funciones son PURAS: no leen
ni escriben en la base de datos, no tocan Django y no registran nada. Quien persista la decision es
el despachador; aqui solo se calcula.

Todos los `datetime` son NAIVE en UTC, como el resto del proyecto (el `datetime` aware esta prohibido
por la matriz de tipos). La marca temporal se obtiene de `utc_now()`.

Reglas transcritas del RFP
--------------------------
REQ-134 (reintento controlado):
  1. Solo los fallos transitorios (timeout, conexion rehusada, error temporal del servidor) dan lugar
     a reintento; un rechazo permanente no se reintenta.
  2. El numero de intentos de una solicitud nunca supera `max_attempts`.
  3. Una solicitud en ENVIADO no tiene reintentos programados ni se reprocesa.
  4. La fecha `next_attempt_at` de una solicitud es siempre POSTERIOR al instante en que se programa.
  5. Agotado el maximo de intentos, la solicitud queda FALLIDO definitiva y no vuelve a reintentarse
     de forma automatica.
  6. Un error no clasificable se considera TRANSITORIO en el primer intento y PERMANENTE a partir del
     segundo.
  La espera es creciente; la propuesta literal del RFP es de 3 intentos a 1, 5 y 15 minutos
  (`[gap: politica de reintentos no definida en el RFP]`). Los minutos salen de `config.backoff_minutos`,
  nunca de un valor incrustado aqui.

REQ-141 / REQ-142 (clasificacion por respuesta SMTP):
  - respuesta 4xx -> temporal -> reintentable.
  - respuesta 5xx permanente (p. ej. 550 buzon inexistente) -> DESCARTADO con `attempt_count = 1`, sin
    ningun reintento, con `smtp_response_code` y motivo trazados (AC-SMTP-04).

DISCREPANCIA DECLARADA DEL RFP (no la resolvemos inventando: se documenta)
-------------------------------------------------------------------------
REQ-134 regla 5 dice que, al agotar los intentos, la solicitud queda **FALLIDO** definitiva, mientras
que REQ-142 regla 4 dice que queda **DESCARTADO**. El DoD de la tarea pide cerrar como
«FALLIDO/DESCARTADO definitivo», sin decantarse. Este modulo resuelve la ambiguedad del unico modo que
deja cada codigo con un significado distinto y NO solapado:
  - **FALLIDO**    = se agotaron los reintentos de un fallo TRANSITORIO (el canal fallo; el
    ADMINISTRADOR puede reenviar con EP-049).
  - **DESCARTADO** = rechazo PERMANENTE, sin reintento, con `attempt_count = 1` (REQ-141 AC-SMTP-04 lo
    dice literalmente).
Si el negocio decide lo contrario, el cambio esta confinado a `decidir()`.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from apps.avisos.motor.configuracion import ConfiguracionMotorAvisos
from apps.avisos.motor.errores import ErrorMotorAvisos, ReintentoNoProgramableError
from apps.avisos.motor.estados import RESULTADOS_INTENTO, EstadoAviso, normalizar_estado
from apps.core.contexto import utc_now

#: Primer digito de una respuesta SMTP temporal (4xx): el servidor pide reintentar mas tarde.
PREFIJO_SMTP_TEMPORAL = "4"

#: Primer digito de una respuesta SMTP permanente (5xx): el mensaje queda rechazado sin vuelta atras.
PREFIJO_SMTP_PERMANENTE = "5"

#: Espera minima de un reintento. Protege la validacion 2 de REQ-134 («`next_attempt_at` siempre
#: posterior al instante de programacion») frente a una configuracion que traiga un 0 en el backoff.
ESPERA_MINIMA = timedelta(minutes=1)

# --- Literales de traza, en espanol (REQ-050) ----------------------------
MOTIVO_AVISO_YA_ENTREGADO = "el aviso ya consta entregado y es terminal"
MOTIVO_RECHAZO_PERMANENTE = (
    "Rechazo permanente de la entrega (respuesta SMTP {smtp_response_code}, resultado {error_code}): "
    "la solicitud se descarta sin reintento (REQ-141 AC-SMTP-04)."
)
MOTIVO_REINTENTO_PROGRAMADO = (
    "Fallo transitorio de la entrega (respuesta SMTP {smtp_response_code}, resultado {error_code}): "
    "se programa el reintento {siguiente_intento} de {max_attempts} para {next_attempt_at} (REQ-134 AC-SMTP-04)."
)
MOTIVO_INTENTOS_AGOTADOS = (
    "Fallo transitorio de la entrega (respuesta SMTP {smtp_response_code}, resultado {error_code}): "
    "agotado el maximo de intentos ({attempt_count} de {max_attempts}), la solicitud queda fallida "
    "definitiva y no se reintenta de forma automatica (REQ-134 regla 5)."
)
NEXT_ATTEMPT_NO_FUTURA = (
    "La proxima ventana de reintento calculada ({next_attempt_at}) no es posterior al instante de programacion ({ahora}): "
    "la politica de reintentos del motor de avisos dejaria la solicitud en bucle (REQ-134 validacion 2)."
)

#: Valor con el que se traza un codigo ausente, para que el motivo nunca quede con un hueco.
SIN_CODIGO = "sin codigo"


class ClaseFallo(str, Enum):
    """Clasificacion cerrada de un fallo de entrega (REQ-134 regla 1; REQ-141)."""

    TRANSITORIO = "TRANSITORIO"
    PERMANENTE = "PERMANENTE"


#: Resultados de intento (`aviso_correo_intento.result`) que son fallos TRANSITORIOS.
#:
#: `CONFIG_ERROR` entra aqui a proposito: el servidor no esta bien configurado o no autentica, pero el
#: mensaje en si NO ha sido rechazado y el administrador puede corregirlo dentro de la ventana de
#: reintentos.
RESULTADOS_TRANSITORIOS: frozenset[str] = frozenset({"TRANSIENT_ERROR", "CONFIG_ERROR"})

#: Resultados de intento que son fallos PERMANENTES: no se arreglan reintentando la misma solicitud.
RESULTADOS_PERMANENTES: frozenset[str] = frozenset({"PERMANENT_ERROR", "NO_RECIPIENTS", "COMPOSE_ERROR"})

#: Resultados del catalogo `RESULTADOS_INTENTO` que este modulo sabe clasificar como fallo.
#: `SENT` queda fuera por definicion: un envio correcto no pasa por la politica de reintentos.
RESULTADOS_CLASIFICABLES: frozenset[str] = (RESULTADOS_TRANSITORIOS | RESULTADOS_PERMANENTES) & RESULTADOS_INTENTO

#: Numero de intento a partir del cual un error NO clasificable deja de considerarse transitorio
#: (REQ-134 regla 6: transitorio en el primer intento, permanente a partir del segundo).
INTENTO_LIMITE_NO_CLASIFICABLE = 1


@dataclass(frozen=True, slots=True)
class DecisionReintento:
    """
    Resultado de aplicar la politica de reintentos a un intento de entrega fallido.

    Es inmutable a proposito: el despachador la calcula una vez y la persiste tal cual; nadie debe
    poder reescribir la ventana o el estado destino entre el calculo y el UPDATE.

    Attributes:
        clase (ClaseFallo): clasificacion del fallo.
        reintentar (bool): `True` si procede programar un nuevo intento automatico.
        next_attempt_at (datetime | None): proxima ventana de entrega, NAIVE en UTC; `None` si no hay reintento.
        estado_destino (EstadoAviso): PENDIENTE si se reintenta; FALLIDO o DESCARTADO si no.
        motivo (str): texto en espanol que explica la decision, para la traza del intento.
    """

    clase: ClaseFallo
    reintentar: bool
    next_attempt_at: datetime | None
    estado_destino: EstadoAviso
    motivo: str


def _normalizar_codigo(valor: str | None) -> str | None:
    """Devuelve el codigo sin espacios y en mayusculas, o `None` si viene vacio o ausente."""

    if valor is None:
        return None
    limpio = str(valor).strip()
    return limpio.upper() if limpio else None


def clasificar_fallo(*, smtp_response_code: str | None, error_code: str | None, attempt_count: int) -> ClaseFallo:
    """
    Clasifica el fallo de un intento de entrega como TRANSITORIO o PERMANENTE (REQ-134 regla 1; REQ-141).

    Manda la respuesta SMTP cuando la hay, porque es lo que ha dicho el servidor remoto: `4xx` es un
    error temporal y `5xx` un rechazo definitivo (p. ej. 550 buzon inexistente). Sin respuesta SMTP se
    mira el resultado del intento (`aviso_correo_intento.result`). Si no hay nada que permita
    clasificar, se aplica la regla 6 de REQ-134: transitorio en el primer intento, permanente despues.

    Args:
        smtp_response_code (str | None): codigo de respuesta SMTP del servidor, si lo hubo.
        error_code (str | None): resultado del intento, del catalogo `RESULTADOS_INTENTO`.
        attempt_count (int): contador de intentos YA incrementado con el intento que acaba de fallar.

    Returns:
        ClaseFallo: `TRANSITORIO` si el reintento tiene sentido; `PERMANENTE` si no lo tiene.
    """

    codigo_smtp = _normalizar_codigo(smtp_response_code)
    if codigo_smtp:
        if codigo_smtp.startswith(PREFIJO_SMTP_TEMPORAL):
            return ClaseFallo.TRANSITORIO
        if codigo_smtp.startswith(PREFIJO_SMTP_PERMANENTE):
            return ClaseFallo.PERMANENTE

    resultado = _normalizar_codigo(error_code)
    if resultado in RESULTADOS_TRANSITORIOS:
        return ClaseFallo.TRANSITORIO
    if resultado in RESULTADOS_PERMANENTES:
        return ClaseFallo.PERMANENTE

    # No clasificable: ni el servidor ni el motor han dicho de que fallo se trata (REQ-134 regla 6).
    return ClaseFallo.TRANSITORIO if attempt_count <= INTENTO_LIMITE_NO_CLASIFICABLE else ClaseFallo.PERMANENTE


def espera_de_reintento(attempt_count: int, config: ConfiguracionMotorAvisos) -> timedelta:
    """
    Espera creciente que corresponde al intento numero `attempt_count` (REQ-134; propuesta del RFP: 1, 5 y 15 min).

    El intento nº1 usa `config.backoff_minutos[0]`, el nº2 el `[1]`, y asi sucesivamente. Cuando
    `attempt_count` supera la longitud de la tupla se repite el ULTIMO valor: la espera deja de crecer,
    pero no se dispara. Un `attempt_count` menor que 1 se trata como 1.

    Args:
        attempt_count (int): numero del intento que acaba de fallar.
        config (ConfiguracionMotorAvisos): parametros del motor; de aqui sale `backoff_minutos`.

    Returns:
        timedelta: espera estrictamente positiva. Si la configuracion trae un 0 se devuelve
        `ESPERA_MINIMA`, porque `next_attempt_at` tiene que ser FUTURA (REQ-134 validacion 2).
    """

    backoff = config.backoff_minutos
    indice = min(max(attempt_count, 1), len(backoff)) - 1
    espera = timedelta(minutes=backoff[indice])
    return espera if espera > ESPERA_MINIMA else ESPERA_MINIMA


def programar_siguiente_intento(
    attempt_count: int,
    config: ConfiguracionMotorAvisos,
    *,
    ahora: datetime | None = None,
) -> datetime:
    """
    Calcula la proxima ventana de entrega sumando la espera del backoff al instante de programacion.

    Args:
        attempt_count (int): numero del intento que acaba de fallar.
        config (ConfiguracionMotorAvisos): parametros del motor.
        ahora (datetime | None): instante de programacion, NAIVE en UTC; si es `None` se usa `utc_now()`.

    Returns:
        datetime: `next_attempt_at` NAIVE en UTC, siempre POSTERIOR a `ahora` (REQ-134 validacion 2).

    Raises:
        ErrorMotorAvisos: si el calculo no diese una fecha futura (configuracion incoherente).
    """

    instante = ahora if ahora is not None else utc_now()
    next_attempt_at = instante + espera_de_reintento(attempt_count, config)
    if next_attempt_at <= instante:
        raise ErrorMotorAvisos(NEXT_ATTEMPT_NO_FUTURA.format(next_attempt_at=next_attempt_at, ahora=instante))
    return next_attempt_at


def quedan_intentos(attempt_count: int, max_attempts: int) -> bool:
    """
    Indica si la solicitud puede consumir todavia un intento mas (REQ-134 regla 2).

    Args:
        attempt_count (int): intentos ya consumidos, incluido el que acaba de fallar.
        max_attempts (int): maximo de intentos de la solicitud.

    Returns:
        bool: `True` mientras `attempt_count` sea menor que `max_attempts`.
    """

    return attempt_count < max_attempts


def decidir(
    *,
    notification_id: str,
    estado_actual: EstadoAviso | str,
    attempt_count: int,
    max_attempts: int,
    smtp_response_code: str | None,
    error_code: str | None,
    config: ConfiguracionMotorAvisos,
    ahora: datetime | None = None,
) -> DecisionReintento:
    """
    Aplica la politica de reintentos a un intento de entrega fallido (REQ-134, REQ-141 AC-SMTP-04).

    Es la funcion central del modulo: clasifica el fallo, comprueba el presupuesto de intentos y
    devuelve la decision completa (si se reintenta, cuando y con que estado se cierra si no).

    Args:
        notification_id (str): identificador del aviso, solo para la traza y los errores.
        estado_actual (EstadoAviso | str): estado de envio actual de la solicitud.
        attempt_count (int): contador de intentos YA incrementado con el intento que acaba de fallar.
        max_attempts (int): maximo de intentos de la solicitud.
        smtp_response_code (str | None): codigo de respuesta SMTP del servidor, si lo hubo.
        error_code (str | None): resultado del intento, del catalogo `RESULTADOS_INTENTO`.
        config (ConfiguracionMotorAvisos): parametros del motor.
        ahora (datetime | None): instante de programacion, NAIVE en UTC; si es `None` se usa `utc_now()`.

    Returns:
        DecisionReintento: decision inmutable con la clase del fallo, la ventana y el estado destino.

    Raises:
        ReintentoNoProgramableError: si la solicitud ya consta ENVIADO (REQ-134 regla 3), estado final
            que no tiene reintentos programados ni se reprocesa.
        ErrorMotorAvisos: si la ventana calculada no fuese posterior al instante de programacion.
    """

    if normalizar_estado(estado_actual) is EstadoAviso.ENVIADO:
        raise ReintentoNoProgramableError(notification_id, MOTIVO_AVISO_YA_ENTREGADO)

    clase = clasificar_fallo(smtp_response_code=smtp_response_code, error_code=error_code, attempt_count=attempt_count)
    codigo_smtp = _normalizar_codigo(smtp_response_code) or SIN_CODIGO
    resultado = _normalizar_codigo(error_code) or SIN_CODIGO

    if clase is ClaseFallo.PERMANENTE:
        # REQ-141 AC-SMTP-04: rechazo permanente -> DESCARTADO, sin ningun reintento.
        return DecisionReintento(
            clase=clase,
            reintentar=False,
            next_attempt_at=None,
            estado_destino=EstadoAviso.DESCARTADO,
            motivo=MOTIVO_RECHAZO_PERMANENTE.format(smtp_response_code=codigo_smtp, error_code=resultado),
        )

    if quedan_intentos(attempt_count, max_attempts):
        next_attempt_at = programar_siguiente_intento(attempt_count, config, ahora=ahora)
        return DecisionReintento(
            clase=clase,
            reintentar=True,
            next_attempt_at=next_attempt_at,
            estado_destino=EstadoAviso.PENDIENTE,
            motivo=MOTIVO_REINTENTO_PROGRAMADO.format(
                smtp_response_code=codigo_smtp,
                error_code=resultado,
                siguiente_intento=attempt_count + 1,
                max_attempts=max_attempts,
                next_attempt_at=next_attempt_at,
            ),
        )

    # REQ-134 regla 5: agotado el maximo, la solicitud queda FALLIDO definitiva (ver discrepancia
    # declarada en el docstring del modulo) y no se reintenta de forma automatica.
    return DecisionReintento(
        clase=clase,
        reintentar=False,
        next_attempt_at=None,
        estado_destino=EstadoAviso.FALLIDO,
        motivo=MOTIVO_INTENTOS_AGOTADOS.format(
            smtp_response_code=codigo_smtp,
            error_code=resultado,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
        ),
    )


__all__ = [
    "ESPERA_MINIMA",
    "INTENTO_LIMITE_NO_CLASIFICABLE",
    "MOTIVO_AVISO_YA_ENTREGADO",
    "MOTIVO_INTENTOS_AGOTADOS",
    "MOTIVO_RECHAZO_PERMANENTE",
    "MOTIVO_REINTENTO_PROGRAMADO",
    "NEXT_ATTEMPT_NO_FUTURA",
    "PREFIJO_SMTP_PERMANENTE",
    "PREFIJO_SMTP_TEMPORAL",
    "RESULTADOS_CLASIFICABLES",
    "RESULTADOS_PERMANENTES",
    "RESULTADOS_TRANSITORIOS",
    "SIN_CODIGO",
    "ClaseFallo",
    "DecisionReintento",
    "clasificar_fallo",
    "decidir",
    "espera_de_reintento",
    "programar_siguiente_intento",
    "quedan_intentos",
]
