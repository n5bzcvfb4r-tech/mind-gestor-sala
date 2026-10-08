"""
Catalogo CERRADO de estados de envio del aviso y grafo de transiciones (ARC-115, REQ-132, REQ-142).

Este modulo es la FUENTE UNICA DE VERDAD del ciclo de vida de la entrega: ningun despachador,
servicio ni vista puede volver a codificar el grafo con if/elif: todos preguntan aqui a traves de
`transicion_permitida()` / `validar_transicion()`.

Los valores del enumerado son identicos a los codigos persistidos: coinciden uno a uno con el
CHECK `ck_aviso_correo_status` del DDL y con `ESTADOS_AVISO_CORREO` del modelo
`apps.core.models.transaccional`. Aqui NO se importa nada de Django a proposito: el modulo debe
ser importable sin `django.setup()` (lo usan tanto el proceso servidor como el consumidor).

Todas las funciones son puras: validan en memoria, no leen ni escriben en la base de datos.
"""

from enum import Enum

from apps.avisos.motor.errores import AvisoYaEntregadoError, TransicionAvisoNoPermitidaError

# Identificador de reemplazo cuando el llamante valida una transicion sin disponer todavia del uuid
# del aviso (por ejemplo, al validar una transicion candidata antes de cargar la fila).
AVISO_SIN_IDENTIFICAR = "sin identificar"

ESTADO_FUERA_DE_CATALOGO = "El estado de envio «{valor}» no pertenece al catalogo cerrado de estados de aviso: {catalogo}."


class EstadoAviso(str, Enum):
    """Estados de envio de un aviso por correo (REQ-142 regla 1; CHECK `ck_aviso_correo_status`)."""

    PENDIENTE = "PENDIENTE"
    ENVIANDO = "ENVIANDO"
    ENVIADO = "ENVIADO"
    FALLIDO = "FALLIDO"
    DESCARTADO = "DESCARTADO"
    SUPRIMIDO = "SUPRIMIDO"


#: Estados sin salida en el ciclo de vida de la entrega.
#:
#: `ENVIADO` es terminal por REQ-142 regla 2: «ENVIADO es un estado final: un aviso enviado no pasa
#: a FALLIDO ni a DESCARTADO». `SUPRIMIDO` lo es por REQ-141: «un aviso en estado SUPRIMIDO no tiene
#: entrega», de modo que nunca vuelve a la cola.
#:
#: `FALLIDO` y `DESCARTADO` NO son terminales en este sentido: no tienen continuacion automatica
#: (REQ-134 regla 5, REQ-142 regla 4), pero admiten el reenvio manual del ADMINISTRADOR.
ESTADOS_TERMINALES: frozenset[EstadoAviso] = frozenset({EstadoAviso.ENVIADO, EstadoAviso.SUPRIMIDO})

#: Grafo de las transiciones que ejecuta el despachador por si solo.
#:
#: REQ-132: «las unicas transiciones admitidas son PENDIENTE -> ENVIANDO y ENVIANDO -> ENVIADO,
#: ENVIANDO -> PENDIENTE o ENVIANDO -> FALLIDO». A eso se suma ENVIANDO -> DESCARTADO (REQ-141,
#: AC-SMTP-04: rechazo permanente 5xx) y PENDIENTE -> SUPRIMIDO (REQ-140 / ARC-115: aviso no
#: entregable, sin destinatario o con composicion incompleta).
TRANSICIONES_AUTOMATICAS: dict[EstadoAviso, frozenset[EstadoAviso]] = {
    EstadoAviso.PENDIENTE: frozenset({EstadoAviso.ENVIANDO, EstadoAviso.SUPRIMIDO}),
    EstadoAviso.ENVIANDO: frozenset({EstadoAviso.ENVIADO, EstadoAviso.PENDIENTE, EstadoAviso.FALLIDO, EstadoAviso.DESCARTADO}),
    EstadoAviso.ENVIADO: frozenset(),  # terminal, NO reactivable (REQ-142 regla 2)
    EstadoAviso.FALLIDO: frozenset(),  # sin reintento automatico (REQ-134 regla 5)
    EstadoAviso.DESCARTADO: frozenset(),  # sin reintento automatico (REQ-142 regla 4)
    EstadoAviso.SUPRIMIDO: frozenset(),
}

#: Transiciones adicionales que SOLO puede ordenar el ADMINISTRADOR al reenviar manualmente.
#:
#: REQ-130 AC-SMTP-09 y EP-049: «reenvia manualmente un aviso fallido o descartado sobre la misma
#: solicitud», sin crear una segunda solicitud. `ENVIADO` sigue sin salida tambien por esta via.
TRANSICIONES_REENVIO_MANUAL: dict[EstadoAviso, frozenset[EstadoAviso]] = {
    EstadoAviso.FALLIDO: frozenset({EstadoAviso.ENVIANDO}),
    EstadoAviso.DESCARTADO: frozenset({EstadoAviso.ENVIANDO}),
}

#: Motivos por los que un aviso queda SUPRIMIDO (CHECK `ck_aviso_correo_motivo_sup`).
MOTIVOS_SUPRESION: frozenset[str] = frozenset(
    {
        "USUARIO_DESACTIVADO",
        "SIN_CORREO",
        "DESTINATARIO_NO_RESOLUBLE",
        "NO_RECIPIENTS",
        "COMPOSICION_INCOMPLETA",
    }
)

#: Resultados posibles de un intento de entrega (CHECK `ck_aviso_intento_result` de `aviso_correo_intento`).
RESULTADOS_INTENTO: frozenset[str] = frozenset(
    {
        "SENT",
        "TRANSIENT_ERROR",
        "PERMANENT_ERROR",
        "NO_RECIPIENTS",
        "COMPOSE_ERROR",
        "CONFIG_ERROR",
    }
)

#: Tipos de aviso por correo (CHECK `ck_aviso_correo_tipo`).
TIPOS_AVISO: frozenset[str] = frozenset(
    {
        "NEW_INCIDENT_ALERT",
        "STATUS_CHANGE_ALERT",
        "CREDENTIAL_ISSUED",
        "PASSWORD_RESET",
    }
)


def normalizar_estado(valor: str | EstadoAviso) -> EstadoAviso:
    """
    Devuelve el miembro de `EstadoAviso` que corresponde al literal persistido o al propio enumerado.

    Lanza `ValueError` si el valor no pertenece al catalogo cerrado: el catalogo no se amplia por la
    puerta de atras, ni siquiera con un codigo que la base de datos rechazaria mas tarde.
    """

    if isinstance(valor, EstadoAviso):
        return valor
    if isinstance(valor, str):
        try:
            return EstadoAviso(valor)
        except ValueError:
            pass
    catalogo = ", ".join(estado.value for estado in EstadoAviso)
    raise ValueError(ESTADO_FUERA_DE_CATALOGO.format(valor=valor, catalogo=catalogo))


def es_terminal(estado: str | EstadoAviso) -> bool:
    """Indica si el estado es final y por tanto no tiene ninguna salida (ENVIADO o SUPRIMIDO)."""

    return normalizar_estado(estado) in ESTADOS_TERMINALES


def destinos_permitidos(origen: str | EstadoAviso, *, manual: bool = False) -> frozenset[EstadoAviso]:
    """
    Devuelve los estados a los que puede pasar `origen`.

    Con `manual=False` solo cuentan las transiciones del despachador; con `manual=True` se suman las
    del reenvio que ordena el ADMINISTRADOR sobre la misma solicitud.
    """

    estado_origen = normalizar_estado(origen)
    automaticas = TRANSICIONES_AUTOMATICAS[estado_origen]
    if not manual:
        return automaticas
    return automaticas | TRANSICIONES_REENVIO_MANUAL.get(estado_origen, frozenset())


def transicion_permitida(origen: str | EstadoAviso, destino: str | EstadoAviso, *, manual: bool = False) -> bool:
    """
    Indica si el grafo admite el paso de `origen` a `destino`.

    No hay auto-transiciones declaradas: `origen == destino` nunca esta permitido.
    """

    return normalizar_estado(destino) in destinos_permitidos(origen, manual=manual)


def validar_transicion(
    origen: str | EstadoAviso,
    destino: str | EstadoAviso,
    *,
    manual: bool = False,
    notification_id: str | None = None,
) -> None:
    """
    Comprueba la transicion y lanza si no esta permitida; no devuelve nada.

    Cuando el ORIGEN es `ENVIADO` lanza `AvisoYaEntregadoError`, que es el caso especifico de la
    validacion 5 de REQ-142 y el que la base de datos defiende con el trigger
    `trg_aviso_correo_estado_final` (ORA-20060). En cualquier otro caso lanza
    `TransicionAvisoNoPermitidaError`.
    """

    estado_origen = normalizar_estado(origen)
    estado_destino = normalizar_estado(destino)
    if estado_destino in destinos_permitidos(estado_origen, manual=manual):
        return
    if estado_origen is EstadoAviso.ENVIADO:
        raise AvisoYaEntregadoError(notification_id or AVISO_SIN_IDENTIFICAR, estado_destino.value)
    raise TransicionAvisoNoPermitidaError(estado_origen.value, estado_destino.value)


__all__ = [
    "AVISO_SIN_IDENTIFICAR",
    "ESTADOS_TERMINALES",
    "ESTADO_FUERA_DE_CATALOGO",
    "MOTIVOS_SUPRESION",
    "RESULTADOS_INTENTO",
    "TIPOS_AVISO",
    "TRANSICIONES_AUTOMATICAS",
    "TRANSICIONES_REENVIO_MANUAL",
    "EstadoAviso",
    "destinos_permitidos",
    "es_terminal",
    "normalizar_estado",
    "transicion_permitida",
    "validar_transicion",
]
