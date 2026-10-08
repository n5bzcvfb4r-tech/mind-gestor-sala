"""
Pruebas de integracion del motor de cola de avisos contra Oracle 23ai Free REAL.

Que acreditara este fichero
---------------------------
El comportamiento del motor de avisos (`apps.avisos.motor`) cuando opera sobre la tabla
`aviso_correo` usada como outbox transaccional: el encolado idempotente por clave de
negocio, la toma FIFO con bloqueo (`SELECT ... FOR UPDATE SKIP LOCKED`), el ciclo de
entrega con su politica de reintentos y backoff, y la recuperacion de avisos que se
quedaron bloqueados por un worker caido.

Por que el oraculo es el dato escrito y releido en Oracle
---------------------------------------------------------
El oraculo de estas pruebas es SIEMPRE la fila efectivamente escrita y vuelta a LEER
DESDE la base Oracle del contenedor, nunca una estructura en memoria del proceso de test.
La razon no es de estilo: lo que el motor promete (unicidad de la clave de aviso, atomicidad
del cambio de estado, exclusion mutua entre workers, durabilidad del contador de intentos)
lo garantizan el DDL y el gestor transaccional de Oracle, no el codigo Python. Un `dict` en
memoria satisface cualquier asercion sobre el motor sin probar nada: no tiene indice unico,
no tiene transacciones y no tiene `SKIP LOCKED`, de modo que un test verde contra el daria
una falsa evidencia justo en los puntos donde el motor puede fallar en produccion.

Por eso esta PROHIBIDO degradar el backend a SQLite, H2 o cualquier doble en memoria. El
esquema bajo prueba es el DDL REAL de produccion, aplicado por Liquibase sobre el changelog
`sources/facilities/master.xml` (las entidades son `managed = False`, ARC-016), y la imagen
es la del proyecto: `gvenzl/oracle-free:23-slim`. Si no hay engine Docker alcanzable las
pruebas se SALTAN con `MOTIVO_SIN_DOCKER` y quedan visibles en el informe; nunca se
desactivan en silencio ni cambian de motor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import pytest

from apps.avisos.motor.claves import clave_aviso_alta
from apps.avisos.motor.configuracion import ConfiguracionMotorAvisos
from apps.avisos.motor.despachador import MotorAvisos
from apps.avisos.motor.estados import EstadoAviso
from apps.avisos.motor.outbox import TIPO_ALTA, ServicioOutboxAvisos
from apps.avisos.motor.repositorio import RepositorioAvisoCorreo
from apps.avisos.motor.transporte import MensajeCorreo, ResultadoEntrega
from apps.core.contexto import ContextoSesion, contexto_de_sesion, utc_now
from apps.core.models import (
    AvisoCorreoEntity,
    CategoriaIncidenciaEntity,
    EstadoIncidenciaEntity,
    IncidenciaEntity,
    RolEntity,
    SalaEntity,
    UsuarioEntity,
)

try:  # El conftest hermano es importable como modulo del paquete de pruebas.
    from apps.avisos.tests.conftest import MOTIVO_SIN_DOCKER, hay_docker
except ImportError:  # pragma: no cover - red de seguridad si cambia el layout
    MOTIVO_SIN_DOCKER = "Requiere un engine Docker alcanzable para levantar Oracle 23ai con Testcontainers."

    def hay_docker() -> bool:
        """Deteccion minima del engine Docker si el conftest hermano no fuese importable."""

        try:
            import docker  # type: ignore[import-not-found]

            docker.from_env().ping()
            return True
        except Exception:
            return False


#: Marca lista para decorar los tests que SI necesitan el contenedor Oracle real.
SALTAR_SIN_DOCKER = pytest.mark.skipif(not hay_docker(), reason=MOTIVO_SIN_DOCKER)

pytestmark = [pytest.mark.integration]


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_el_esquema_de_avisos_esta_disponible(esquema_aplicado: bool) -> None:
    """Ancla del fichero: sin esquema aplicado no hay nada que probar."""

    assert esquema_aplicado


# --- Dobles y semillas del escenario -------------------------------------

#: Testigo del worker que «muere» con la solicitud ya tomada en ENVIANDO.
WORKER_CAIDO = "worker-caido"

#: Minutos que lleva bloqueada la solicitud atascada: muy por fuera de la ventana de recuperacion.
MINUTOS_ATASCADA = 60


@dataclass
class TransporteQueCuenta:
    """
    Doble del puerto `TransporteCorreo` que CUENTA las entregas, sin hablar SMTP.

    Vive SOLO en las pruebas: el codigo de producto no contiene ningun modo simulado (la fabrica
    `transporte_por_defecto` devuelve `None` si no hay configuracion SMTP activa, nunca un envio
    fingido). Este doble sustituye UNICAMENTE el extremo de red, no el oraculo del DoD: la cola, los
    estados y la unicidad se siguen verificando contra el Oracle real del contenedor.

    `enviar` respeta el contrato del puerto: no lanza nunca y devuelve un `ResultadoEntrega`.
    """

    mensajes: list[MensajeCorreo] = field(default_factory=list)

    def enviar(self, mensaje: MensajeCorreo) -> ResultadoEntrega:
        """Acumula el mensaje y lo da por aceptado con un `Message-ID` propio del doble."""

        self.mensajes.append(mensaje)
        return ResultadoEntrega.aceptado(f"<doble-{len(self.mensajes)}@avisos.pruebas.local>")

    @property
    def envios(self) -> int:
        """Numero de correos que el motor ha pedido entregar a traves de este transporte."""

        return len(self.mensajes)


def _sembrar_actor(sufijo: str) -> UsuarioEntity:
    """
    Crea la cuenta que actua de actor de la prueba y la RELEE de la base para conocer su IDENTITY.

    Se inserta con `bulk_create` siguiendo el patron de `apps/core/tests/test_persistencia_oracle.py`:
    ese camino NO pasa por `save()` y por tanto NO invoca al `AtribucionMixin`, de modo que la cuenta
    se puede escribir cuando todavia no hay ningun contexto de sesion publicado al que atribuirla.
    En Oracle `bulk_create` no devuelve la PK generada, asi que el `user_id` solo se conoce releyendo.
    """

    correo = f"motor.avisos.{sufijo}@mind.local"
    UsuarioEntity.objects.bulk_create(
        [
            UsuarioEntity(
                full_name=f"Actor del motor de avisos ({sufijo})",
                corporate_email=correo,
                username=f"motor.avisos.{sufijo}",
                role_code=RolEntity.objects.get(pk="ADMINISTRADOR"),
                status="ACTIVO",
                password_hash=f"hash-argon2id-motor-avisos-{sufijo}",
                password_salt=f"sal-motor-avisos-{sufijo}",
                password_algorithm="argon2id",
                must_change_password="Y",
            )
        ]
    )
    return UsuarioEntity.objects.get(corporate_email=correo)


def _contexto_de(actor: UsuarioEntity, session_id: str) -> ContextoSesion:
    """Contexto de sesion del actor: lo exige el `AtribucionMixin` para sellar `reported_by` (REQ-064)."""

    return ContextoSesion(
        user_id=actor.user_id,
        role_code="ADMINISTRADOR",
        session_id=session_id,
        data_scope="ALL",
        display_name=actor.full_name,
    )


def _sembrar_incidencia(referencia: str) -> IncidenciaEntity:
    """
    Da de alta una incidencia que satisface las FK de `aviso_correo` (`incident_id` NOT NULL).

    Los catalogos (sala, oficina, categoria, estado) NO se inventan: se LEEN de las semillas que el
    changelog dml ya dejo aplicadas, igual que en la prueba de persistencia de `apps.core`. Debe
    invocarse DENTRO de un `contexto_de_sesion`, porque `reported_by` lo sella el `AtribucionMixin`.
    """

    categoria = CategoriaIncidenciaEntity.objects.filter(is_active="Y").first()
    assert categoria is not None, "cat_categoria_incidencia no tiene ninguna categoria activa sembrada"
    sala = SalaEntity.objects.filter(is_active="Y").select_related("office").first()
    assert sala is not None, "cat_sala no tiene ninguna sala activa sembrada"

    incidencia = IncidenciaEntity(
        reference_code=referencia,
        room=sala,
        category=categoria,
        room_name_snapshot=sala.room_name,
        office_name_snapshot=sala.office.office_name,
        category_name_snapshot=categoria.category_name,
        description=f"Incidencia de apoyo a la prueba del motor de avisos ({referencia})",
        status=EstadoIncidenciaEntity.objects.get(pk="ABIERTA"),
    )
    incidencia.save()
    return incidencia


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_la_caida_del_worker_no_genera_un_segundo_correo(
    esquema_aplicado: bool,
    config_motor: ConfiguracionMotorAvisos,
) -> None:
    """
    DoD / AC-AVI-06 y AC-SMTP-02: la recuperacion de atascadas NO genera un segundo correo.

    Escenario (una caida de worker simulada sobre el Oracle real):

    * Solicitud A, ATASCADA: un worker la tomo (`ENVIANDO`, `locked_by='worker-caido'`) y murio
      antes de cerrar la transicion. Su `locked_at` tiene 60 minutos, muy por fuera de la ventana de
      recuperacion (15 minutos de `config_motor`).
    * Solicitud B, YA ENTREGADA: esta en `ENVIADO` con `message_id` y `sent_at` informados, que es lo
      que exige `ck_aviso_correo_enviado`. Es la que NO debe volver a enviarse jamas.

    Lo que se acredita, releyendo SIEMPRE desde la base (nunca el objeto en memoria):

    1. La recuperacion devuelve A a `PENDIENTE` y SUELTA el testigo (`locked_by IS NULL`, que ademas
       impone `ck_aviso_correo_locked` fuera de `ENVIANDO`).
    2. B sigue intacta en `ENVIADO` con su `message_id` original: el filtro de recuperacion solo mira
       `ENVIANDO`, de modo que una solicitud ya entregada no entra nunca.
    3. Al procesar la cola, el transporte recibe EXACTAMENTE UNA peticion de envio. Esa es la
       asercion central del acuerdo: de las M solicitudes ya entregadas se generan 0 reenvios.
    """

    assert esquema_aplicado

    actor = _sembrar_actor("caida-worker")
    with contexto_de_sesion(_contexto_de(actor, "33333333-3333-4333-8333-333333333333")):
        incidencia_atascada = _sembrar_incidencia("INC-MOTOR-ATASCADA-01")
        incidencia_entregada = _sembrar_incidencia("INC-MOTOR-ENTREGADA-01")

    momento = utc_now()

    # Solicitud A: tomada por un worker que murio hace 60 minutos. `ck_aviso_correo_vinculo` exige
    # incidencia informada y entrada de historial vacia para `NEW_INCIDENT_ALERT`.
    atascada = AvisoCorreoEntity(
        notification_key="NEW_INCIDENT_ALERT:INC:MOTOR-ATASCADA-01",
        notification_type="NEW_INCIDENT_ALERT",
        incident=incidencia_atascada,
        history_entry=None,
        recipient_user=actor,
        recipient_email=actor.corporate_email,
        subject="Nueva incidencia INC-MOTOR-ATASCADA-01",
        body_text="La solicitud quedo bloqueada por un worker que murio antes de cerrar la transicion.",
        status=EstadoAviso.ENVIANDO.value,
        attempt_count=0,
        max_attempts=3,
        locked_by=WORKER_CAIDO,
        locked_at=momento - timedelta(minutes=MINUTOS_ATASCADA),
        created_at=momento - timedelta(minutes=MINUTOS_ATASCADA),
    )
    atascada.save()

    # Solicitud B: entrega ya confirmada. `ck_aviso_correo_enviado` exige `message_id` y `sent_at`,
    # y `ck_aviso_correo_locked` prohibe el testigo fuera de `ENVIANDO`.
    message_id_entregada = "<ya-entregado-01@avisos.pruebas.local>"
    entregada = AvisoCorreoEntity(
        notification_key="NEW_INCIDENT_ALERT:INC:MOTOR-ENTREGADA-01",
        notification_type="NEW_INCIDENT_ALERT",
        incident=incidencia_entregada,
        history_entry=None,
        recipient_user=actor,
        recipient_email=actor.corporate_email,
        subject="Nueva incidencia INC-MOTOR-ENTREGADA-01",
        body_text="La solicitud ya fue entregada por el servidor SMTP antes de la caida del worker.",
        status=EstadoAviso.ENVIADO.value,
        attempt_count=1,
        max_attempts=3,
        locked_by=None,
        locked_at=momento - timedelta(minutes=MINUTOS_ATASCADA),
        message_id=message_id_entregada,
        sent_at=momento - timedelta(minutes=MINUTOS_ATASCADA),
        created_at=momento - timedelta(minutes=MINUTOS_ATASCADA),
    )
    entregada.save()

    # --- Recuperacion de la ventana ------------------------------------------------
    recuperados = MotorAvisos(config=config_motor).recuperar_atascados()
    assert recuperados == 1, "solo la solicitud atascada en ENVIANDO entra en la ventana de recuperacion"

    # 1. La atascada vuelve a la cola y suelta el testigo. Se RELEE de la base, no del objeto.
    atascada_releida = AvisoCorreoEntity.objects.get(pk=atascada.pk)
    assert atascada_releida.status == EstadoAviso.PENDIENTE.value, "la atascada debe volver a PENDIENTE"
    assert atascada_releida.locked_by is None, "la recuperacion suelta el testigo (ck_aviso_correo_locked)"
    assert atascada_releida.next_attempt_at is None, "vuelve a la cabeza del FIFO, sin reintento programado"
    assert atascada_releida.message_id is None, "la atascada nunca llego a entregarse"

    # 2. La ya entregada no la toca nadie: el filtro de recuperacion solo mira ENVIANDO.
    entregada_releida = AvisoCorreoEntity.objects.get(pk=entregada.pk)
    assert entregada_releida.status == EstadoAviso.ENVIADO.value, "ENVIADO es terminal: la recuperacion no lo revierte"
    assert entregada_releida.message_id == message_id_entregada, "el message_id de la entrega original queda intacto"
    assert entregada_releida.locked_by is None

    # --- Proceso de la cola con un transporte que cuenta ---------------------------
    transporte = TransporteQueCuenta()
    resumen = MotorAvisos(transporte=transporte, config=config_motor).procesar_lote()

    assert resumen.tomados == 1, "solo la solicitud recuperada esta PENDIENTE y es elegible"
    assert resumen.entregados == 1

    atascada_entregada = AvisoCorreoEntity.objects.get(pk=atascada.pk)
    assert atascada_entregada.status == EstadoAviso.ENVIADO.value, "la solicitud recuperada se entrega en el siguiente ciclo"
    assert atascada_entregada.message_id, "ck_aviso_correo_enviado exige message_id en ENVIADO"
    assert atascada_entregada.locked_by is None

    # 3. ASERCION CENTRAL (AC-AVI-06 / AC-SMTP-02): de las solicitudes ya entregadas se generan
    #    CERO reenvios. Un segundo correo aqui significaria un aviso duplicado en el buzon del
    #    destinatario tras cada caida de worker.
    assert transporte.envios == 1, (
        "la recuperacion tras la caida del worker debe generar EXACTAMENTE un envio (el de la solicitud atascada) "
        f"y 0 reenvios de las ya entregadas; el transporte recibio {transporte.envios} peticiones de envio"
    )

    entregada_final = AvisoCorreoEntity.objects.get(pk=entregada.pk)
    assert entregada_final.message_id == message_id_entregada, "la solicitud ya entregada no genera un segundo correo"
    assert entregada_final.sent_at == entregada_releida.sent_at, "su fecha de envio original no se reescribe"


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_la_cola_de_avisos_persiste_en_oracle_y_es_idempotente(
    esquema_aplicado: bool,
    config_motor: ConfiguracionMotorAvisos,
) -> None:
    """
    DoD: «Persistencia: python-oracledb + test contra Testcontainers Oracle (dict != BBDD)».

    Todo lo que esta prueba afirma esta ESCRITO en el Oracle del contenedor y RELEIDO de el con una
    consulta nueva: en ningun punto se comprueba el objeto que devolvio el servicio. Un `dict` en
    memoria pasaria cualquiera de estas aserciones sin acreditar nada, porque no tiene indice unico,
    ni transacciones, ni `SELECT ... FOR UPDATE SKIP LOCKED`.

    Tres hechos, los tres con la base como oraculo:

    1. ENCOLADO: `ServicioOutboxAvisos.encolar_aviso_alta` deja una fila `PENDIENTE`, con cero
       intentos, sin testigo de worker y con la `notification_key` que dicta `clave_aviso_alta`.
    2. IDEMPOTENCIA REAL: el segundo encolado del MISMO aviso de alta devuelve `creado=False` y la
       tabla sigue teniendo UNA sola fila para esa incidencia y tipo. Quien lo impide es la
       restriccion de la base (`uk_aviso_correo_notif_key` / `ux_aviso_correo_alta` respondiendo
       ORA-00001), no una comprobacion previa en Python.
    3. TOMA FIFO CON BLOQUEO: `RepositorioAvisoCorreo.tomar_pendientes` marca el lote como
       `ENVIANDO` con el testigo del worker y respeta el orden de llegada por `created_at`, pese a
       que el `Meta.ordering` del modelo es descendente.
    """

    assert esquema_aplicado

    actor = _sembrar_actor("persistencia-cola")
    with contexto_de_sesion(_contexto_de(actor, "44444444-4444-4444-8444-444444444444")):
        primera = _sembrar_incidencia("INC-MOTOR-COLA-01")
        segunda = _sembrar_incidencia("INC-MOTOR-COLA-02")
        tercera = _sembrar_incidencia("INC-MOTOR-COLA-03")

    outbox = ServicioOutboxAvisos(config=config_motor)

    # --- 1. Encolado y RELECTURA desde la base ------------------------------------
    resultado = outbox.encolar_aviso_alta(incident_id=primera.incident_id, recipient_email=actor.corporate_email)
    assert resultado.creado is True, "el primer encolado del aviso de alta crea la solicitud"

    clave_esperada = clave_aviso_alta(primera.incident_id)
    # Consulta NUEVA contra Oracle: no se mira el objeto devuelto por el servicio.
    encolado = AvisoCorreoEntity.objects.get(notification_key=clave_esperada)
    assert encolado.notification_type == TIPO_ALTA
    assert encolado.incident_id == primera.incident_id
    assert encolado.history_entry_id is None, "ck_aviso_correo_vinculo exige history_entry_id nulo en NEW_INCIDENT_ALERT"
    assert encolado.status == EstadoAviso.PENDIENTE.value
    assert encolado.attempt_count == 0
    assert encolado.locked_by is None, "una solicitud recien encolada no tiene testigo de worker"
    assert encolado.locked_at is None
    assert encolado.next_attempt_at is None
    assert encolado.created_at is not None, "created_at ordena el FIFO y queda persistido en la base"

    # --- 2. Idempotencia contra la restriccion unica REAL -------------------------
    repetido = outbox.encolar_aviso_alta(incident_id=primera.incident_id, recipient_email=actor.corporate_email)
    assert repetido.creado is False, "el segundo encolado del mismo aviso de alta es idempotente (ORA-00001)"
    assert repetido.notification_key == clave_esperada

    filas_de_la_incidencia = AvisoCorreoEntity.objects.filter(incident_id=primera.incident_id, notification_type=TIPO_ALTA).count()
    assert filas_de_la_incidencia == 1, (
        "tras dos encolados del mismo aviso de alta debe quedar EXACTAMENTE una fila en aviso_correo: "
        f"la unicidad la defiende la base (ux_aviso_correo_alta), y hay {filas_de_la_incidencia}"
    )
    assert AvisoCorreoEntity.objects.get(notification_key=clave_esperada).pk == encolado.pk, (
        "la fila preexistente es la misma, no una segunda solicitud con otra PK"
    )

    # --- 3. Toma FIFO con bloqueo --------------------------------------------------
    # Dos avisos de alta mas, de OTRAS incidencias, para no chocar con la unicidad del primero.
    segundo = outbox.encolar_aviso_alta(incident_id=segunda.incident_id, recipient_email=actor.corporate_email)
    tercero = outbox.encolar_aviso_alta(incident_id=tercera.incident_id, recipient_email=actor.corporate_email)
    assert segundo.creado is True
    assert tercero.creado is True

    repositorio = RepositorioAvisoCorreo()
    tomados = repositorio.tomar_pendientes(limite=2, locked_by=config_motor.identificador_worker)
    assert len(tomados) == 2, "el lote se acota al limite pedido"

    # El orden del lote es el de llegada, no el `Meta.ordering` DESCENDENTE del modelo.
    claves_tomadas = [aviso.notification_key for aviso in tomados]
    assert claves_tomadas == [clave_esperada, segundo.notification_key], (
        f"la toma debe ser FIFO por created_at; llegaron en el orden {claves_tomadas}"
    )

    # RELECTURA desde la base de las dos tomadas: el bloqueo tiene que estar ESCRITO, no en memoria.
    for clave in claves_tomadas:
        tomado = AvisoCorreoEntity.objects.get(notification_key=clave)
        assert tomado.status == EstadoAviso.ENVIANDO.value, "la toma deja la solicitud en ENVIANDO"
        assert tomado.locked_by == config_motor.identificador_worker, "el testigo del worker queda persistido"
        assert tomado.locked_at is not None, "locked_at marca el instante del bloqueo"

    # El tercero NO entro en el lote y sigue intacto y disponible para el siguiente ciclo.
    sobrante = AvisoCorreoEntity.objects.get(notification_key=tercero.notification_key)
    assert sobrante.status == EstadoAviso.PENDIENTE.value, "lo que excede el limite se queda en la cola"
    assert sobrante.locked_by is None

    # El FIFO se comprueba ademas sobre el propio dato persistido: created_at creciente.
    marcas = list(
        AvisoCorreoEntity.objects.filter(notification_key__in=[*claves_tomadas, tercero.notification_key])
        .order_by("created_at", "notification_id")
        .values_list("notification_key", flat=True)
    )
    assert marcas == [*claves_tomadas, tercero.notification_key], "el orden por created_at en la base es el de encolado"
