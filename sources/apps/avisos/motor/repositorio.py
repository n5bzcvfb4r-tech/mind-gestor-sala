"""
Capa de acceso a datos de la cola de avisos por correo (`aviso_correo`, ARC-115, REQ-132).

Este modulo NO decide reglas de negocio: construye consultas y persiste transiciones que otros
(el despachador, el recuperador, el servicio de outbox) ya han decidido. Lo unico que valida es la
coherencia estructural de lo que va a escribir: el grafo de estados de `apps.avisos.motor.estados`
y los catalogos cerrados que la base de datos defiende con CHECK.

Particularidades de Oracle que gobiernan el codigo de este modulo
----------------------------------------------------------------
1. El backend Oracle de Django declara `supports_select_for_update_with_limit = False`: una
   consulta `select_for_update(...)[:n]` levanta `NotSupportedError`. Por eso la toma de lotes va
   en DOS PASOS: primero se resuelven los identificadores del lote (consulta acotada, sin bloqueo)
   y despues se bloquean esos identificadores concretos con `SELECT ... FOR UPDATE SKIP LOCKED`
   SIN limite, porque el conjunto ya esta acotado por el paso anterior. El segundo paso reconfirma
   el estado BAJO el bloqueo: ahi es donde se cierra la carrera entre dos trabajadores.
2. `AvisoCorreoEntity.Meta.ordering` es `["-created_at", "-notification_id"]`, es decir
   DESCENDENTE y por tanto ANTI-FIFO. Toda consulta de consumo de la cola lleva un
   `.order_by("created_at", "notification_id")` EXPLICITO que anula ese orden por defecto.
3. `SELECT ... FOR UPDATE` exige una transaccion abierta. Los metodos que bloquean filas se
   envuelven ellos mismos en `transaction.atomic()` para ser seguros por si solos, y aun asi
   estan pensados para invocarse dentro del `transaction.atomic()` del ciclo del despachador.
4. La transaccion NO se mantiene abierta durante el envio SMTP: `tomar_pendientes` marca el lote
   como `ENVIANDO` y confirma; la entrega ocurre fuera, y de ahi la razon de ser de
   `locked_by`/`locked_at` y de la ventana de recuperacion de `recuperar_atascados`.
5. `dispatch_id` y `status_code` de `aviso_correo` son columnas `GENERATED ALWAYS AS ... VIRTUAL`
   y no estan mapeadas en el modelo (mencionarlas en un INSERT o UPDATE da ORA-54013). El campo
   fisico de estado se llama `status`.
6. Las CHECK del DDL condicionan que campos viajan en cada UPDATE: `ck_aviso_correo_locked` exige
   soltar `locked_by` al salir de `ENVIANDO`; `ck_aviso_correo_enviado` exige `message_id` y
   `sent_at` al entrar en `ENVIADO`; `ck_aviso_correo_supresion` exige motivo y fecha al entrar en
   `SUPRIMIDO`; `ck_aviso_correo_intentos` acota `attempt_count` a `max_attempts`.

Aqui no hay borrado fisico de ninguna clase (REQ-047): el modelo lo rechaza por mixin y el
repositorio no publica ninguna operacion que lo intente.
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.db import models, transaction
from django.db.models import Count, Q

from apps.avisos.motor.errores import ErrorMotorAvisos
from apps.avisos.motor.estados import MOTIVOS_SUPRESION, RESULTADOS_INTENTO, EstadoAviso, validar_transicion
from apps.core.contexto import utc_now
from apps.core.repositorios import RepositorioBase

if TYPE_CHECKING:  # pragma: no cover - solo para anotaciones; los modelos no se importan antes de django.setup()
    from apps.core.models import AvisoCorreoEntity, AvisoCorreoIntentoEntity


# Ancho de `aviso_correo.last_error_message` y de `aviso_correo_intento.error_message`:
# VARCHAR2(500 CHAR). El texto se recorta aqui para que un traceback SMTP largo no reviente
# el INSERT con ORA-12899.
LONGITUD_MAXIMA_MENSAJE_ERROR = 500

# Orden FIFO explicito de consumo de la cola: anula el `Meta.ordering` DESCENDENTE del modelo.
ORDEN_FIFO = ("created_at", "notification_id")

MOTIVO_SUPRESION_FUERA_DE_CATALOGO = "El motivo de supresion «{motivo}» no pertenece al catalogo cerrado de motivos: {catalogo}."
RESULTADO_INTENTO_FUERA_DE_CATALOGO = "El resultado de intento «{resultado}» no pertenece al catalogo cerrado de resultados: {catalogo}."
ENVIADO_SIN_MESSAGE_ID = "No se puede marcar como ENVIADO el aviso «{notification_id}» sin un identificador de mensaje SMTP."


def _recortar(texto: str | None, longitud: int = LONGITUD_MAXIMA_MENSAJE_ERROR) -> str | None:
    """Recorta el texto al ancho de la columna conservando `None` tal cual."""

    if texto is None:
        return None
    return texto[:longitud]


class RepositorioAvisoCorreo(RepositorioBase):
    """
    Acceso a datos de la cola de avisos por correo y de su traza de intentos.

    Resuelve los modelos de forma perezosa (como `RepositorioUsuario` en `apps.core.repositorios`)
    porque este modulo lo importan tanto el proceso servidor como el despachador, y los modelos no
    se pueden importar a nivel de modulo antes de `django.setup()`.
    """

    modelo_intento: type[models.Model]

    def __init__(self, modelo: type[models.Model] | None = None, modelo_intento: type[models.Model] | None = None) -> None:
        if modelo is None:
            from apps.core.models import AvisoCorreoEntity

            modelo = AvisoCorreoEntity
        if modelo_intento is None:
            from apps.core.models import AvisoCorreoIntentoEntity

            modelo_intento = AvisoCorreoIntentoEntity
        super().__init__(modelo)
        self.modelo_intento = modelo_intento

    # --- Lectura y alta ---------------------------------------------------

    def obtener_por_clave(self, notification_key: str) -> "AvisoCorreoEntity | None":
        """
        Devuelve la solicitud cuya clave de idempotencia es `notification_key`, o `None`.

        `uk_aviso_correo_notif_key` garantiza que como mucho hay una fila, de modo que el filtro
        resuelve la consulta en el indice unico y no hace falta desambiguar en memoria (ARC-115).
        """

        return self.modelo.objects.filter(notification_key=notification_key).first()

    def insertar(self, **campos: Any) -> "AvisoCorreoEntity":
        """
        Inserta una solicitud de aviso nueva (patron outbox, REQ-137).

        NO captura ninguna excepcion a proposito: la idempotencia la resuelve la capa de outbox
        sobre el `IntegrityError` que levanta `uk_aviso_correo_notif_key` cuando la misma clave ya
        existe. Tragarse aqui ese error convertiria la unicidad de la base de datos en un silencio.
        """

        aviso = self.modelo(**campos)
        aviso.save()
        return aviso

    # --- Consumo de la cola -----------------------------------------------

    def tomar_pendientes(self, *, limite: int, locked_by: str, ahora: datetime | None = None) -> list["AvisoCorreoEntity"]:
        """
        Toma en FIFO un lote de solicitudes `PENDIENTE` vencidas y las marca `ENVIANDO` (REQ-132).

        Materializa las reglas 1 a 4 de REQ-132: solo entran las solicitudes `PENDIENTE` cuyo
        `next_attempt_at` es nulo o ya ha vencido, se toman en orden de llegada y se marcan como
        `ENVIANDO` con el testigo del trabajador (`locked_by`, `locked_at`) para que dos replicas
        no envien el mismo correo.

        Va en DOS PASOS por la limitacion del backend Oracle de Django
        (`supports_select_for_update_with_limit = False`): un `select_for_update(skip_locked=True)`
        con slice levantaria `NotSupportedError`.

        * Paso 1: se resuelven los identificadores del lote con una consulta ACOTADA y sin bloqueo,
          que Oracle traduce a `FETCH FIRST n ROWS ONLY` apoyandose en el indice
          `ix_aviso_correo_fifo (status, next_attempt_at, created_at)`.
        * Paso 2: se bloquean esos identificadores concretos con `FOR UPDATE SKIP LOCKED` y SIN
          limite (el conjunto ya esta acotado por el paso 1), reconfirmando `status=PENDIENTE`
          bajo el bloqueo: esa reconfirmacion es la que cierra la carrera entre dos trabajadores,
          porque el que llega segundo ve la fila ya en `ENVIANDO` y la descarta.

        Debe invocarse dentro de `transaction.atomic()` (`SELECT ... FOR UPDATE` lo exige); el
        metodo abre ademas su propia transaccion para ser seguro por si solo. La transaccion se
        cierra aqui: la entrega SMTP ocurre DESPUES del commit, nunca con el bloqueo tomado.

        Args:
            limite: numero maximo de solicitudes del lote (tamano de lote del motor).
            locked_by: identificador del trabajador que toma el lote (`host:pid`, 60 caracteres).
            ahora: marca temporal naive en UTC; por defecto `utc_now()`.

        Returns:
            list: las solicitudes tomadas, ya actualizadas en base y en memoria, en orden FIFO.
        """

        momento = ahora if ahora is not None else utc_now()
        if limite <= 0:
            return []

        with transaction.atomic():
            # Paso 1: identificadores del lote, SIN bloqueo y CON tope (Oracle admite el limite aqui).
            # El `order_by` explicito es obligatorio: el `Meta.ordering` del modelo es DESCENDENTE.
            identificadores = list(
                self.modelo.objects.filter(status=EstadoAviso.PENDIENTE.value)
                .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=momento))
                .order_by(*ORDEN_FIFO)
                .values_list("pk", flat=True)[:limite]
            )
            if not identificadores:
                return []

            # Paso 2: bloqueo de ese conjunto ya acotado, SIN limite en la consulta.
            # Reconfirmar `status=PENDIENTE` bajo el bloqueo cierra la carrera entre trabajadores.
            tomados = list(
                self.modelo.objects.select_for_update(skip_locked=True)
                .filter(pk__in=identificadores, status=EstadoAviso.PENDIENTE.value)
                .order_by(*ORDEN_FIFO)
            )
            if not tomados:
                return []

            self.modelo.objects.filter(pk__in=[aviso.pk for aviso in tomados]).update(
                status=EstadoAviso.ENVIANDO.value,
                locked_by=locked_by,
                locked_at=momento,
            )
            # Se refleja en memoria lo mismo que se ha escrito, para que el llamante no relea.
            for aviso in tomados:
                aviso.status = EstadoAviso.ENVIANDO.value
                aviso.locked_by = locked_by
                aviso.locked_at = momento
            return tomados

    def recuperar_atascados(
        self,
        *,
        ventana_minutos: int,
        ahora: datetime | None = None,
        limite: int | None = None,
    ) -> list["AvisoCorreoEntity"]:
        """
        Devuelve a `PENDIENTE` las solicitudes atascadas en `ENVIANDO` (REQ-132 regla 5, AC-AVI-06).

        Una solicitud queda atascada cuando el trabajador que la tomo murio entre el `ENVIANDO` y
        el cierre de la transicion. Pasada la ventana (`locked_at <= ahora - ventana_minutos`) se
        devuelve a la cola SOLTANDO el testigo (`locked_by = None`, que es lo que exige la CHECK
        `ck_aviso_correo_locked`: o el estado es `ENVIANDO` o no hay testigo) y CONSERVANDO
        `locked_at` como traza del ultimo bloqueo: el DDL lo deja fuera de la CHECK justo para eso.
        `next_attempt_at` se deja a `None` para que la solicitud vuelva a la cabeza de la cola
        respetando su `created_at`, es decir, sin perder su sitio en el FIFO.

        La IDEMPOTENCIA frente a un segundo correo NO la da este metodo, sino el modelo de estados:
        una solicitud ya entregada esta en `ENVIADO` (estado terminal, con `message_id` y `sent_at`
        informados por `ck_aviso_correo_enviado`) y por tanto NUNCA entra en este filtro, que solo
        mira `ENVIANDO`. Si la entrega llego a producirse pero el proceso murio antes de confirmarla,
        la solicitud sigue en `ENVIANDO` y se reintentara: ese es el compromiso at-least-once
        declarado del motor, no un descuido de este metodo.

        Usa el mismo patron de DOS PASOS que `tomar_pendientes` (el backend Oracle no admite
        `select_for_update` con limite) y debe invocarse dentro de `transaction.atomic()`; abre
        ademas su propia transaccion para ser seguro por si solo.

        Args:
            ventana_minutos: minutos que debe llevar bloqueada la solicitud para considerarla atascada.
            ahora: marca temporal naive en UTC; por defecto `utc_now()`.
            limite: tope opcional de solicitudes a recuperar en una pasada; `None` = sin tope.

        Returns:
            list: las solicitudes devueltas a `PENDIENTE`, actualizadas en base y en memoria.
        """

        momento = ahora if ahora is not None else utc_now()
        if limite is not None and limite <= 0:
            return []
        umbral = momento - timedelta(minutes=ventana_minutos)

        with transaction.atomic():
            # Paso 1: identificadores de los atascados, SIN bloqueo. El orden explicito vuelve a ser
            # obligatorio porque el `Meta.ordering` del modelo es ANTI-FIFO.
            consulta = (
                self.modelo.objects.filter(status=EstadoAviso.ENVIANDO.value, locked_at__lte=umbral)
                .order_by(*ORDEN_FIFO)
                .values_list("pk", flat=True)
            )
            identificadores = list(consulta[:limite] if limite is not None else consulta)
            if not identificadores:
                return []

            # Paso 2: bloqueo del conjunto acotado, SIN limite, reconfirmando `ENVIANDO`.
            atascados = list(
                self.modelo.objects.select_for_update(skip_locked=True)
                .filter(pk__in=identificadores, status=EstadoAviso.ENVIANDO.value)
                .order_by(*ORDEN_FIFO)
            )
            if not atascados:
                return []

            for aviso in atascados:
                validar_transicion(aviso.status, EstadoAviso.PENDIENTE, notification_id=str(aviso.pk))

            # `locked_by` viaja a NULL en el MISMO UPDATE que el estado (CHECK ck_aviso_correo_locked);
            # `locked_at` se conserva como traza del ultimo bloqueo.
            self.modelo.objects.filter(pk__in=[aviso.pk for aviso in atascados]).update(
                status=EstadoAviso.PENDIENTE.value,
                locked_by=None,
                next_attempt_at=None,
            )
            for aviso in atascados:
                aviso.status = EstadoAviso.PENDIENTE.value
                aviso.locked_by = None
                aviso.next_attempt_at = None
            return atascados

    # --- Cierre de la transicion de una solicitud --------------------------

    def marcar_enviado(self, aviso: "AvisoCorreoEntity", *, message_id: str, sent_at: datetime | None = None) -> "AvisoCorreoEntity":
        """
        Cierra la entrega con exito: `ENVIANDO -> ENVIADO` (REQ-132 regla 3, REQ-142 regla 2).

        Valida la transicion contra el grafo ANTES de escribir, de modo que un aviso ya entregado
        no se reescribe (`AvisoYaEntregadoError`). La CHECK `ck_aviso_correo_enviado` exige
        `message_id` y `sent_at` NOT NULL cuando el estado es `ENVIADO`, asi que ambos viajan en el
        mismo UPDATE; `locked_by` se suelta a la vez por `ck_aviso_correo_locked`, y el rastro de
        error y el reintento pendiente se limpian porque la entrega ha prosperado.

        `attempt_count` viaja en el UPDATE para persistir el incremento que `incrementar_intento`
        hizo en memoria durante este ciclo de entrega.

        Args:
            aviso: solicitud en `ENVIANDO` a cerrar.
            message_id: identificador del mensaje devuelto por el servidor SMTP.
            sent_at: marca temporal naive en UTC de la entrega; por defecto `utc_now()`.

        Returns:
            AvisoCorreoEntity: la solicitud actualizada.

        Raises:
            ErrorMotorAvisos: si no hay `message_id`, o si la transicion no esta permitida.
        """

        if not message_id:
            raise ErrorMotorAvisos(ENVIADO_SIN_MESSAGE_ID.format(notification_id=aviso.pk))
        validar_transicion(aviso.status, EstadoAviso.ENVIADO, notification_id=str(aviso.pk))

        with transaction.atomic():
            aviso.status = EstadoAviso.ENVIADO.value
            aviso.message_id = message_id
            aviso.sent_at = sent_at if sent_at is not None else utc_now()
            aviso.locked_by = None
            aviso.last_error_code = None
            aviso.last_error_message = None
            aviso.next_attempt_at = None
            # `update_fields` lista SOLO los campos tocados: evita reescribir `body_text` y
            # `body_html` (CLOB) en cada transicion de la cola.
            aviso.save(
                update_fields=[
                    "status",
                    "message_id",
                    "sent_at",
                    "locked_by",
                    "last_error_code",
                    "last_error_message",
                    "next_attempt_at",
                    "attempt_count",
                ]
            )
        return aviso

    def devolver_a_pendiente(
        self,
        aviso: "AvisoCorreoEntity",
        *,
        next_attempt_at: datetime,
        last_error_code: str | None,
        last_error_message: str | None,
    ) -> "AvisoCorreoEntity":
        """
        Programa un reintento tras un fallo transitorio: `ENVIANDO -> PENDIENTE` (REQ-134, AC-SMTP-04).

        El calendario del reintento (el backoff de 1, 5 y 15 minutos) NO se decide aqui: llega ya
        resuelto en `next_attempt_at`. Este metodo solo persiste la vuelta a la cola soltando el
        testigo en el MISMO UPDATE que el estado (`ck_aviso_correo_locked`) y dejando el rastro del
        ultimo error, recortado al ancho de la columna VARCHAR2(500 CHAR).

        `attempt_count` viaja en el UPDATE para persistir el incremento que `incrementar_intento`
        hizo en memoria durante este ciclo de entrega: sin el, el contador de reintentos no
        avanzaria nunca y la solicitud quedaria en bucle.

        Args:
            aviso: solicitud en `ENVIANDO` cuyo intento ha fallado de forma transitoria.
            next_attempt_at: momento naive en UTC a partir del cual la solicitud vuelve a ser elegible.
            last_error_code: codigo del error del intento.
            last_error_message: texto del error (nunca credenciales SMTP, REQ-063).

        Returns:
            AvisoCorreoEntity: la solicitud actualizada.
        """

        validar_transicion(aviso.status, EstadoAviso.PENDIENTE, notification_id=str(aviso.pk))

        with transaction.atomic():
            aviso.status = EstadoAviso.PENDIENTE.value
            aviso.next_attempt_at = next_attempt_at
            aviso.locked_by = None
            aviso.last_error_code = last_error_code
            aviso.last_error_message = _recortar(last_error_message)
            aviso.save(
                update_fields=[
                    "status",
                    "next_attempt_at",
                    "locked_by",
                    "last_error_code",
                    "last_error_message",
                    "attempt_count",
                ]
            )
        return aviso

    def marcar_fallido(
        self,
        aviso: "AvisoCorreoEntity",
        *,
        last_error_code: str | None,
        last_error_message: str | None,
    ) -> "AvisoCorreoEntity":
        """
        Agota los reintentos de un fallo transitorio: `ENVIANDO -> FALLIDO` (REQ-134 regla 5).

        `FALLIDO` no tiene continuacion automatica (el despachador no vuelve a tomar la solicitud),
        pero no es terminal: el ADMINISTRADOR puede ordenar un reenvio manual sobre la misma
        solicitud. Por eso `next_attempt_at` se deja a `None` (nada que programar) y `locked_by` se
        suelta en el mismo UPDATE que el estado (`ck_aviso_correo_locked`).

        Args:
            aviso: solicitud en `ENVIANDO` que ha agotado sus intentos.
            last_error_code: codigo del ultimo error observado.
            last_error_message: texto del ultimo error, recortado a 500 caracteres.

        Returns:
            AvisoCorreoEntity: la solicitud actualizada.
        """

        return self._cerrar_sin_entrega(
            aviso,
            destino=EstadoAviso.FALLIDO,
            last_error_code=last_error_code,
            last_error_message=last_error_message,
        )

    def marcar_descartado(
        self,
        aviso: "AvisoCorreoEntity",
        *,
        last_error_code: str | None,
        last_error_message: str | None,
    ) -> "AvisoCorreoEntity":
        """
        Cierra un rechazo permanente del servidor SMTP: `ENVIANDO -> DESCARTADO` (REQ-141, AC-SMTP-04).

        Un 5xx permanente (destinatario inexistente, mensaje rechazado) no se reintenta: insistir
        solo consumiria la ventana de envio. Igual que `marcar_fallido`, deja `next_attempt_at` a
        `None`, suelta el testigo y admite el reenvio manual del ADMINISTRADOR.

        Args:
            aviso: solicitud en `ENVIANDO` rechazada de forma permanente.
            last_error_code: codigo del error permanente.
            last_error_message: texto del error, recortado a 500 caracteres.

        Returns:
            AvisoCorreoEntity: la solicitud actualizada.
        """

        return self._cerrar_sin_entrega(
            aviso,
            destino=EstadoAviso.DESCARTADO,
            last_error_code=last_error_code,
            last_error_message=last_error_message,
        )

    def _cerrar_sin_entrega(
        self,
        aviso: "AvisoCorreoEntity",
        *,
        destino: EstadoAviso,
        last_error_code: str | None,
        last_error_message: str | None,
    ) -> "AvisoCorreoEntity":
        """Cierre comun de `FALLIDO` y `DESCARTADO`: mismo UPDATE, distinto estado destino."""

        validar_transicion(aviso.status, destino, notification_id=str(aviso.pk))

        with transaction.atomic():
            aviso.status = destino.value
            aviso.next_attempt_at = None
            aviso.locked_by = None
            aviso.last_error_code = last_error_code
            aviso.last_error_message = _recortar(last_error_message)
            aviso.save(
                update_fields=[
                    "status",
                    "next_attempt_at",
                    "locked_by",
                    "last_error_code",
                    "last_error_message",
                    "attempt_count",
                ]
            )
        return aviso

    def marcar_suprimido(
        self,
        aviso: "AvisoCorreoEntity",
        *,
        suppression_reason_code: str,
        ahora: datetime | None = None,
    ) -> "AvisoCorreoEntity":
        """
        Marca la solicitud como no entregable: `-> SUPRIMIDO` (REQ-140, REQ-141, ARC-115).

        Un aviso suprimido no tiene entrega y no vuelve a la cola: es el cierre de los casos sin
        destinatario resoluble, sin correo o con composicion incompleta. La CHECK
        `ck_aviso_correo_supresion` exige motivo y `suppressed_at` informados si y solo si el estado
        es `SUPRIMIDO`, asi que ambos viajan en el mismo UPDATE junto con el `locked_by` liberado
        (`ck_aviso_correo_locked`). El motivo se valida antes contra el catalogo cerrado
        `MOTIVOS_SUPRESION`, que es el mismo de `ck_aviso_correo_motivo_sup`.

        Args:
            aviso: solicitud a suprimir.
            suppression_reason_code: motivo del catalogo cerrado de supresion.
            ahora: marca temporal naive en UTC; por defecto `utc_now()`.

        Returns:
            AvisoCorreoEntity: la solicitud actualizada.

        Raises:
            ErrorMotorAvisos: si el motivo no pertenece al catalogo cerrado.
        """

        if suppression_reason_code not in MOTIVOS_SUPRESION:
            catalogo = ", ".join(sorted(MOTIVOS_SUPRESION))
            raise ErrorMotorAvisos(MOTIVO_SUPRESION_FUERA_DE_CATALOGO.format(motivo=suppression_reason_code, catalogo=catalogo))
        validar_transicion(aviso.status, EstadoAviso.SUPRIMIDO, notification_id=str(aviso.pk))

        with transaction.atomic():
            aviso.status = EstadoAviso.SUPRIMIDO.value
            aviso.suppression_reason_code = suppression_reason_code
            aviso.suppressed_at = ahora if ahora is not None else utc_now()
            aviso.locked_by = None
            aviso.next_attempt_at = None
            aviso.save(
                update_fields=[
                    "status",
                    "suppression_reason_code",
                    "suppressed_at",
                    "locked_by",
                    "next_attempt_at",
                ]
            )
        return aviso

    # --- Traza de intentos y contadores ------------------------------------

    def registrar_intento(
        self,
        aviso: "AvisoCorreoEntity",
        *,
        result_code: str,
        smtp_response_code: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        recipients_snapshot: str | None = None,
        recipient_count: int = 0,
        message_id: str | None = None,
    ) -> "AvisoCorreoIntentoEntity":
        """
        Escribe la traza append-only de un intento de entrega en `aviso_correo_intento` (ARC-119).

        La tabla es inmutable por mixin: aqui solo se INSERTA, nunca se reescribe. El
        `attempt_number` se toma de `aviso.attempt_count`, que el llamante ya ha incrementado con
        `incrementar_intento`, porque `uk_aviso_intento_correlativo (notification_id,
        attempt_number)` exige que el numero sea correlativo y unico dentro del aviso. El
        `result_code` se valida contra el catalogo cerrado `RESULTADOS_INTENTO`, el mismo de la
        CHECK `ck_aviso_intento_result`, y el texto del error se recorta al ancho de la columna.

        Args:
            aviso: solicitud a la que pertenece el intento.
            result_code: resultado del intento, del catalogo cerrado.
            smtp_response_code: codigo de respuesta SMTP de tres digitos, si lo hubo.
            error_code: codigo del error del intento, si lo hubo.
            error_message: texto del error (nunca credenciales SMTP, REQ-063), recortado a 500.
            recipients_snapshot: destinatarios vigentes en el intento (nombre y correo corporativo).
            recipient_count: numero de destinatarios del intento.
            message_id: identificador del mensaje entregado, si el intento prospero.

        Returns:
            AvisoCorreoIntentoEntity: la entrada de traza recien insertada.

        Raises:
            ErrorMotorAvisos: si el resultado no pertenece al catalogo cerrado.
        """

        if result_code not in RESULTADOS_INTENTO:
            catalogo = ", ".join(sorted(RESULTADOS_INTENTO))
            raise ErrorMotorAvisos(RESULTADO_INTENTO_FUERA_DE_CATALOGO.format(resultado=result_code, catalogo=catalogo))

        intento = self.modelo_intento(
            notification=aviso,
            # Se copia la incidencia del aviso cuando la tiene: hay tipos de aviso sin incidencia.
            incident_id=aviso.incident_id,
            attempt_number=aviso.attempt_count,
            result_code=result_code,
            smtp_response_code=smtp_response_code,
            error_code=error_code,
            error_message=_recortar(error_message),
            recipients_snapshot=recipients_snapshot,
            recipient_count=recipient_count,
            message_id=message_id,
        )
        intento.save()
        return intento

    def incrementar_intento(self, aviso: "AvisoCorreoEntity") -> int:
        """
        Sube en memoria el contador de intentos respetando el tope `max_attempts` (REQ-142 regla 3).

        La CHECK `ck_aviso_correo_intentos` exige `attempt_count <= max_attempts`: si el contador ya
        esta en el tope NO se sube, se devuelve el valor actual y la decision de cerrar la solicitud
        como `FALLIDO` queda en manos del despachador (este repositorio no decide reglas). El valor
        se persiste en el mismo UPDATE que cierra la transicion (`marcar_*`, `devolver_a_pendiente`),
        de modo que no hay un UPDATE adicional por intento.

        Args:
            aviso: solicitud cuyo intento se esta contabilizando.

        Returns:
            int: el `attempt_count` resultante.
        """

        if aviso.attempt_count < aviso.max_attempts:
            aviso.attempt_count = aviso.attempt_count + 1
        return aviso.attempt_count

    def contar_por_estado(self, **filtros: Any) -> dict[str, int]:
        """
        Recuento de solicitudes agrupadas por estado de envio, para la traza de operacion del despachador.

        La agregacion se resuelve EN LA CONSULTA (`GROUP BY status`), nunca recorriendo filas en
        memoria: la cola puede tener cientos de miles de solicitudes y traerlas para contarlas seria
        un error de diseno. El `order_by()` vacio es imprescindible: el `Meta.ordering` del modelo
        arrastraria `created_at` al `GROUP BY` y romperia la agrupacion.

        Args:
            **filtros: filtros opcionales del ORM que acotan el recuento (tipo de aviso, fechas...).

        Returns:
            dict: estado de envio -> numero de solicitudes; los estados sin filas no aparecen.
        """

        agregado = self.modelo.objects.filter(**filtros).values("status").annotate(total=Count("notification_id")).order_by()
        return {fila["status"]: fila["total"] for fila in agregado}


__all__ = [
    "ENVIADO_SIN_MESSAGE_ID",
    "LONGITUD_MAXIMA_MENSAJE_ERROR",
    "MOTIVO_SUPRESION_FUERA_DE_CATALOGO",
    "ORDEN_FIFO",
    "RESULTADO_INTENTO_FUERA_DE_CATALOGO",
    "RepositorioAvisoCorreo",
]
