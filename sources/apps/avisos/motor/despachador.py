"""
Despachador de la cola de avisos por correo: el CONSUMIDOR del patron outbox (ARC-014, REQ-132, REQ-134, REQ-142).

Que hace este modulo
--------------------
Toma solicitudes de `aviso_correo` en orden FIFO, las entrega a traves del puerto de transporte y
cierra su ciclo de vida (ENVIADO, PENDIENTE con reintento, FALLIDO, DESCARTADO o SUPRIMIDO). No
redacta el correo (eso es el puerto `CompositorAviso`), no habla SMTP (eso es `TransporteCorreo`),
no construye consultas (eso es `RepositorioAvisoCorreo`) y no calcula el backoff (eso es
`apps.avisos.motor.reintentos`). Aqui vive UNICAMENTE la ORQUESTACION del ciclo.

La transaccion y el envio SMTP NO se solapan
--------------------------------------------
`RepositorioAvisoCorreo.tomar_pendientes` marca el lote como `ENVIANDO`, suelta el bloqueo y
CONFIRMA. La entrega ocurre despues, fuera de toda transaccion: mantener abierta una transaccion
Oracle mientras se espera a un servidor SMTP bloquearia filas durante segundos y, con varias
replicas, pararia la cola entera. El precio de esa decision es el compromiso at-least-once: si el
proceso muere entre la entrega y su confirmacion, la solicitud se queda en `ENVIANDO` y la rescata
la ventana de recuperacion (`recuperar_atascados`).

Idempotencia de la entrega (AC-AVI-06 / AC-SMTP-02)
---------------------------------------------------
Una solicitud ya entregada esta en `ENVIADO`, estado TERMINAL con `message_id` y `sent_at`
informados. Ni `recuperar_atascados` (que solo mira `ENVIANDO`) ni `tomar_pendientes` (que solo
mira `PENDIENTE`) la vuelven a tocar, y si alguien la pasa igualmente por `procesar_aviso`, la
guarda de idempotencia la devuelve como `"omitido"` sin invocar al transporte. Por debajo, la base
de datos defiende lo mismo con el trigger `trg_aviso_correo_estado_final` (ORA-20060).

Ninguna excepcion escapa del bucle del lote
-------------------------------------------
Una solicitud rota (contenido imposible, dato corrupto, fallo del repositorio) NO puede tumbar el
ciclo: eso pararia la cola para todos los demas avisos. Cada `procesar_aviso` va envuelto en su
propio `try/except`, se traza con `logger.exception` y el bucle continua.

Que se traza y que NO (REQ-063, REQ-076, REQ-079)
-------------------------------------------------
Al log solo van identificadores y contadores: `notification_id`, `notification_type`,
`attempt_count`, `result_code`, `smtp_response_code` y el desenlace. NUNCA el `recipient_email`,
el asunto, el cuerpo del mensaje ni credencial alguna: ningun correo corporativo aparece en las
trazas del motor.

DECISIONES DECLARADAS (no inventadas en silencio; ver tambien los docstrings de cada metodo)
--------------------------------------------------------------------------------------------
1. SUPRESION DESDE `ENVIANDO`. Las dos supresiones que detecta el despachador (sin destinatario,
   composicion incompleta) se descubren con la solicitud ya tomada, es decir en `ENVIANDO`. El grafo
   de `estados.py` declara esa arista (`ENVIANDO -> SUPRIMIDO`), de modo que el cierre marca
   `SUPRIMIDO` DIRECTAMENTE, sin transitos tecnicos por `PENDIENTE`.
2. REENVIO MANUAL Y PRESUPUESTO DE INTENTOS. `uk_aviso_intento_correlativo` exige un
   `attempt_number` nuevo por cada intento y `ck_aviso_correo_intentos` acota
   `attempt_count <= max_attempts`. Una solicitud `FALLIDO` llega con el presupuesto AGOTADO, de
   modo que, sin ampliarlo, el reenvio manual de AC-SMTP-09 no podria trazar su intento. Por eso
   `reenviar()` amplia el presupuesto en UNO: el tope de REQ-134 acota los reintentos AUTOMATICOS,
   y el intento que el ADMINISTRADOR ordena expresamente es el que lo justifica.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from django.db import transaction

from apps.avisos.motor.calendario import procesamiento_permitido, proxima_reanudacion
from apps.avisos.motor.configuracion import ConfiguracionMotorAvisos, configuracion_motor
from apps.avisos.motor.errores import ErrorMotorAvisos
from apps.avisos.motor.estados import MOTIVOS_SUPRESION, RESULTADOS_INTENTO, EstadoAviso, validar_transicion
from apps.avisos.motor.reintentos import decidir
from apps.avisos.motor.repositorio import LONGITUD_MAXIMA_MENSAJE_ERROR, RepositorioAvisoCorreo
from apps.avisos.motor.transporte import MensajeCorreo, ResultadoEntrega, TransporteCorreo, transporte_por_defecto
from apps.core.contexto import utc_now

if TYPE_CHECKING:  # pragma: no cover - solo anotaciones: los modelos no se importan antes de django.setup()
    from apps.core.models import AvisoCorreoEntity

logger = logging.getLogger(__name__)


def _resultado_del_catalogo(codigo: str) -> str:
    """
    Devuelve el resultado de intento comprobando que pertenece a `RESULTADOS_INTENTO`.

    La comprobacion se hace al importar el modulo para que un codigo inventado salte en el arranque
    y no al persistir el intento, ya dentro de la transaccion (CHECK `ck_aviso_intento_result`).
    """

    if codigo not in RESULTADOS_INTENTO:
        catalogo = ", ".join(sorted(RESULTADOS_INTENTO))
        raise ErrorMotorAvisos(f"El resultado de intento «{codigo}» no pertenece al catalogo cerrado: {catalogo}.")
    return codigo


def _motivo_del_catalogo(codigo: str) -> str:
    """Devuelve el motivo de supresion comprobando que pertenece a `MOTIVOS_SUPRESION` (CHECK `ck_aviso_correo_motivo_sup`)."""

    if codigo not in MOTIVOS_SUPRESION:
        catalogo = ", ".join(sorted(MOTIVOS_SUPRESION))
        raise ErrorMotorAvisos(f"El motivo de supresion «{codigo}» no pertenece al catalogo cerrado: {catalogo}.")
    return codigo


#: Resultados de intento que usa el despachador, tomados del catalogo cerrado del motor.
RESULTADO_ENVIADO: str = _resultado_del_catalogo("SENT")
RESULTADO_TRANSITORIO: str = _resultado_del_catalogo("TRANSIENT_ERROR")
RESULTADO_CONFIGURACION: str = _resultado_del_catalogo("CONFIG_ERROR")
RESULTADO_SIN_DESTINATARIOS: str = _resultado_del_catalogo("NO_RECIPIENTS")
RESULTADO_COMPOSICION: str = _resultado_del_catalogo("COMPOSE_ERROR")

#: Motivos de supresion que cierra el despachador, del catalogo cerrado del motor.
MOTIVO_SIN_DESTINATARIOS: str = _motivo_del_catalogo("NO_RECIPIENTS")
MOTIVO_COMPOSICION_INCOMPLETA: str = _motivo_del_catalogo("COMPOSICION_INCOMPLETA")

#: Numero de destinatarios de un aviso del motor: `aviso_correo` tiene UN `recipient_email`.
#: La CHECK `ck_aviso_intento_sin_dest` exige `recipient_count > 0` salvo para `NO_RECIPIENTS`.
DESTINATARIOS_POR_AVISO: int = 1
SIN_DESTINATARIOS: int = 0

# --- Etiquetas de desenlace de `procesar_aviso` --------------------------
DESENLACE_ENTREGADO = "entregado"
DESENLACE_REPROGRAMADO = "reprogramado"
DESENLACE_FALLIDO = "fallido"
DESENLACE_DESCARTADO = "descartado"
DESENLACE_SUPRIMIDO = "suprimido"
DESENLACE_OMITIDO = "omitido"

#: Campo de `ResumenCiclo` que incrementa cada desenlace. `DESENLACE_OMITIDO` no suma en ningun
#: contador: una solicitud ya entregada no es un desenlace de este ciclo, es la ausencia de el.
CAMPO_POR_DESENLACE: dict[str, str] = {
    DESENLACE_ENTREGADO: "entregados",
    DESENLACE_REPROGRAMADO: "reprogramados",
    DESENLACE_FALLIDO: "fallidos",
    DESENLACE_DESCARTADO: "descartados",
    DESENLACE_SUPRIMIDO: "suprimidos",
}

#: Estados destino que el despachador sabe cerrar tras un intento fallido, con su desenlace.
DESENLACE_POR_ESTADO: dict[EstadoAviso, str] = {
    EstadoAviso.PENDIENTE: DESENLACE_REPROGRAMADO,
    EstadoAviso.FALLIDO: DESENLACE_FALLIDO,
    EstadoAviso.DESCARTADO: DESENLACE_DESCARTADO,
}

# --- Literales en espanol (REQ-050) --------------------------------------
SIN_TRANSPORTE_ACTIVO = (
    "No hay configuracion SMTP activa (configuracion_smtp.is_active='Y'): el motor no puede entregar el aviso. "
    "El intento se cierra como CONFIG_ERROR y la solicitud no queda bloqueada en ENVIANDO."
)
SIN_DESTINATARIO_RESOLUBLE = "La solicitud no tiene destinatario de correo informado: no hay nada que entregar."
COMPOSICION_INCOMPLETA = "La solicitud no tiene asunto o cuerpo de texto: el aviso no se puede componer y no se entrega."
AVISO_NO_ENCONTRADO = "No existe ninguna solicitud de aviso con identificador «{notification_id}»."
ESTADO_DESTINO_NO_SOPORTADO = "La politica de reintentos devolvio el estado destino «{estado}», que el despachador no sabe cerrar."

# --- Mensajes de traza ---------------------------------------------------
TRAZA_FUERA_DE_HORARIO = "Motor de avisos fuera de la ventana de servicio: no se consume la cola, las pendientes se conservan en FIFO"
TRAZA_SIN_TRANSPORTE = "Motor de avisos sin configuracion SMTP activa: el lote se cierra con intentos CONFIG_ERROR"
TRAZA_LOTE = "Ciclo del despachador de avisos completado"
TRAZA_DESENLACE = "Solicitud de aviso procesada"
TRAZA_FALLO_INESPERADO = "Fallo inesperado al procesar una solicitud de aviso: se continua con el resto del lote"
TRAZA_RECUPERACION = "Solicitudes de aviso atascadas devueltas a PENDIENTE"
TRAZA_REENVIO = "Reenvio manual de una solicitud de aviso ordenado por el ADMINISTRADOR"


@runtime_checkable
class CompositorAviso(Protocol):
    """
    Puerto de COMPOSICION del aviso (AVI-02): redacta asunto y cuerpo y resuelve el destinatario.

    La implementacion es propiedad de otra tarea. El despachador solo sabe pedirle que componga la
    solicitud y comprobar despues si el contenido ha quedado informado.
    """

    def componer(self, aviso: "AvisoCorreoEntity") -> bool:
        """Informa `subject`, `body_text` y `recipient_email` en el aviso; devuelve `True` si quedo compuesto."""

        ...


@dataclass(frozen=True, slots=True)
class ResumenCiclo:
    """
    Recuento de lo ocurrido en un ciclo del despachador, para la traza de operacion.

    Es inmutable a proposito: es una fotografia del ciclo, no un acumulador que nadie deba
    reescribir despues. `recuperados` lo informa `recuperar_atascados` cuando quien orquesta el
    ciclo completo (el planificador de ARC-014) compone ambas fases.
    """

    tomados: int = 0
    entregados: int = 0
    reprogramados: int = 0
    fallidos: int = 0
    descartados: int = 0
    suprimidos: int = 0
    recuperados: int = 0
    omitido_por_horario: bool = False


class MotorAvisos:
    """
    Orquestador del ciclo de entrega de la cola de avisos (ARC-014).

    Todas sus dependencias son PUERTOS inyectables (repositorio, transporte, compositor y
    configuracion) y se resuelven de forma PEREZOSA en el primer uso, nunca a nivel de modulo: este
    modulo lo importa tanto el proceso servidor como el planificador del despachador, y resolver el
    repositorio al importar exigiria `django.setup()` hecho de antemano.
    """

    def __init__(
        self,
        *,
        repositorio: RepositorioAvisoCorreo | None = None,
        transporte: TransporteCorreo | None = None,
        compositor: CompositorAviso | None = None,
        config: ConfiguracionMotorAvisos | None = None,
    ) -> None:
        self._repositorio = repositorio
        self._transporte = transporte
        self._compositor = compositor
        self._config = config

    # --- Resolucion perezosa de los puertos --------------------------------

    @property
    def config(self) -> ConfiguracionMotorAvisos:
        """Parametros de operacion del motor; se leen una sola vez y se conservan durante la vida del motor."""

        if self._config is None:
            self._config = configuracion_motor()
        return self._config

    @property
    def repositorio(self) -> RepositorioAvisoCorreo:
        """Acceso a datos de la cola; se construye en el primer uso (los modelos exigen `django.setup()`)."""

        if self._repositorio is None:
            self._repositorio = RepositorioAvisoCorreo()
        return self._repositorio

    @property
    def compositor(self) -> CompositorAviso | None:
        """Puerto de composicion, o `None` si no se ha inyectado ninguno (AVI-02 es de otra tarea)."""

        return self._compositor

    def _resolver_transporte(self) -> TransporteCorreo | None:
        """
        Devuelve el transporte inyectado o fabrica el de produccion con la configuracion SMTP activa.

        NO se memoriza a proposito: la fila activa de `configuracion_smtp` la puede cambiar el
        ADMINISTRADOR en caliente y cada ciclo debe trabajar con la vigente. Devuelve `None` cuando
        no hay configuracion activa; quien llama lo traduce a un intento `CONFIG_ERROR`.
        """

        if self._transporte is not None:
            return self._transporte
        return transporte_por_defecto(self.config)

    # --- Fases del ciclo ---------------------------------------------------

    def recuperar_atascados(self) -> int:
        """
        Devuelve a `PENDIENTE` las solicitudes abandonadas en `ENVIANDO` (REQ-132 regla 5, AC-AVI-06).

        Una solicitud queda atascada cuando el trabajador que la tomo murio entre el `ENVIANDO` y el
        cierre de su transicion. Pasada `config.ventana_recuperacion_minutos` vuelve a la cola sin
        perder su sitio en el FIFO (`next_attempt_at` a nulo, `created_at` intacto).

        Las solicitudes YA ENTREGADAS no entran NUNCA en este filtro: una entrega confirmada deja la
        solicitud en `ENVIADO`, estado terminal con `message_id` y `sent_at` informados
        (`ck_aviso_correo_enviado`), y el filtro de recuperacion solo mira `ENVIANDO`. Ahi esta la
        garantia de que la recuperacion no genera un segundo correo de un aviso ya entregado.

        Returns:
            int: numero de solicitudes devueltas a `PENDIENTE`.
        """

        recuperados = len(self.repositorio.recuperar_atascados(ventana_minutos=self.config.ventana_recuperacion_minutos))
        if recuperados:
            logger.info(
                TRAZA_RECUPERACION,
                extra={"data": {"recuperados": recuperados, "ventana_minutos": self.config.ventana_recuperacion_minutos}},
            )
        return recuperados

    def procesar_lote(self) -> ResumenCiclo:
        """
        Consume un lote de la cola en orden FIFO y cierra el ciclo de vida de cada solicitud.

        Fuera de la ventana de servicio NO se toca la cola (REQ-132 regla 6): las solicitudes
        pendientes se conservan sin perdida y se procesan al reanudar respetando su orden de
        llegada, porque `tomar_pendientes` ordena siempre por `created_at`.

        El transporte se resuelve UNA vez por lote. Si no hay configuracion SMTP activa no se lanza
        ninguna excepcion ni se deja el lote bloqueado en `ENVIANDO`: cada solicitud se cierra con
        un intento `CONFIG_ERROR` y la decision que corresponda de la politica de reintentos.

        Ninguna excepcion de una solicitud concreta interrumpe el recorrido: si una solicitud rota
        tumbara el bucle, la cola entera quedaria parada.

        Returns:
            ResumenCiclo: recuento del ciclo; `omitido_por_horario=True` si no hubo consumo.
        """

        config = self.config
        if not procesamiento_permitido(config):
            logger.info(
                TRAZA_FUERA_DE_HORARIO,
                extra={"data": {"proxima_reanudacion": proxima_reanudacion(utc_now(), config).isoformat()}},
            )
            return ResumenCiclo(omitido_por_horario=True)

        # EL ORDEN IMPORTA: el transporte se resuelve ANTES de tomar el lote. `_resolver_transporte`
        # lee la fila activa de `configuracion_smtp` en BASE DE DATOS y, si esa lectura revienta
        # (base inalcanzable), la excepcion escapa de este metodo. Resolviendolo primero, ese fallo
        # ocurre cuando todavia no se ha tomado nada y ninguna solicitud queda colgada en ENVIANDO
        # esperando a la ventana de recuperacion. Resolver `None` (no hay configuracion SMTP activa)
        # NO es un fallo: el lote si se toma y cada solicitud se cierra con un intento CONFIG_ERROR.
        transporte = self._resolver_transporte()

        # La transaccion de la toma se abre y se CIERRA dentro de `tomar_pendientes`: a partir de
        # aqui no hay ningun bloqueo tomado y el envio SMTP puede durar lo que tarde.
        tomados = self.repositorio.tomar_pendientes(limite=config.tamano_lote, locked_by=config.identificador_worker)
        if not tomados:
            return ResumenCiclo()

        if transporte is None:
            logger.warning(TRAZA_SIN_TRANSPORTE, extra={"data": {"tomados": len(tomados)}})

        contadores: dict[str, int] = dict.fromkeys(CAMPO_POR_DESENLACE.values(), 0)
        for aviso in tomados:
            try:
                desenlace = self.procesar_aviso(aviso, transporte=transporte)
            except Exception:  # noqa: BLE001 - el ciclo NO se detiene por una solicitud concreta
                # La solicitud se queda en ENVIANDO y la rescata la ventana de recuperacion
                # (`recuperar_atascados`); el resto del lote sigue procesandose.
                logger.exception(
                    TRAZA_FALLO_INESPERADO,
                    extra={"data": {"notification_id": str(aviso.pk), "notification_type": aviso.notification_type}},
                )
                continue
            campo = CAMPO_POR_DESENLACE.get(desenlace)
            if campo is not None:
                contadores[campo] += 1

        resumen = ResumenCiclo(tomados=len(tomados), **contadores)
        logger.info(TRAZA_LOTE, extra={"data": {"tomados": resumen.tomados, **contadores}})
        return resumen

    def procesar_aviso(self, aviso: "AvisoCorreoEntity", *, transporte: TransporteCorreo | None) -> str:
        """
        Entrega UNA solicitud ya tomada y cierra su transicion (REQ-132, REQ-134, REQ-141, REQ-142).

        El envio ocurre FUERA de toda transaccion; solo la escritura del intento y el cierre del
        estado van juntos en un `transaction.atomic()`, de modo que nunca queda un intento trazado
        sin su transicion ni al reves.

        Args:
            aviso: solicitud tomada por `tomar_pendientes` (en `ENVIANDO`).
            transporte: puerto de entrega, o `None` si no hay configuracion SMTP activa.

        Returns:
            str: desenlace del ciclo de la solicitud (`entregado`, `reprogramado`, `fallido`,
            `descartado`, `suprimido` u `omitido`).
        """

        config = self.config
        repositorio = self.repositorio

        # 1. Guarda de idempotencia (AC-AVI-06 / AC-SMTP-02): un aviso ya entregado esta en ENVIADO,
        #    estado terminal. No se reenvia, no se vuelve a trazar y NO se invoca al transporte.
        if aviso.status == EstadoAviso.ENVIADO.value:
            self._trazar(aviso, outcome=DESENLACE_OMITIDO, result_code=RESULTADO_ENVIADO)
            return DESENLACE_OMITIDO

        # 2. Sin destinatario no hay entrega posible (REQ-140). La CHECK `ck_aviso_intento_sin_dest`
        #    obliga a que un intento con `recipient_count = 0` lleve exactamente `NO_RECIPIENTS`.
        if not (aviso.recipient_email or "").strip():
            return self._suprimir(
                aviso,
                result_code=RESULTADO_SIN_DESTINATARIOS,
                recipient_count=SIN_DESTINATARIOS,
                motivo_supresion=MOTIVO_SIN_DESTINATARIOS,
                detalle=SIN_DESTINATARIO_RESOLUBLE,
            )

        # 3. Composicion (AVI-02). REQ-142 regla 5: un reintento REUTILIZA el contenido ya compuesto
        #    y no lo recompone, asi que solo se compone antes del primer intento.
        if not self._contenido_completo(aviso):
            if self.compositor is not None and aviso.attempt_count == 0:
                self.compositor.componer(aviso)
            if not self._contenido_completo(aviso):
                return self._suprimir(
                    aviso,
                    result_code=RESULTADO_COMPOSICION,
                    recipient_count=DESTINATARIOS_POR_AVISO,
                    motivo_supresion=MOTIVO_COMPOSICION_INCOMPLETA,
                    detalle=COMPOSICION_INCOMPLETA,
                )

        # 4. El contador sube ANTES del envio: `registrar_intento` numera el intento con el
        #    `attempt_count` resultante (`uk_aviso_intento_correlativo`).
        attempt_count = repositorio.incrementar_intento(aviso)

        # 5. Entrega, SIN transaccion abierta: el bloqueo se solto con el commit de `tomar_pendientes`.
        if transporte is None:
            resultado = ResultadoEntrega.de_configuracion(SIN_TRANSPORTE_ACTIVO)
        else:
            mensaje = MensajeCorreo(
                destinatarios=(aviso.recipient_email,),
                asunto=aviso.subject,
                cuerpo_texto=aviso.body_text,
                cuerpo_html=aviso.body_html,
            )
            resultado = transporte.enviar(mensaje)

        if resultado.entregado:
            return self._cerrar_entrega(aviso, resultado=resultado, attempt_count=attempt_count)
        return self._cerrar_fallo(aviso, resultado=resultado, attempt_count=attempt_count, config=config)

    def reenviar(self, notification_id: str, *, actor_user_id: int) -> str:
        """
        Reenvia manualmente una solicitud fallida o descartada (REQ-130 AC-SMTP-09, EP-049).

        Reenviar NO crea una segunda solicitud: la MISMA fila vuelve a `ENVIANDO` y, si el servidor
        la acepta, pasa a `ENVIADO`, con el nuevo intento trazado en `aviso_correo_intento` y el
        actor y la fecha del reenvio (`resent_by_user_id`, `resent_at`) en la solicitud.

        Solo se admite sobre `FALLIDO` o `DESCARTADO`, que es el grafo `TRANSICIONES_REENVIO_MANUAL`
        de `estados.py`. Sobre una solicitud en `ENVIADO`, `validar_transicion(..., manual=True)`
        lanza `AvisoYaEntregadoError` y la excepcion SE DEJA PROPAGAR: es la respuesta correcta a
        pedir el reenvio de un aviso que ya consta entregado (REQ-142 regla 2).

        El presupuesto de intentos se amplia en uno cuando ya estaba agotado (decision declarada 2
        en la cabecera del modulo): `ck_aviso_correo_intentos` acota `attempt_count <= max_attempts`
        y `uk_aviso_intento_correlativo` exige un numero de intento nuevo, de modo que sin ampliarlo
        el intento ordenado por el ADMINISTRADOR no cabria. El tope de REQ-134 acota los reintentos
        AUTOMATICOS, no los que el ADMINISTRADOR ordena expresamente.

        Este metodo NO es un endpoint: lo invoca la tarea propietaria de EP-049. Aqui solo vive la operacion.

        Args:
            notification_id: identificador de la solicitud a reenviar.
            actor_user_id: ADMINISTRADOR que ordena el reenvio (sale del contexto de sesion, nunca
                del payload de la peticion).

        Returns:
            str: desenlace del reenvio, con las mismas etiquetas que `procesar_aviso`.

        Raises:
            ErrorMotorAvisos: si la solicitud no existe.
            AvisoYaEntregadoError: si la solicitud ya consta `ENVIADO`.
            TransicionAvisoNoPermitidaError: si su estado no admite el reenvio manual.
        """

        config = self.config
        aviso = self.repositorio.obtener_o_error(notification_id, AVISO_NO_ENCONTRADO.format(notification_id=notification_id))
        validar_transicion(aviso.status, EstadoAviso.ENVIANDO, manual=True, notification_id=str(aviso.pk))

        momento = utc_now()
        with transaction.atomic():
            aviso.status = EstadoAviso.ENVIANDO.value
            aviso.locked_by = config.identificador_worker
            aviso.locked_at = momento
            aviso.resent_by_user_id = actor_user_id
            aviso.resent_at = momento
            if aviso.attempt_count >= aviso.max_attempts:
                aviso.max_attempts = aviso.attempt_count + 1
            # `update_fields` acota el UPDATE a lo tocado: ni `body_text` ni `body_html` (CLOB)
            # se reescriben, y el contenido compuesto se reutiliza tal cual (REQ-142 regla 5).
            aviso.save(update_fields=["status", "locked_by", "locked_at", "resent_by_user", "resent_at", "max_attempts"])

        logger.info(
            TRAZA_REENVIO,
            extra={
                "data": {
                    "notification_id": str(aviso.pk),
                    "notification_type": aviso.notification_type,
                    "actor_user_id": actor_user_id,
                }
            },
        )
        return self.procesar_aviso(aviso, transporte=self._resolver_transporte())

    # --- Cierres de la transicion -----------------------------------------

    def _cerrar_entrega(self, aviso: "AvisoCorreoEntity", *, resultado: ResultadoEntrega, attempt_count: int) -> str:
        """
        Persiste la entrega aceptada: intento `SENT` y transicion a `ENVIADO` en la misma transaccion.

        El `message_id` es obligatorio en ambos sitios (`ck_aviso_intento_message_id` y
        `ck_aviso_correo_enviado`). Si un transporte devolviese `entregado=True` sin el,
        `marcar_enviado` lanza `ErrorMotorAvisos` y la transaccion revierte tambien el intento: no
        queda ninguna traza incoherente.
        """

        with transaction.atomic():
            self.repositorio.registrar_intento(
                aviso,
                result_code=RESULTADO_ENVIADO,
                smtp_response_code=resultado.smtp_response_code,
                recipient_count=DESTINATARIOS_POR_AVISO,
                message_id=resultado.message_id,
            )
            self.repositorio.marcar_enviado(aviso, message_id=resultado.message_id, sent_at=utc_now())

        self._trazar(
            aviso,
            outcome=DESENLACE_ENTREGADO,
            result_code=RESULTADO_ENVIADO,
            attempt_count=attempt_count,
            smtp_response_code=resultado.smtp_response_code,
        )
        return DESENLACE_ENTREGADO

    def _cerrar_fallo(
        self,
        aviso: "AvisoCorreoEntity",
        *,
        resultado: ResultadoEntrega,
        attempt_count: int,
        config: ConfiguracionMotorAvisos,
    ) -> str:
        """
        Aplica la politica de reintentos al intento fallido y persiste su desenlace (REQ-134, REQ-141).

        La decision (reintentar y cuando, o cerrar como `FALLIDO` o `DESCARTADO`) NO se toma aqui:
        se delega integra en `apps.avisos.motor.reintentos.decidir`, que es quien conoce el backoff
        y la clasificacion del fallo. Este metodo solo escribe lo decidido, en una sola transaccion.
        """

        result_code = self._resultado_de_intento(resultado.error_code)
        decision = decidir(
            notification_id=str(aviso.pk),
            estado_actual=aviso.status,
            attempt_count=attempt_count,
            max_attempts=aviso.max_attempts or config.max_attempts_por_defecto,
            smtp_response_code=resultado.smtp_response_code,
            error_code=resultado.error_code,
            config=config,
        )
        # El motivo de la decision es el rastro de ultimo recurso cuando el transporte no trajo
        # texto propio; se recorta al ancho de `aviso_correo.last_error_message` (500 caracteres).
        mensaje_error = resultado.error_message or decision.motivo[:LONGITUD_MAXIMA_MENSAJE_ERROR]
        desenlace = DESENLACE_POR_ESTADO.get(decision.estado_destino)
        if desenlace is None:
            raise ErrorMotorAvisos(ESTADO_DESTINO_NO_SOPORTADO.format(estado=decision.estado_destino.value))

        with transaction.atomic():
            self.repositorio.registrar_intento(
                aviso,
                result_code=result_code,
                smtp_response_code=resultado.smtp_response_code,
                error_code=resultado.error_code,
                error_message=mensaje_error,
                recipient_count=DESTINATARIOS_POR_AVISO,
            )
            if decision.estado_destino is EstadoAviso.PENDIENTE:
                self.repositorio.devolver_a_pendiente(
                    aviso,
                    next_attempt_at=decision.next_attempt_at,
                    last_error_code=result_code,
                    last_error_message=mensaje_error,
                )
            elif decision.estado_destino is EstadoAviso.FALLIDO:
                self.repositorio.marcar_fallido(aviso, last_error_code=result_code, last_error_message=mensaje_error)
            else:
                self.repositorio.marcar_descartado(aviso, last_error_code=result_code, last_error_message=mensaje_error)

        self._trazar(
            aviso,
            outcome=desenlace,
            result_code=result_code,
            attempt_count=attempt_count,
            smtp_response_code=resultado.smtp_response_code,
        )
        return desenlace

    def _suprimir(
        self,
        aviso: "AvisoCorreoEntity",
        *,
        result_code: str,
        recipient_count: int,
        motivo_supresion: str,
        detalle: str,
    ) -> str:
        """
        Cierra una solicitud no entregable: traza el intento y la deja `SUPRIMIDO` (REQ-140, REQ-141).

        Estas dos situaciones (sin destinatario, composicion incompleta) se descubren con la solicitud
        ya tomada, es decir en `ENVIANDO`, y el grafo de `estados.py` declara esa arista: la supresion
        se marca DIRECTAMENTE, sin transitos tecnicos por ningun estado intermedio (decision 1).

        El `attempt_count` NO se incrementa: no ha habido intento de entrega contra el servidor, solo
        la constatacion de que la solicitud no es entregable.
        """

        with transaction.atomic():
            self.repositorio.registrar_intento(
                aviso,
                result_code=result_code,
                error_code=result_code,
                error_message=detalle,
                recipient_count=recipient_count,
            )
            self.repositorio.marcar_suprimido(aviso, suppression_reason_code=motivo_supresion)

        self._trazar(aviso, outcome=DESENLACE_SUPRIMIDO, result_code=result_code)
        return DESENLACE_SUPRIMIDO

    # --- Utilidades internas ----------------------------------------------

    @staticmethod
    def _contenido_completo(aviso: "AvisoCorreoEntity") -> bool:
        """Indica si la solicitud trae ya asunto y cuerpo de texto, que es lo minimo entregable."""

        return bool((aviso.subject or "").strip()) and bool((aviso.body_text or "").strip())

    @staticmethod
    def _resultado_de_intento(error_code: str | None) -> str:
        """
        Traduce el codigo de error del transporte a un resultado del catalogo cerrado.

        Un codigo ausente o ajeno al catalogo se trata como `TRANSIENT_ERROR`: es el lado seguro
        (el aviso todavia no ha llegado a nadie) y evita que un transporte mal implementado tumbe el
        INSERT del intento con la CHECK `ck_aviso_intento_result`.
        """

        codigo = (error_code or "").strip().upper()
        return codigo if codigo in RESULTADOS_INTENTO else RESULTADO_TRANSITORIO

    @staticmethod
    def _trazar(
        aviso: "AvisoCorreoEntity",
        *,
        outcome: str,
        result_code: str,
        attempt_count: int | None = None,
        smtp_response_code: str | None = None,
    ) -> None:
        """
        Traza el desenlace de una solicitud con identificadores y contadores UNICAMENTE.

        Nunca se escribe el `recipient_email`, el asunto, el cuerpo ni credencial alguna: ningun
        correo corporativo puede aparecer en los logs (REQ-063, REQ-076, REQ-079).
        """

        logger.info(
            TRAZA_DESENLACE,
            extra={
                "data": {
                    "notification_id": str(aviso.pk),
                    "notification_type": aviso.notification_type,
                    "attempt_count": attempt_count if attempt_count is not None else aviso.attempt_count,
                    "result_code": result_code,
                    "smtp_response_code": smtp_response_code,
                    "outcome": outcome,
                }
            },
        )


__all__ = [
    "AVISO_NO_ENCONTRADO",
    "CAMPO_POR_DESENLACE",
    "COMPOSICION_INCOMPLETA",
    "DESENLACE_DESCARTADO",
    "DESENLACE_ENTREGADO",
    "DESENLACE_FALLIDO",
    "DESENLACE_OMITIDO",
    "DESENLACE_POR_ESTADO",
    "DESENLACE_REPROGRAMADO",
    "DESENLACE_SUPRIMIDO",
    "DESTINATARIOS_POR_AVISO",
    "ESTADO_DESTINO_NO_SOPORTADO",
    "MOTIVO_COMPOSICION_INCOMPLETA",
    "MOTIVO_SIN_DESTINATARIOS",
    "RESULTADO_COMPOSICION",
    "RESULTADO_CONFIGURACION",
    "RESULTADO_ENVIADO",
    "RESULTADO_SIN_DESTINATARIOS",
    "RESULTADO_TRANSITORIO",
    "SIN_DESTINATARIOS",
    "SIN_DESTINATARIO_RESOLUBLE",
    "SIN_TRANSPORTE_ACTIVO",
    "CompositorAviso",
    "MotorAvisos",
    "ResumenCiclo",
]
