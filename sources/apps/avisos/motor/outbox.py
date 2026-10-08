"""
Cara de ESCRITURA del motor de avisos: el patron OUTBOX sobre `aviso_correo` (ARC-115, REQ-130, REQ-132, REQ-137).

Este modulo es la API que consumen las demas unidades de negocio (alta de incidencia, cambio de
estado, emision de credencial) para ENCOLAR un aviso. Aqui no se envia nada y no se abre ni se
cierra ninguna transaccion: solo se INSERTA la solicitud.

REQ-132 / ADR-006 - EL PATRON OUTBOX
====================================
«La solicitud se persiste DENTRO de la misma transaccion del alta, de modo que no se pierde ningun
aviso si el proceso cae tras el commit»; «un fallo de correo nunca revierte el alta».

Y el anti-patron que el handbook declara PROHIBIDO: «enviar el correo SMTP dentro de la transaccion
del alta, o encolar el aviso en una lista en memoria del proceso».

De ahi las tres reglas que gobiernan todo lo que sigue:

1. Este modulo SOLO hace INSERT. No compone, no entrega y no habla con SMTP. La entrega la hace el
   despachador DESPUES del commit del negocio, leyendo la cola de la base de datos.
2. Este modulo NUNCA abre ni cierra la transaccion del llamante: no hay `transaction.commit()` ni
   `transaction.rollback()` en ninguna linea. La frontera transaccional pertenece al servicio de
   negocio que invoca el encolado (handbook: el repositorio y el outbox NO deciden la transaccion).
   El unico `atomic` que aparece aqui es un savepoint ANIDADO, y su razon de ser se explica abajo.
3. La cola es la TABLA, nunca una estructura en memoria del proceso. No hay ninguna lista, cola ni
   diccionario de modulo en este fichero: una cola en memoria se perderia al reiniciar el contenedor,
   que es exactamente el fallo que REQ-132 obliga a evitar.

REQ-130 / AC-AVI-01 - LA IDEMPOTENCIA
=====================================
«Existe exactamente una solicitud `NEW_INCIDENT_ALERT` por incidencia»; «un segundo intento de
registrar el mismo aviso se ignora de forma IDEMPOTENTE».

El mecanismo real NO es una consulta previa de «¿ya existe?» (dos transacciones concurrentes la
pasarian las dos y encolarian dos avisos): es la restriccion unica `uk_aviso_correo_notif_key` sobre
`notification_key`, reforzada por el indice unico funcional parcial `ux_aviso_correo_alta`. La base
responde **ORA-00001** al segundo INSERT, que el backend de Django traduce a `IntegrityError`, y este
modulo lo interpreta como «ya estaba encolado» devolviendo `ResultadoEncolado(creado=False)` con la
fila preexistente. El llamante no tiene que distinguir los dos casos para funcionar.

LA TRAMPA DEL SAVEPOINT (lo mas importante de este fichero)
===========================================================
Capturar el `IntegrityError` del INSERT duplicado SIN un savepoint deja la transaccion del llamante
marcada como «needs rollback»: a partir de ese punto, CUALQUIER consulta posterior del alta de la
incidencia falla con `TransactionManagementError` y el alta entera se cae. Es decir, el intento de
ser idempotente reventaria justo el alta que se pretendia proteger.

Por eso el INSERT va envuelto en `with transaction.atomic(savepoint=True):` ANIDADO dentro de la
transaccion del negocio: al chocar con la unicidad se deshace SOLO el savepoint, la transaccion
exterior sigue viva y puede hacer commit con la incidencia ya dada de alta. El `except` se escribe
FUERA del `with` a proposito, porque el savepoint se deshace al salir del bloque: solo entonces la
conexion vuelve a admitir la relectura de la fila preexistente.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.db import IntegrityError, transaction

from apps.avisos.motor.claves import clave_aviso_alta, clave_aviso_cambio_estado, clave_aviso_credencial, validar_clave
from apps.avisos.motor.configuracion import ConfiguracionMotorAvisos, configuracion_motor
from apps.avisos.motor.errores import ErrorMotorAvisos
from apps.avisos.motor.estados import TIPOS_AVISO, EstadoAviso
from apps.avisos.motor.repositorio import RepositorioAvisoCorreo
from apps.core.contexto import utc_now

if TYPE_CHECKING:  # pragma: no cover - solo para anotaciones; los modelos no se importan antes de django.setup()
    from apps.core.models import AvisoCorreoEntity


# Literales del catalogo cerrado `ck_aviso_correo_tipo`. Son un subconjunto de `TIPOS_AVISO`, que
# sigue siendo la fuente unica de verdad: `encolar` comprueba la pertenencia contra ella.
TIPO_ALTA = "NEW_INCIDENT_ALERT"
TIPO_CAMBIO_ESTADO = "STATUS_CHANGE_ALERT"
TIPO_CREDENCIAL_EMITIDA = "CREDENTIAL_ISSUED"
TIPO_RESTABLECIMIENTO = "PASSWORD_RESET"

#: Los dos tipos de aviso que no cuelgan de ninguna incidencia (CHECK `ck_aviso_correo_vinculo`).
TIPOS_CREDENCIAL: frozenset[str] = frozenset({TIPO_CREDENCIAL_EMITIDA, TIPO_RESTABLECIMIENTO})

#: Formato del discriminante derivado del reloj para los avisos de credencial (`clave_aviso_credencial`).
FORMATO_DISCRIMINANTE = "%Y%m%d%H%M%S%f"

TIPO_AVISO_FUERA_DE_CATALOGO = "El tipo de aviso «{tipo}» no pertenece al catalogo cerrado de tipos: {catalogo}."
TIPO_NO_ES_DE_CREDENCIAL = "El tipo de aviso «{tipo}» no es un aviso de credencial; los admitidos son: {catalogo}."
TRANSICION_SIN_CAMBIO = (
    "No se puede encolar un aviso de cambio de estado de la incidencia «{incident_id}» con el mismo estado anterior y "
    "nuevo («{estado}»): un cambio al mismo estado no es una transicion (CHECK `ck_aviso_correo_transicion`, REQ-137)."
)


@dataclass(frozen=True, slots=True)
class ResultadoEncolado:
    """
    Resultado de un encolado, con la respuesta a «¿se ha creado ahora o ya estaba?».

    `creado=False` NO es un error: significa que el encolado fue IDEMPOTENTE (REQ-130, AC-AVI-01) y
    que `aviso` es la solicitud que ya existia con esa misma `notification_key`. El llamante puede
    ignorar el dato sin consecuencias; lo necesita solo quien quiera trazar el reproceso.
    """

    aviso: "AvisoCorreoEntity"
    creado: bool
    notification_key: str


class ServicioOutboxAvisos:
    """
    Servicio de encolado de avisos por correo: la unica puerta de ESCRITURA del outbox.

    Las dependencias se resuelven de forma PEREZOSA (como `RepositorioAvisoCorreo` hace con sus
    modelos): nada se construye a nivel de modulo, porque los modelos del ORM no son importables
    antes de `django.setup()` y `configuracion_motor()` lee `settings`. Ambas se pueden inyectar,
    que es como las pruebas sustituyen el acceso a datos por un doble sin tocar el codigo de producto.
    """

    def __init__(self, repositorio: RepositorioAvisoCorreo | None = None, config: ConfiguracionMotorAvisos | None = None) -> None:
        self._repositorio: RepositorioAvisoCorreo | None = repositorio
        self._config: ConfiguracionMotorAvisos | None = config

    # --- Resolucion perezosa de dependencias -------------------------------

    def _resolver_repositorio(self) -> RepositorioAvisoCorreo:
        """Devuelve el repositorio inyectado o construye el real en el primer uso, nunca a nivel de modulo."""

        if self._repositorio is None:
            self._repositorio = RepositorioAvisoCorreo()
        return self._repositorio

    def _resolver_config(self) -> ConfiguracionMotorAvisos:
        """Devuelve la configuracion inyectada o la lee de `settings.AVISOS_MOTOR` en el primer uso."""

        if self._config is None:
            self._config = configuracion_motor()
        return self._config

    # --- Metodo base --------------------------------------------------------

    def encolar(self, *, notification_type: str, notification_key: str, **campos: Any) -> ResultadoEncolado:
        """
        Inserta una solicitud de aviso en la cola, de forma idempotente, DENTRO de la transaccion del llamante.

        Es el metodo base del outbox: los `encolar_aviso_*` solo componen la clave y las referencias
        propias de su tipo y terminan aqui.

        Que hace, en orden:

        1. Valida `notification_type` contra el catalogo cerrado `TIPOS_AVISO` (el mismo de
           `ck_aviso_correo_tipo`) y la `notification_key` contra el ancho de su columna, de modo que
           una llamada incoherente falle en Python y en espanol, y no como un ORA-02290 o un ORA-12899
           en mitad de la transaccion de negocio.
        2. Inserta con el estado inicial de la cola: `PENDIENTE`, sin intentos, sin reintento
           programado y sin testigo de trabajador (`locked_by`/`locked_at` a `None`, que es lo que
           exige `ck_aviso_correo_locked` fuera de `ENVIANDO`). El tope de intentos es el que pase el
           llamante o, en su defecto, `config.max_attempts_por_defecto` (REQ-134).
        3. Si la base responde `IntegrityError` (ORA-00001 de `uk_aviso_correo_notif_key`), NO es un
           error del llamante: el aviso ya estaba encolado. Se relee por clave y se devuelve con
           `creado=False`. Si la relectura devuelve `None`, la colision vino de OTRA restriccion
           distinta a la de idempotencia (una FK, una CHECK): ocultarla esconderia un defecto de datos
           real, asi que el `IntegrityError` original se RE-LANZA tal cual.

        El INSERT va dentro de `transaction.atomic(savepoint=True)` ANIDADO. Sin ese savepoint, atrapar
        el `IntegrityError` dejaria la transaccion del llamante en estado «needs rollback» y la
        siguiente consulta del alta moriria con `TransactionManagementError`: el intento de ser
        idempotente reventaria el alta que se pretendia proteger. Con el, se deshace SOLO el savepoint
        y la transaccion exterior sigue viva y puede hacer commit.

        Aqui NO se llama a `transaction.commit()` ni a `transaction.rollback()`: la frontera
        transaccional es del servicio de negocio que encola.

        Args:
            notification_type: tipo del catalogo cerrado `ck_aviso_correo_tipo`.
            notification_key: clave de idempotencia ya construida por `apps.avisos.motor.claves`.
            **campos: resto de columnas del aviso (referencias, destinatario, contenido, `max_attempts`).

        Returns:
            ResultadoEncolado: la solicitud y si se ha creado ahora (`creado=True`) o ya existia.

        Raises:
            ErrorMotorAvisos: si el tipo no pertenece al catalogo cerrado.
            ClaveAvisoInvalidaError: si la clave esta vacia, tiene espacios de borde o excede 120 caracteres.
            IntegrityError: si la colision no es la de idempotencia (otra restriccion de la tabla).
        """

        if notification_type not in TIPOS_AVISO:
            catalogo = ", ".join(sorted(TIPOS_AVISO))
            raise ErrorMotorAvisos(TIPO_AVISO_FUERA_DE_CATALOGO.format(tipo=notification_type, catalogo=catalogo))
        validar_clave(notification_key)

        repositorio = self._resolver_repositorio()
        max_attempts = campos.pop("max_attempts", None)
        if max_attempts is None:
            max_attempts = self._resolver_config().max_attempts_por_defecto

        try:
            # SAVEPOINT ANIDADO: es la razon de ser de este metodo. Al chocar con
            # `uk_aviso_correo_notif_key` se deshace SOLO este bloque; la transaccion del alta que nos
            # envuelve NO queda marcada como «needs rollback» y puede seguir consultando y confirmando.
            with transaction.atomic(savepoint=True):
                aviso = repositorio.insertar(
                    notification_key=notification_key,
                    notification_type=notification_type,
                    status=EstadoAviso.PENDIENTE.value,
                    attempt_count=0,
                    max_attempts=max_attempts,
                    created_at=utc_now(),
                    next_attempt_at=None,
                    locked_by=None,
                    locked_at=None,
                    **campos,
                )
        except IntegrityError:
            # El `except` esta FUERA del `with` a proposito: el savepoint se deshace al salir del
            # bloque, y solo entonces la conexion admite la relectura que sigue.
            existente = repositorio.obtener_por_clave(notification_key)
            if existente is None:
                # La colision no fue la de idempotencia (otra restriccion de la tabla): se deja subir.
                raise
            return ResultadoEncolado(aviso=existente, creado=False, notification_key=notification_key)

        return ResultadoEncolado(aviso=aviso, creado=True, notification_key=notification_key)

    # --- Encolados por tipo de aviso ----------------------------------------

    def encolar_aviso_alta(
        self,
        *,
        incident_id: int,
        recipient_email: str | None = None,
        recipient_user_id: int | None = None,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        max_attempts: int | None = None,
    ) -> ResultadoEncolado:
        """
        Encola el aviso de alta de una incidencia: `NEW_INCIDENT_ALERT` (REQ-130, AC-AVI-01).

        Se invoca DENTRO de la transaccion del alta: la solicitud se confirma con la incidencia, de
        modo que ningun aviso se pierde si el proceso cae tras el commit, y un fallo posterior de
        correo nunca revierte el alta (REQ-132 / ADR-006).

        La clave es `clave_aviso_alta(incident_id)`, es decir, la incidencia por si sola: hay
        EXACTAMENTE un aviso de alta por incidencia, y por eso un segundo encolado devuelve
        `creado=False` con la solicitud que ya existia. `history_entry_id` viaja SIEMPRE a `None`
        porque `ck_aviso_correo_vinculo` lo exige para este tipo.

        El CONTENIDO (`subject`, `body_text`, `body_html`) es OPCIONAL aqui: la composicion de la
        plantilla es responsabilidad de otra unidad (AVI-02) y puede llegar despues del encolado.
        Mientras no este compuesto, el despachador NO lo entrega: una solicitud sin asunto ni cuerpo
        acaba en `SUPRIMIDO` con motivo `COMPOSICION_INCOMPLETA`, nunca como un correo vacio.

        Args:
            incident_id: incidencia recien dada de alta.
            recipient_email: correo del destinatario, si el llamante ya lo ha resuelto.
            recipient_user_id: usuario destinatario, si el aviso se dirige a uno concreto.
            subject: asunto ya compuesto, si lo hubiera.
            body_text: cuerpo en texto plano ya compuesto, si lo hubiera.
            body_html: cuerpo en HTML ya compuesto, si lo hubiera.
            max_attempts: tope de intentos; por defecto `config.max_attempts_por_defecto`.

        Returns:
            ResultadoEncolado: la solicitud y si se ha creado ahora o ya estaba encolada.
        """

        return self.encolar(
            notification_type=TIPO_ALTA,
            notification_key=clave_aviso_alta(incident_id),
            incident_id=incident_id,
            history_entry_id=None,
            recipient_user_id=recipient_user_id,
            recipient_email=recipient_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            max_attempts=max_attempts,
        )

    def encolar_aviso_cambio_estado(
        self,
        *,
        incident_id: int,
        history_entry_id: int,
        previous_status_code: str,
        new_status_code: str,
        recipient_email: str | None = None,
        recipient_user_id: int | None = None,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        max_attempts: int | None = None,
    ) -> ResultadoEncolado:
        """
        Encola el aviso de cambio de estado de una incidencia: `STATUS_CHANGE_ALERT` (REQ-137).

        Se invoca DENTRO de la transaccion que escribe el asiento de historico, por el mismo motivo
        que el aviso de alta. La clave es `clave_aviso_cambio_estado(incident_id, history_entry_id)`:
        hay un unico aviso por entrada de historial, asi que un reproceso del cambio de estado NO
        genera un segundo correo (devuelve `creado=False`).

        `ck_aviso_correo_transicion` exige que ambos codigos de estado esten informados y que
        DIFIERAN: «un cambio al mismo estado no es una transicion y no genera aviso». Se comprueba
        aqui, en espanol, para que el llamante no se encuentre la violacion de CHECK dentro de su
        propia transaccion.

        Los dos codigos de estado viajan al ORM como `previous_status_code_id` / `new_status_code_id`
        porque en `AvisoCorreoEntity` son claves ajenas a `cat_estado_incidencia`: el sufijo `_id` es
        el `attname` del campo y permite escribir el codigo directamente, sin resolver la fila del
        catalogo.

        El contenido es opcional por la misma razon que en el aviso de alta (lo compone AVI-02).

        Args:
            incident_id: incidencia cuyo estado ha cambiado.
            history_entry_id: asiento de historico que registra la transicion.
            previous_status_code: codigo de estado de origen (`cat_estado_incidencia`).
            new_status_code: codigo de estado de destino (`cat_estado_incidencia`).
            recipient_email: correo del destinatario, si el llamante ya lo ha resuelto.
            recipient_user_id: usuario destinatario, si el aviso se dirige a uno concreto.
            subject: asunto ya compuesto, si lo hubiera.
            body_text: cuerpo en texto plano ya compuesto, si lo hubiera.
            body_html: cuerpo en HTML ya compuesto, si lo hubiera.
            max_attempts: tope de intentos; por defecto `config.max_attempts_por_defecto`.

        Returns:
            ResultadoEncolado: la solicitud y si se ha creado ahora o ya estaba encolada.

        Raises:
            ErrorMotorAvisos: si el estado anterior y el nuevo son el mismo.
        """

        if previous_status_code == new_status_code:
            raise ErrorMotorAvisos(TRANSICION_SIN_CAMBIO.format(incident_id=incident_id, estado=new_status_code))

        return self.encolar(
            notification_type=TIPO_CAMBIO_ESTADO,
            notification_key=clave_aviso_cambio_estado(incident_id, history_entry_id),
            incident_id=incident_id,
            history_entry_id=history_entry_id,
            previous_status_code_id=previous_status_code,
            new_status_code_id=new_status_code,
            recipient_user_id=recipient_user_id,
            recipient_email=recipient_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            max_attempts=max_attempts,
        )

    def encolar_aviso_credencial(
        self,
        *,
        notification_type: str,
        recipient_user_id: int,
        discriminante: str | None = None,
        recipient_email: str | None = None,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        max_attempts: int | None = None,
    ) -> ResultadoEncolado:
        """
        Encola un aviso de credencial: `CREDENTIAL_ISSUED` o `PASSWORD_RESET`.

        Se invoca DENTRO de la transaccion que emite la credencial o registra el restablecimiento.

        A diferencia de los avisos de incidencia, el usuario destinatario NO identifica una emision
        concreta: el mismo usuario puede pedir el restablecimiento de su contrasena tantas veces como
        quiera, y las dos emisiones DEBEN coexistir. Por eso la clave lleva un `discriminante`; si no
        se aporta, se deriva del reloj con `utc_now().strftime("%Y%m%d%H%M%S%f")`. Es la UNICA fuente
        de no determinismo de este modulo, y es deliberada: sin ella, el segundo restablecimiento
        chocaria con la clave del primero y el usuario se quedaria esperando un correo que nunca llega.
        Quien necesite un encolado reproducible (un reproceso que SI debe ser idempotente) pasa su
        propio discriminante.

        `incident_id` y `history_entry_id` viajan SIEMPRE a `None` y `recipient_user_id` es
        obligatorio: es literalmente lo que exige `ck_aviso_correo_vinculo` para estos dos tipos.

        Args:
            notification_type: `CREDENTIAL_ISSUED` o `PASSWORD_RESET`.
            recipient_user_id: usuario al que se emite la credencial o se restablece la contrasena.
            discriminante: segmento que distingue esta emision; por defecto, la marca temporal de ahora.
            recipient_email: correo del destinatario, si el llamante ya lo ha resuelto.
            subject: asunto ya compuesto, si lo hubiera.
            body_text: cuerpo en texto plano ya compuesto, si lo hubiera.
            body_html: cuerpo en HTML ya compuesto, si lo hubiera.
            max_attempts: tope de intentos; por defecto `config.max_attempts_por_defecto`.

        Returns:
            ResultadoEncolado: la solicitud y si se ha creado ahora o ya estaba encolada.

        Raises:
            ErrorMotorAvisos: si el tipo no es uno de los dos tipos de credencial.
        """

        if notification_type not in TIPOS_CREDENCIAL:
            catalogo = ", ".join(sorted(TIPOS_CREDENCIAL))
            raise ErrorMotorAvisos(TIPO_NO_ES_DE_CREDENCIAL.format(tipo=notification_type, catalogo=catalogo))

        marca = discriminante if discriminante is not None else utc_now().strftime(FORMATO_DISCRIMINANTE)
        return self.encolar(
            notification_type=notification_type,
            notification_key=clave_aviso_credencial(notification_type, recipient_user_id, marca),
            incident_id=None,
            history_entry_id=None,
            recipient_user_id=recipient_user_id,
            recipient_email=recipient_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            max_attempts=max_attempts,
        )

    # --- Consulta -----------------------------------------------------------

    def ya_encolado(self, notification_key: str) -> bool:
        """
        Indica si ya existe una solicitud con esa clave de idempotencia.

        Es una consulta INFORMATIVA (para la traza o para una vista), NO el mecanismo de idempotencia:
        entre este `SELECT` y el `INSERT` cabe otra transaccion concurrente. Quien garantiza que no
        hay dos avisos es `uk_aviso_correo_notif_key`, que `encolar` gestiona sobre el savepoint.
        """

        return self._resolver_repositorio().obtener_por_clave(notification_key) is not None


def outbox() -> ServicioOutboxAvisos:
    """
    Punto de entrada estable del outbox para las unidades consumidoras.

    Devuelve una instancia NUEVA en cada llamada: el servicio no guarda estado de negocio (solo sus
    dependencias perezosas), asi que no hay motivo para compartir una instancia de modulo, y un
    singleton mutable arrastraria la configuracion leida en el arranque durante toda la vida del
    proceso. Quien necesite inyectar dobles construye `ServicioOutboxAvisos` directamente.
    """

    return ServicioOutboxAvisos()


__all__ = [
    "FORMATO_DISCRIMINANTE",
    "TIPOS_CREDENCIAL",
    "TIPO_ALTA",
    "TIPO_AVISO_FUERA_DE_CATALOGO",
    "TIPO_CAMBIO_ESTADO",
    "TIPO_CREDENCIAL_EMITIDA",
    "TIPO_NO_ES_DE_CREDENCIAL",
    "TIPO_RESTABLECIMIENTO",
    "TRANSICION_SIN_CAMBIO",
    "ResultadoEncolado",
    "ServicioOutboxAvisos",
    "outbox",
]
