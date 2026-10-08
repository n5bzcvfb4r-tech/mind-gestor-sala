"""
Disparo UNICO del aviso de alta de incidencia: consumidor del evento `AvisoSolicitado` (ARC-014, EVT-001, REQ-130, REQ-132).

Este modulo es la costura entre el alta de incidencia (ARC-013, el PRODUCTOR) y el outbox de avisos
(`apps.avisos.motor.outbox`, TSK-02). No reimplementa nada del motor: no declara estados, ni tipos,
ni tabla, ni clave de idempotencia. Importa el catalogo cerrado (`TIPOS_AVISO`), el tipo literal
(`TIPO_ALTA`) y el servicio de encolado, y se limita a decidir UNA cosa: ante que evento se encola
un `NEW_INCIDENT_ALERT` y ante cuales no.

POR QUE EL CONSUMIDOR NO ABRE TRANSACCION (la razon de ser del fichero)
=======================================================================
REQ-132 / ADR-006: «la solicitud se persiste DENTRO de la misma transaccion del alta». De ahi sale
media hoja del DoD de esta unidad, porque es lo que hace que «un alta fallida o revertida no deje
ninguna solicitud»: el INSERT del aviso y el INSERT de la incidencia son el MISMO commit. Si el alta
revienta despues de encolar, el `ROLLBACK` del negocio se lleva por delante la solicitud, sin que
este modulo tenga que compensar ni borrar nada.

Por eso aqui NO hay `transaction.atomic()`, ni `commit()`, ni `rollback()`, ni `on_commit()`: ni una
linea. La frontera transaccional pertenece al servicio de alta que publica el evento. Envolver el
consumo en su propio `atomic` romperia exactamente la garantia que se pretende dar: el aviso se
confirmaria por su cuenta y sobreviviria a un alta revertida, dejando un correo anunciando una
incidencia que no existe. El unico `atomic` de todo el camino es el savepoint ANIDADO que
`ServicioOutboxAvisos.encolar` abre para absorber la colision de unicidad, y vive alli, no aqui.

EL TRANSPORTE: UNA SEÑAL DE DJANGO, NO UN BROKER
================================================
`asyncapi.yaml` declara el canal `facilities.avisos.solicitado.v1` con `x-mind-transport: internal`.
El bus en proceso de este arquetipo es `django.dispatch.Signal`: entrega SINCRONA, en el mismo hilo
y, por tanto, en la misma transaccion del productor, que es justo lo que exige el patron outbox. No
hay cola intermedia, ni proceso aparte, ni `log.info` haciendo de «publicacion»: la publicacion es
`Signal.send` y el aviso resultante es la FILA en `aviso_correo`, no la linea de log.

Se usa `Signal.send` y NUNCA `send_robust`. `send_robust` captura las excepciones de los receptores
y las devuelve como resultado: el productor seguiria adelante y confirmaria el alta creyendo que el
aviso quedo encolado cuando no fue asi, y el equipo de mantenimiento no se enteraria de la
incidencia. Con `send`, el fallo del consumidor sube hasta el alta y su transaccion se revierte: o se
dan de alta la incidencia y su aviso, o ninguno de los dos.

LA TRAMPA DEL RECEPTOR DUPLICADO
================================
`AppConfig.ready()` puede ejecutarse mas de una vez en un mismo proceso (recarga del servidor de
desarrollo, `django.setup()` repetido en pruebas o en un comando que vuelve a poblar el registro de
apps). Si el receptor se conectara sin `dispatch_uid`, cada pasada añadiria OTRO receptor y un unico
evento de alta ejecutaria el consumidor dos veces. La idempotencia del outbox salvaria la cara (el
segundo encolado devolveria `creado=False`), pero el sintoma real seria peor: trabajo y consultas
duplicados por cada alta, y una traza que miente diciendo que hubo dos disparos. `dispatch_uid` es
precisamente el mecanismo que `django.dispatch` ofrece para que la conexion sea idempotente: con
`DISPATCH_UID` fijo, la segunda llamada a `conectar_disparo_alta()` no registra nada nuevo.

QUE NO HACE ESTE MODULO
=======================
No resuelve destinatarios y no compone el correo. `encolar_aviso_alta` admite `recipient_email=None`
a proposito: la resolucion del destinatario y la composicion de la plantilla son responsabilidad de
AVI-02, y el despachador ya defiende el caso de que nunca lleguen, suprimiendo la solicitud con
`NO_RECIPIENTS` o `COMPOSICION_INCOMPLETA` en lugar de enviar un correo vacio. Meter aqui la
resolucion adelantaria a la transaccion del alta un trabajo que no le pertenece y que puede fallar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.dispatch import Signal

from apps.avisos.motor.errores import ErrorMotorAvisos
from apps.avisos.motor.estados import TIPOS_AVISO
from apps.avisos.motor.outbox import TIPO_ALTA, ResultadoEncolado, ServicioOutboxAvisos

logger = logging.getLogger(__name__)

#: Canal del evento `AvisoSolicitado` (EVT-001) tal y como lo declara `asyncapi.yaml`: productor
#: ARC-013 (alta de incidencia) -> consumidor ARC-014 (motor de avisos, este componente). Se deja
#: como constante para que la traza y las pruebas puedan nombrar el canal sin repetir el literal.
CANAL_AVISO_SOLICITADO = "facilities.avisos.solicitado.v1"

#: Identificador estable de la conexion del receptor. Con el, conectar dos veces NO duplica el
#: receptor (ver «LA TRAMPA DEL RECEPTOR DUPLICADO» en el docstring del modulo).
DISPATCH_UID = "avisos.alta.disparo"

#: Señal interna que transporta `AvisoSolicitado` dentro del proceso.
#:
#: Argumentos que viajan en cada `send` (el resto se ignora y queda en `**extra`):
#:
#: - `sender`: el productor que publica (el servicio de alta); puede ser `None`.
#: - `notification_type` (str, obligatorio): tipo del catalogo cerrado `TIPOS_AVISO`.
#: - `incident_id` (int | None): incidencia a la que pertenece el aviso.
#: - `history_entry_id` (int | None): asiento de historico, solo para los avisos de cambio de estado.
#:
#: El mensaje del asyncapi menciona ademas el id de solicitud, el destinatario resuelto, el estado de
#: envio y la fecha de creacion: esos cuatro NO los produce el emisor, los produce el outbox al
#: insertar la fila (la solicitud no existe antes de ser encolada) y se leen del `ResultadoDisparo`.
aviso_solicitado = Signal()

TIPO_AVISO_FUERA_DE_CATALOGO = (
    "El tipo de aviso «{tipo}» del evento «{canal}» no pertenece al catalogo cerrado de tipos: {catalogo}."
)
TIPO_NO_DISPARA_ALTA = (
    "El evento de tipo «{tipo}» no corresponde al alta de una incidencia: no se encola ninguna solicitud "
    "«{tipo_alta}» (AC-AVI-02)."
)
ALTA_SIN_INCIDENCIA = (
    "No se puede encolar el aviso de alta «{tipo_alta}» sin incidencia: `incident_id` es obligatorio para este tipo "
    "(CHECK `ck_aviso_correo_vinculo`)."
)
ALTA_CON_HISTORIAL = (
    "No se puede encolar el aviso de alta «{tipo_alta}» de la incidencia «{incident_id}» con la entrada de historial "
    "«{history_entry_id}»: `ck_aviso_correo_vinculo` exige `history_entry_id` nulo para este tipo."
)

TRAZA_DISPARO = "Aviso de alta de incidencia procesado por el consumidor del evento"
TRAZA_IGNORADO = "Evento de aviso ignorado por el consumidor de alta: no es un alta de incidencia"


@dataclass(frozen=True, slots=True)
class ResultadoDisparo:
    """
    Desenlace de un consumo del evento `AvisoSolicitado`.

    Hay exactamente tres desenlaces posibles, y conviene no confundirlos:

    - `creado=True`, `ignorado=False`: el evento era un alta y la solicitud se ha INSERTADO ahora.
    - `creado=False`, `ignorado=False`: el evento era un alta y la solicitud YA ESTABA encolada. NO es
      un error: es la idempotencia de REQ-130 / AC-AVI-01 funcionando («existe exactamente una
      solicitud `NEW_INCIDENT_ALERT` por incidencia»; el reproceso, el doble submit o el reintento del
      alta se absorben contra `uk_aviso_correo_notif_key`). `encolado.aviso` es la solicitud previa.
    - `ignorado=True`: el evento NO era un alta y el outbox no se ha tocado siquiera. `motivo` explica
      por que, en espanol, y `encolado` y `notification_key` viajan a `None` porque no hay solicitud
      alguna asociada a este consumo.

    `notification_key` se duplica fuera de `encolado` por comodidad del llamante y de la traza: es el
    unico dato del resultado que se puede leer sin tocar el ORM.
    """

    encolado: ResultadoEncolado | None = None
    creado: bool = False
    ignorado: bool = False
    notification_key: str | None = None
    motivo: str | None = None


class ConsumidorAltaIncidencia:
    """
    Consumidor de `AvisoSolicitado` que encola el aviso de alta de incidencia.

    La dependencia del outbox se resuelve de forma PEREZOSA, igual que `ServicioOutboxAvisos` hace
    con su repositorio: nada se construye a nivel de modulo, porque el servicio acaba tocando modelos
    del ORM y `settings`, que no estan disponibles antes de `django.setup()`. Y se puede INYECTAR, que
    es como las pruebas sustituyen el encolado por un doble sin parchear el modulo de producto.

    El consumo es un metodo de instancia (`consumir`), y la funcion de modulo
    `consumir_aviso_solicitado` -que es la que se conecta a la señal- delega en una instancia nueva.
    Asi el receptor registrado no arrastra estado entre eventos y, aun asi, hay un objeto construible
    con un doble dentro para las pruebas.
    """

    def __init__(self, outbox_servicio: ServicioOutboxAvisos | None = None) -> None:
        self._outbox: ServicioOutboxAvisos | None = outbox_servicio

    def _resolver_outbox(self) -> ServicioOutboxAvisos:
        """Devuelve el servicio inyectado o construye el real en el primer uso, nunca a nivel de modulo."""

        if self._outbox is None:
            self._outbox = ServicioOutboxAvisos()
        return self._outbox

    def consumir(
        self,
        sender: Any = None,
        *,
        notification_type: str,
        incident_id: int | None = None,
        history_entry_id: int | None = None,
        **extra: Any,
    ) -> ResultadoDisparo:
        """
        Atiende un evento `AvisoSolicitado` y encola, como mucho, UNA solicitud de alta.

        Orden de decisiones:

        1. `notification_type` fuera de `TIPOS_AVISO` -> `ErrorMotorAvisos`. El catalogo es CERRADO
           (`ck_aviso_correo_tipo`): un tipo inventado es un defecto del productor y debe saltar en
           Python y en espanol, no como un ORA-02290 dentro de la transaccion del alta.
        2. `notification_type` distinto de `TIPO_ALTA` -> `ResultadoDisparo(ignorado=True)` SIN tocar
           el outbox. **Esta es la rama de AC-AVI-02**: los cambios de estado posteriores de la
           incidencia (EN_CURSO, RESUELTA, CERRADA) publican `STATUS_CHANGE_ALERT` por este mismo
           canal, y ninguno de ellos puede producir una SEGUNDA solicitud de alta. Quien encola el
           aviso de esas transiciones es `encolar_aviso_cambio_estado`, con su propia clave por
           asiento de historico; este consumidor no se mete ahi.
        3. Alta sin `incident_id` -> `ErrorMotorAvisos`: un aviso de alta sin incidencia no es
           encolable, lo prohibe `ck_aviso_correo_vinculo`.
        4. Alta con `history_entry_id` informado -> `ErrorMotorAvisos`: la misma CHECK exige que para
           `NEW_INCIDENT_ALERT` el asiento de historico sea nulo. Se rechaza en vez de ignorarlo en
           silencio porque significa que el productor confundio el alta con un cambio de estado.
        5. Camino nominal: se delega en `encolar_aviso_alta(incident_id=...)`, que construye la clave
           `clave_aviso_alta(incident_id)` y absorbe la colision de unicidad sobre su savepoint.

        No se abre ni se cierra ninguna transaccion: se corre DENTRO de la del alta (ver el docstring
        del modulo). Tampoco se resuelve destinatario ni se compone contenido: es de AVI-02.

        Args:
            sender: el productor que publico el evento; se acepta por el protocolo de `Signal` y no
                interviene en ninguna decision.
            notification_type: tipo del catalogo cerrado que viaja en el evento.
            incident_id: incidencia del aviso; obligatoria para el alta.
            history_entry_id: asiento de historico; debe ser nulo para el alta.
            **extra: resto de argumentos de la señal (`signal`, campos futuros del mensaje). Se
                ignoran a proposito: añadir un dato al evento no puede romper a este consumidor.

        Returns:
            ResultadoDisparo: el desenlace, con `creado`, `ignorado` y la clave de idempotencia.

        Raises:
            ErrorMotorAvisos: tipo fuera de catalogo, alta sin incidencia o alta con asiento de historico.
        """

        if notification_type not in TIPOS_AVISO:
            catalogo = ", ".join(sorted(TIPOS_AVISO))
            raise ErrorMotorAvisos(
                TIPO_AVISO_FUERA_DE_CATALOGO.format(tipo=notification_type, canal=CANAL_AVISO_SOLICITADO, catalogo=catalogo)
            )

        if notification_type != TIPO_ALTA:
            motivo = TIPO_NO_DISPARA_ALTA.format(tipo=notification_type, tipo_alta=TIPO_ALTA)
            logger.debug(
                TRAZA_IGNORADO,
                extra={"data": {"canal": CANAL_AVISO_SOLICITADO, "notification_type": notification_type, "incident_id": incident_id}},
            )
            return ResultadoDisparo(ignorado=True, motivo=motivo)

        if incident_id is None:
            raise ErrorMotorAvisos(ALTA_SIN_INCIDENCIA.format(tipo_alta=TIPO_ALTA))

        if history_entry_id is not None:
            raise ErrorMotorAvisos(
                ALTA_CON_HISTORIAL.format(tipo_alta=TIPO_ALTA, incident_id=incident_id, history_entry_id=history_entry_id)
            )

        encolado = self._resolver_outbox().encolar_aviso_alta(incident_id=incident_id)

        # La traza es TRAZA: deja constancia de que el disparo ocurrio. El aviso en si es la fila de
        # `aviso_correo` que acaba de insertar el outbox, nunca esta linea de log.
        logger.info(
            TRAZA_DISPARO,
            extra={
                "data": {
                    "canal": CANAL_AVISO_SOLICITADO,
                    "notification_type": TIPO_ALTA,
                    "incident_id": incident_id,
                    "notification_key": encolado.notification_key,
                    "creado": encolado.creado,
                }
            },
        )
        return ResultadoDisparo(
            encolado=encolado,
            creado=encolado.creado,
            ignorado=False,
            notification_key=encolado.notification_key,
        )


def consumir_aviso_solicitado(
    sender: Any = None,
    *,
    notification_type: str,
    incident_id: int | None = None,
    history_entry_id: int | None = None,
    **extra: Any,
) -> ResultadoDisparo:
    """
    Receptor de `aviso_solicitado`: delega en una `ConsumidorAltaIncidencia` nueva.

    Es la funcion que se conecta a la señal (ver `conectar_disparo_alta`). Se construye una instancia
    por evento para que el receptor registrado no guarde estado entre altas; quien necesite inyectar
    un doble del outbox instancia `ConsumidorAltaIncidencia` directamente y llama a `consumir`, sin
    parchear nada.

    Las excepciones suben al emisor a proposito: la señal se publica con `send` y el productor tiene
    que enterarse de que su aviso no se encolo para revertir el alta.
    """

    return ConsumidorAltaIncidencia().consumir(
        sender,
        notification_type=notification_type,
        incident_id=incident_id,
        history_entry_id=history_entry_id,
        **extra,
    )


def publicar_alta_incidencia(*, incident_id: int, remitente: Any = None) -> list[tuple[Any, Any]]:
    """
    Publica `AvisoSolicitado` para el alta de una incidencia (la cara de PUBLICACION, EVT-001).

    La invoca el productor del alta (ARC-013) DENTRO de su propia transaccion, justo despues de
    escribir la incidencia y antes de confirmar: la entrega de la señal es sincrona y en el mismo
    hilo, asi que el INSERT del aviso cae en el mismo commit y un alta revertida no deja ninguna
    solicitud (REQ-132 / ADR-006).

    Se usa `Signal.send` y NO `send_robust`. `send_robust` atrapa las excepciones de los receptores y
    las devuelve como si fueran respuestas: el alta confirmaria creyendo que el aviso quedo encolado,
    el equipo de mantenimiento nunca recibiria el correo y el fallo no apareceria en ninguna parte
    salvo si alguien inspeccionase la lista de respuestas. Con `send`, el fallo del consumidor
    propaga hasta el alta y arrastra su transaccion: o incidencia y aviso, o ninguno de los dos.

    `history_entry_id` viaja SIEMPRE a `None`: `ck_aviso_correo_vinculo` lo exige para el tipo
    `NEW_INCIDENT_ALERT`.

    Args:
        incident_id: incidencia recien dada de alta.
        remitente: `sender` de la señal; por defecto `None`, que entrega a los receptores conectados
            sin filtro de emisor (que es como los conecta `conectar_disparo_alta`).

    Returns:
        list[tuple[Any, Any]]: los pares `(receptor, respuesta)` que devuelve `Signal.send`. Para el
        receptor de este modulo, la respuesta es el `ResultadoDisparo` del consumo.
    """

    return aviso_solicitado.send(
        sender=remitente,
        notification_type=TIPO_ALTA,
        incident_id=incident_id,
        history_entry_id=None,
    )


def conectar_disparo_alta(dispatch_uid: str = DISPATCH_UID) -> None:
    """
    Conecta `consumir_aviso_solicitado` a la señal `aviso_solicitado`.

    Lo llama el composition root de la app (`AvisosConfig.ready()`), que es el primer momento en que
    el registro de apps esta completo.

    `dispatch_uid` NO es decorativo: `ready()` puede ejecutarse varias veces en un mismo proceso
    (recarga del runserver, `django.setup()` repetido). Sin el, cada pasada añadiria otro receptor y
    un unico alta ejecutaria el consumidor dos veces, duplicando el trabajo de encolado. Con el, la
    segunda conexion es una operacion vacia. Se expone como parametro unicamente para que las pruebas
    puedan registrar una conexion aislada bajo otro identificador.
    """

    aviso_solicitado.connect(consumir_aviso_solicitado, dispatch_uid=dispatch_uid)


def desconectar_disparo_alta(dispatch_uid: str = DISPATCH_UID) -> None:
    """
    Desconecta el receptor del disparo de alta, identificandolo por su `dispatch_uid`.

    Es la operacion inversa de `conectar_disparo_alta` y existe sobre todo para que una prueba pueda
    dejar el bus como lo encontro: un receptor que sobrevive a su test contamina a los siguientes.
    Desconectar lo que no estaba conectado no es un error.
    """

    aviso_solicitado.disconnect(dispatch_uid=dispatch_uid)


__all__ = [
    "ALTA_CON_HISTORIAL",
    "ALTA_SIN_INCIDENCIA",
    "CANAL_AVISO_SOLICITADO",
    "DISPATCH_UID",
    "TIPO_AVISO_FUERA_DE_CATALOGO",
    "TIPO_NO_DISPARA_ALTA",
    "TRAZA_DISPARO",
    "TRAZA_IGNORADO",
    "ConsumidorAltaIncidencia",
    "ResultadoDisparo",
    "aviso_solicitado",
    "conectar_disparo_alta",
    "consumir_aviso_solicitado",
    "desconectar_disparo_alta",
    "publicar_alta_incidencia",
]
