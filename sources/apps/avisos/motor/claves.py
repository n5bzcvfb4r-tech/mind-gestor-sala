"""
Construccion de la clave de idempotencia del outbox de avisos: `aviso_correo.notification_key` (T.5 ARC-115, REQ-137).

POR QUE UNA CLAVE DETERMINISTA Y LEGIBLE, Y NO UN HASH
======================================================
El mecanismo de idempotencia NO esta en este modulo: esta en la base de datos. La columna
`notification_key VARCHAR2(120 CHAR) NOT NULL` lleva la restriccion `uk_aviso_correo_notif_key
UNIQUE`, de modo que el SEGUNDO INSERT de un mismo aviso choca con ORA-00001 y el reintento de la
API, el doble submit del formulario o el reproceso del despachador no producen una segunda
solicitud ni, por tanto, un segundo correo. Este modulo solo tiene que producir, a partir de los
MISMOS datos de negocio, EXACTAMENTE la misma cadena: por eso las funciones son puras y
deterministas, sin reloj, sin azar y sin estado.

Siendo la unicidad el unico requisito tecnico, un hash (un SHA-256 del tipo mas los identificadores)
serviria igual de bien para colisionar... y para nada mas. La clave se escoge LEGIBLE porque la
columna se muestra en el panel de supervision de avisos (REQ-145): leyendo
`STATUS_CHANGE_ALERT:INC:4312:HIST:9871` el administrador reconoce de un vistazo que ese aviso
pertenece a la incidencia 4312 y al asiento de historico 9871, y puede ir a buscarlos. Con un hash
tendria una cadena opaca que solo se puede comparar, nunca interpretar, y el diagnostico de un
aviso atascado exigiria recorrer el camino inverso consultando otras tablas. La legibilidad no
debilita la idempotencia: la igualdad de cadenas es la misma con digitos que con hexadecimales.

QUE REFERENCIAS LLEVA CADA TIPO
===============================
Las fija la CHECK `ck_aviso_correo_vinculo` del DDL (changelog `14-aviso-correo.xml`), y la nota de
inventario de T.5 la resume como «idempotencia por tipo + incidencia + entrada de historial»:

- `NEW_INCIDENT_ALERT`  -> `incident_id` NOT NULL y `history_entry_id` NULL.
- `STATUS_CHANGE_ALERT` -> `incident_id` NOT NULL y `history_entry_id` NOT NULL.
- `CREDENTIAL_ISSUED` / `PASSWORD_RESET` -> sin incidencia ni asiento, con `recipient_user_id` NOT NULL.

`construir_clave` valida esas mismas combinaciones ANTES de componer la cadena, de modo que una
llamada incoherente falla aqui, en espanol, y no mas tarde como una violacion de CHECK de Oracle
cuya unica pista seria el nombre de la restriccion.

EL TOPE DE 120 CARACTERES
=========================
`LONGITUD_MAXIMA_CLAVE` es la longitud declarada de la columna. Los identificadores que entran en la
clave (`incident_id`, `history_entry_id`, `recipient_user_id`) son NUMBER de Oracle generados por
IDENTITY, asi que el caso peor realista cabe holgadamente: ni el prefijo mas largo
(`STATUS_CHANGE_ALERT:INC::HIST:`, 30 caracteres) con identificadores de quince digitos se acerca al
tope. Aun asi `validar_clave` lo comprueba SIEMPRE, porque el unico argumento de longitud no acotada
es el `discriminante` de los avisos de credencial, y porque el fallo tiene que saltar en Python con
un mensaje en espanol que diga que clave sobro de largo, y no como un ORA-12899 ("value too large
for column") en mitad del INSERT, dentro ya de la transaccion del alta.
"""

from apps.avisos.motor.errores import ClaveAvisoInvalidaError
from apps.avisos.motor.estados import TIPOS_AVISO

#: Longitud de `aviso_correo.notification_key` (VARCHAR2(120 CHAR)); ninguna clave puede excederla.
LONGITUD_MAXIMA_CLAVE: int = 120

#: Separador de los segmentos de la clave. No aparece en ningun identificador, asi que la clave es inequivoca.
SEPARADOR: str = ":"

# Literales del catalogo cerrado `ck_aviso_correo_tipo`, necesarios para despachar. No amplian
# `TIPOS_AVISO`: son un subconjunto suyo y `construir_clave` comprueba la pertenencia antes de usarlos.
_TIPO_ALTA = "NEW_INCIDENT_ALERT"
_TIPO_CAMBIO_ESTADO = "STATUS_CHANGE_ALERT"
_TIPO_CREDENCIAL_EMITIDA = "CREDENTIAL_ISSUED"
_TIPO_RESTABLECIMIENTO = "PASSWORD_RESET"

#: Los dos tipos de aviso de credencial, que no cuelgan de ninguna incidencia.
_TIPOS_CREDENCIAL: frozenset[str] = frozenset({_TIPO_CREDENCIAL_EMITIDA, _TIPO_RESTABLECIMIENTO})

# Etiquetas de los segmentos de referencia. Son parte del contrato de la clave: cambiarlas cambia
# la clave de todos los avisos futuros y rompe la idempotencia frente a los ya encolados.
_ETIQUETA_INCIDENCIA = "INC"
_ETIQUETA_HISTORICO = "HIST"
_ETIQUETA_USUARIO = "USR"

# --- Motivos de rechazo ---------------------------------------------------
_MOTIVO_TIPO_DESCONOCIDO = "el tipo de aviso no pertenece al catalogo cerrado de `ck_aviso_correo_tipo`."
_MOTIVO_VACIA = "esta vacia o solo contiene espacios."
_MOTIVO_ESPACIOS = "empieza o termina con espacios en blanco."
_MOTIVO_DEMASIADO_LARGA = "ocupa {longitud} caracteres y el maximo admitido por la columna es {maximo}."
_MOTIVO_FALTA_INCIDENCIA = "el aviso de tipo «{tipo}» exige `incident_id` y no se ha recibido."
_MOTIVO_SOBRA_INCIDENCIA = "el aviso de tipo «{tipo}» no cuelga de ninguna incidencia y se ha recibido `incident_id`."
_MOTIVO_FALTA_HISTORICO = "el aviso de tipo «{tipo}» exige `history_entry_id` y no se ha recibido."
_MOTIVO_SOBRA_HISTORICO = "el aviso de tipo «{tipo}» no cuelga de ninguna entrada de historial y se ha recibido `history_entry_id`."
_MOTIVO_FALTA_DESTINATARIO = "el aviso de tipo «{tipo}» exige `recipient_user_id` y no se ha recibido."
_MOTIVO_SOBRA_DESTINATARIO = "el aviso de tipo «{tipo}» se dirige a un colectivo y no admite `recipient_user_id`."
_MOTIVO_FALTA_DISCRIMINANTE = (
    "el aviso de tipo «{tipo}» exige un discriminante no vacio: sin el, una segunda emision al mismo usuario "
    "reutilizaria la clave de la primera y el aviso se perderia."
)
_MOTIVO_SOBRA_DISCRIMINANTE = "el aviso de tipo «{tipo}» se identifica por sus referencias y no admite discriminante."


def validar_clave(clave: str) -> str:
    """
    Comprueba que `clave` es una `notification_key` admisible y la devuelve tal cual.

    Reglas, en el orden en que se aplican: la clave no puede estar vacia ni contener solo espacios,
    no puede empezar ni terminar con espacios en blanco y no puede exceder `LONGITUD_MAXIMA_CLAVE`.

    El espacio sobrante se RECHAZA y no se recorta en silencio a proposito. La clave es el testigo de
    idempotencia: si esta funcion limpiase el argumento, dos llamantes que pasan `"X"` y `" X"`
    acabarian compartiendo fila sin haberlo pedido, y un espacio colado en un discriminante (que es
    el unico segmento que llega de fuera) pasaria inadvertido en lugar de delatar el fallo del
    llamante. Devolver la cadena validada permite componerla en una sola expresion,
    `return validar_clave(SEPARADOR.join(...))`, que es como la usan todos los constructores.

    Lanza `ClaveAvisoInvalidaError` con el motivo concreto si alguna regla no se cumple.
    """

    if not clave or not clave.strip():
        raise ClaveAvisoInvalidaError(clave, _MOTIVO_VACIA)

    if clave != clave.strip():
        raise ClaveAvisoInvalidaError(clave, _MOTIVO_ESPACIOS)

    if len(clave) > LONGITUD_MAXIMA_CLAVE:
        raise ClaveAvisoInvalidaError(clave, _MOTIVO_DEMASIADO_LARGA.format(longitud=len(clave), maximo=LONGITUD_MAXIMA_CLAVE))

    return clave


def _exigir(condicion: bool, notification_type: str, motivo: str) -> None:
    """Lanza `ClaveAvisoInvalidaError` con el motivo formateado si la condicion de `ck_aviso_correo_vinculo` no se cumple."""

    if not condicion:
        raise ClaveAvisoInvalidaError(notification_type, motivo.format(tipo=notification_type))


def clave_aviso_alta(incident_id: int) -> str:
    """
    Clave del aviso de alta de incidencia: `NEW_INCIDENT_ALERT:INC:{incident_id}`.

    Hay EXACTAMENTE un aviso de alta por incidencia (REQ-130, AC-AVI-01), asi que la incidencia por
    si sola identifica el aviso y la clave no necesita mas segmentos. Es la contrapartida en codigo
    del indice unico parcial `ux_aviso_correo_alta` del DDL.
    """

    return validar_clave(SEPARADOR.join((_TIPO_ALTA, _ETIQUETA_INCIDENCIA, str(incident_id))))


def clave_aviso_cambio_estado(incident_id: int, history_entry_id: int) -> str:
    """
    Clave del aviso de cambio de estado: `STATUS_CHANGE_ALERT:INC:{incident_id}:HIST:{history_entry_id}`.

    La entrada de historial ya es unica por si misma, pero la incidencia se conserva en la clave
    porque es lo que la hace LEGIBLE en el panel de supervision: el administrador ve a que incidencia
    pertenece el aviso sin tener que resolver antes el asiento. Es la contrapartida en codigo del
    indice unico parcial `ux_aviso_correo_cambio_estado`, que tambien indexa la pareja completa.
    """

    segmentos = (_TIPO_CAMBIO_ESTADO, _ETIQUETA_INCIDENCIA, str(incident_id), _ETIQUETA_HISTORICO, str(history_entry_id))
    return validar_clave(SEPARADOR.join(segmentos))


def clave_aviso_credencial(notification_type: str, recipient_user_id: int, discriminante: str) -> str:
    """
    Clave de un aviso de credencial: `{notification_type}:USR:{recipient_user_id}:{discriminante}`.

    `notification_type` tiene que ser `CREDENTIAL_ISSUED` o `PASSWORD_RESET`, los dos unicos tipos
    que `ck_aviso_correo_vinculo` admite sin incidencia ni asiento de historico.

    El `discriminante` es OBLIGATORIO y no puede venir vacio. A diferencia de la incidencia o del
    asiento, el usuario destinatario NO identifica una emision concreta: el mismo usuario puede pedir
    el restablecimiento de su contrasena tantas veces como quiera. Sin discriminante, el segundo
    restablecimiento generaria la misma clave que el primero, el INSERT chocaria con ORA-00001 y el
    aviso se perderia, dejando al usuario esperando un correo que nunca llega. Vale como discriminante
    cualquier dato que distinga las dos emisiones; lo habitual es la marca temporal de emision en
    formato `%Y%m%d%H%M%S%f`.

    Lanza `ClaveAvisoInvalidaError` si el tipo no es de credencial o si el discriminante falta.
    """

    if notification_type not in TIPOS_AVISO:
        raise ClaveAvisoInvalidaError(notification_type, _MOTIVO_TIPO_DESCONOCIDO)

    if notification_type not in _TIPOS_CREDENCIAL:
        raise ClaveAvisoInvalidaError(notification_type, _MOTIVO_SOBRA_DESTINATARIO.format(tipo=notification_type))

    if not discriminante or not discriminante.strip():
        raise ClaveAvisoInvalidaError(notification_type, _MOTIVO_FALTA_DISCRIMINANTE.format(tipo=notification_type))

    segmentos = (notification_type, _ETIQUETA_USUARIO, str(recipient_user_id), discriminante)
    return validar_clave(SEPARADOR.join(segmentos))


def construir_clave(
    notification_type: str,
    *,
    incident_id: int | None = None,
    history_entry_id: int | None = None,
    recipient_user_id: int | None = None,
    discriminante: str | None = None,
) -> str:
    """
    Punto de entrada unico: valida las referencias del tipo y despacha al constructor que corresponda.

    Las referencias admitidas y exigidas para cada tipo son LITERALMENTE las de la CHECK
    `ck_aviso_correo_vinculo`, y se comprueban en las dos direcciones: que no FALTE ninguna
    obligatoria y que no SOBRE ninguna prohibida. La segunda mitad es tan necesaria como la primera,
    porque un `history_entry_id` colado en un aviso de alta no cambiaria la clave (el constructor lo
    ignora) pero delata que el llamante esta encolando el aviso equivocado, y la fila acabaria
    rechazada por la CHECK dentro de la transaccion de negocio.

    Lanza `ClaveAvisoInvalidaError` si `notification_type` no pertenece a `TIPOS_AVISO` o si el juego
    de referencias no es el que ese tipo exige.
    """

    if notification_type not in TIPOS_AVISO:
        raise ClaveAvisoInvalidaError(notification_type, _MOTIVO_TIPO_DESCONOCIDO)

    if notification_type == _TIPO_ALTA:
        _exigir(incident_id is not None, notification_type, _MOTIVO_FALTA_INCIDENCIA)
        _exigir(history_entry_id is None, notification_type, _MOTIVO_SOBRA_HISTORICO)
        _exigir(recipient_user_id is None, notification_type, _MOTIVO_SOBRA_DESTINATARIO)
        _exigir(discriminante is None, notification_type, _MOTIVO_SOBRA_DISCRIMINANTE)
        return clave_aviso_alta(incident_id)  # type: ignore[arg-type]

    if notification_type == _TIPO_CAMBIO_ESTADO:
        _exigir(incident_id is not None, notification_type, _MOTIVO_FALTA_INCIDENCIA)
        _exigir(history_entry_id is not None, notification_type, _MOTIVO_FALTA_HISTORICO)
        _exigir(recipient_user_id is None, notification_type, _MOTIVO_SOBRA_DESTINATARIO)
        _exigir(discriminante is None, notification_type, _MOTIVO_SOBRA_DISCRIMINANTE)
        return clave_aviso_cambio_estado(incident_id, history_entry_id)  # type: ignore[arg-type]

    # CREDENTIAL_ISSUED y PASSWORD_RESET: ni incidencia ni asiento, y destinatario individual.
    _exigir(incident_id is None, notification_type, _MOTIVO_SOBRA_INCIDENCIA)
    _exigir(history_entry_id is None, notification_type, _MOTIVO_SOBRA_HISTORICO)
    _exigir(recipient_user_id is not None, notification_type, _MOTIVO_FALTA_DESTINATARIO)
    _exigir(discriminante is not None, notification_type, _MOTIVO_FALTA_DISCRIMINANTE)
    return clave_aviso_credencial(notification_type, recipient_user_id, discriminante)  # type: ignore[arg-type]


__all__ = [
    "LONGITUD_MAXIMA_CLAVE",
    "SEPARADOR",
    "clave_aviso_alta",
    "clave_aviso_cambio_estado",
    "clave_aviso_credencial",
    "construir_clave",
    "validar_clave",
]
