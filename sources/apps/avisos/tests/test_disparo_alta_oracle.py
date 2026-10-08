"""
Pruebas de integracion del disparo del aviso de alta de incidencia contra Oracle 23ai Free REAL.

Que acreditara este fichero
---------------------------
El comportamiento del consumidor del evento de alta (`apps.avisos.alta.disparo`) cuando una
incidencia recien creada publica su aviso: el encolado de UNA sola solicitud de correo en la
tabla `aviso_correo` usada como outbox transaccional, la idempotencia del disparo frente a
reemisiones del mismo evento (misma clave de negocio, un unico correo), y el cableado del
receptor en el composition root (`AvisosConfig.ready()`), que debe quedar registrado UNA sola
vez para que un evento no se traduzca en dos encolados.

Por que el oraculo es el dato escrito y releido en Oracle
---------------------------------------------------------
El oraculo de estas pruebas es SIEMPRE la fila efectivamente escrita y vuelta a LEER DESDE la
base Oracle del contenedor, nunca una estructura en memoria del proceso de test. La razon no es
de estilo: lo que el disparo promete —que un alta produzca exactamente una solicitud de aviso
pase lo que pase— lo garantiza el indice unico sobre la clave de negocio y la transaccion de
Oracle, no el codigo Python. Un `dict` en memoria satisface cualquier asercion sobre el disparo
sin probar nada: no tiene restriccion de unicidad ni atomicidad, de modo que un test verde
contra el daria una falsa evidencia justo donde el disparo puede fallar en produccion
(reintento del evento, dos workers, reentrega de la senal).

Por eso esta PROHIBIDO degradar el backend a SQLite, H2 o cualquier doble en memoria. El
esquema bajo prueba es el DDL REAL de produccion, aplicado por Liquibase sobre el changelog
`sources/facilities/master.xml` (las entidades son `managed = False`, ARC-016), y la imagen es
la del proyecto: `gvenzl/oracle-free:23-slim`. Si no hay engine Docker alcanzable las pruebas
se SALTAN con `MOTIVO_SIN_DOCKER` y quedan visibles en el informe; nunca se desactivan en
silencio ni cambian de motor.
"""

from __future__ import annotations

import pytest
from django.db import transaction

from apps.avisos.alta.disparo import aviso_solicitado, publicar_alta_incidencia
from apps.avisos.motor.claves import clave_aviso_alta
from apps.avisos.motor.estados import EstadoAviso
from apps.avisos.motor.outbox import TIPO_ALTA, TIPO_CAMBIO_ESTADO
from apps.core.contexto import ContextoSesion, contexto_de_sesion
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


# --- Semillas del escenario ----------------------------------------------


def _sembrar_actor(sufijo: str) -> UsuarioEntity:
    """
    Crea la cuenta que actua de actor de la prueba y la RELEE de la base para conocer su IDENTITY.

    Se inserta con `bulk_create` siguiendo el patron de `apps/core/tests/test_persistencia_oracle.py`:
    ese camino NO pasa por `save()` y por tanto NO invoca al `AtribucionMixin`, de modo que la cuenta
    se puede escribir cuando todavia no hay ningun contexto de sesion publicado al que atribuirla.
    En Oracle `bulk_create` no devuelve la PK generada, asi que el `user_id` solo se conoce releyendo.
    """

    correo = f"disparo.alta.{sufijo}@mind.local"
    UsuarioEntity.objects.bulk_create(
        [
            UsuarioEntity(
                full_name=f"Actor del disparo de alta ({sufijo})",
                corporate_email=correo,
                username=f"disparo.alta.{sufijo}",
                role_code=RolEntity.objects.get(pk="ADMINISTRADOR"),
                status="ACTIVO",
                password_hash=f"hash-argon2id-disparo-alta-{sufijo}",
                password_salt=f"sal-disparo-alta-{sufijo}",
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
        description=f"Incidencia de apoyo a la prueba del disparo de alta ({referencia})",
        status=EstadoIncidenciaEntity.objects.get(pk="ABIERTA"),
    )
    incidencia.save()
    return incidencia


# --- Cableado en el composition root --------------------------------------


def test_el_consumidor_del_evento_esta_conectado_en_el_composition_root() -> None:
    """
    DoD: el receptor del disparo queda conectado a la senal UNA sola vez, y solo una.

    No necesita base de datos ni contenedor: el oraculo es el registro de receptores de la senal
    `aviso_solicitado` tal y como lo dejo `AvisosConfig.ready()` al arrancar Django. Se comprueba
    ademas que una segunda invocacion de `conectar_disparo_alta()` NO anade un segundo receptor:
    el `dispatch_uid` es lo que impide que un unico evento de alta acabe encolando dos veces.
    """

    from apps.avisos.alta.disparo import (
        DISPATCH_UID,
        aviso_solicitado,
        conectar_disparo_alta,
    )

    receptores = len(aviso_solicitado.receivers)
    assert receptores == 1, (
        "AvisosConfig.ready() debe dejar EXACTAMENTE un receptor conectado a `aviso_solicitado`; "
        f"hay {receptores}."
    )

    conectar_disparo_alta()

    receptores_tras_reconectar = len(aviso_solicitado.receivers)
    assert receptores_tras_reconectar == 1, (
        f"Reconectar con dispatch_uid={DISPATCH_UID!r} no debe duplicar el receptor (duplicaria el "
        f"encolado del aviso); tras la segunda llamada hay {receptores_tras_reconectar}."
    )


# --- Publicacion y consumo del evento de alta -----------------------------


@SALTAR_SIN_DOCKER
@pytest.mark.django_db(transaction=True)
def test_AC_AVI_01_el_evento_de_alta_deja_exactamente_una_solicitud_por_incidencia(
    esquema_aplicado: bool,
) -> None:
    """
    [AC-AVI-01] «Existe EXACTAMENTE una solicitud NEW_INCIDENT_ALERT por incidencia».

    Recorre el camino completo de EVT-001 por su cara publica: se PUBLICA el evento con
    `publicar_alta_incidencia` (que es `Signal.send`, el transporte interno declarado en el
    asyncapi) y lo consume el receptor que `AvisosConfig.ready()` dejo conectado. Para reproducir el
    reproceso real -reintento del alta, doble submit del formulario, reentrega de la señal- el MISMO
    evento se publica DOS veces.

    Oraculo: la tabla `aviso_correo` del Oracle del contenedor, releida con una consulta NUEVA. Se
    cuenta cuantas filas hay para ese `incident_id` con tipo `NEW_INCIDENT_ALERT` y tiene que haber
    UNA, con la clave de negocio que dicta `clave_aviso_alta` y en estado PENDIENTE. Quien impide la
    segunda es el indice unico de la base (ORA-00001 absorbido sobre el savepoint del outbox), no una
    comprobacion previa en Python: por eso el oraculo no puede ser un doble en memoria, que no tiene
    restriccion de unicidad y daria verde sin acreditar nada.

    No hay limpieza explicita al final a proposito: el borrado FISICO esta prohibido en el dominio
    (`SinBorradoFisicoMixin`, REQ-047: las bajas son logicas), de modo que la prueba se aisla con su
    propio `reference_code` y su propia incidencia, igual que el fichero hermano
    `test_motor_avisos_oracle.py`.
    """

    assert esquema_aplicado

    actor = _sembrar_actor("ac-avi-01")
    with contexto_de_sesion(_contexto_de(actor, "55555555-5555-4555-8555-555555555555")):
        incidencia = _sembrar_incidencia("INC-DISPARO-ALTA-01")

    clave_esperada = clave_aviso_alta(incidencia.incident_id)

    # --- Primera publicacion: el consumo CREA la solicitud -------------------------
    respuestas = publicar_alta_incidencia(incident_id=incidencia.incident_id)
    assert len(respuestas) == 1, f"el evento debe entregarse a UN unico receptor; respondieron {len(respuestas)}"

    primer_resultado = respuestas[0][1]
    assert primer_resultado.ignorado is False, (
        f"un evento de alta no se ignora; el consumidor devolvio ignorado={primer_resultado.ignorado} "
        f"con motivo {primer_resultado.motivo!r}"
    )
    assert primer_resultado.creado is True, f"la primera publicacion del alta debe CREAR la solicitud; creado={primer_resultado.creado}"
    assert primer_resultado.notification_key == clave_esperada, (
        f"la clave de negocio del aviso debe ser {clave_esperada!r}; fue {primer_resultado.notification_key!r}"
    )

    # --- Segunda publicacion del MISMO evento: idempotente -------------------------
    respuestas_repetidas = publicar_alta_incidencia(incident_id=incidencia.incident_id)
    segundo_resultado = respuestas_repetidas[0][1]
    assert segundo_resultado.creado is False, (
        f"reprocesar el MISMO evento de alta no puede crear una segunda solicitud; creado={segundo_resultado.creado}"
    )
    assert segundo_resultado.notification_key == clave_esperada, (
        f"el reproceso debe resolver la MISMA clave de idempotencia: se esperaba {clave_esperada!r} y "
        f"llego {segundo_resultado.notification_key!r}"
    )

    # --- Oraculo: lo que quedo ESCRITO en Oracle, releido --------------------------
    solicitudes = AvisoCorreoEntity.objects.filter(
        incident_id=incidencia.incident_id,
        notification_type=TIPO_ALTA,
    ).count()
    assert solicitudes == 1, (
        "tras publicar DOS veces el evento de alta de la misma incidencia debe quedar EXACTAMENTE una "
        f"fila NEW_INCIDENT_ALERT en aviso_correo (AC-AVI-01); en Oracle hay {solicitudes}"
    )

    encolada = AvisoCorreoEntity.objects.get(notification_key=clave_esperada)
    assert encolada.incident_id == incidencia.incident_id, (
        f"la solicitud debe apuntar a la incidencia {incidencia.incident_id}; apunta a {encolada.incident_id}"
    )
    assert encolada.notification_type == TIPO_ALTA, f"el tipo persistido debe ser {TIPO_ALTA}; es {encolada.notification_type}"
    assert encolada.history_entry_id is None, (
        f"ck_aviso_correo_vinculo exige history_entry_id nulo en el aviso de alta; vale {encolada.history_entry_id}"
    )
    assert encolada.status == EstadoAviso.PENDIENTE.value, (
        f"la solicitud recien encolada queda PENDIENTE de despacho; su estado en Oracle es {encolada.status}"
    )


@SALTAR_SIN_DOCKER
@pytest.mark.django_db(transaction=True)
def test_un_alta_revertida_no_deja_ninguna_solicitud_de_aviso(
    esquema_aplicado: bool,
) -> None:
    """
    DoD: «un alta fallida o revertida no deja ninguna solicitud».

    Es el patron OUTBOX de REQ-132 / ADR-006: la solicitud de aviso se persiste DENTRO de la misma
    transaccion del alta, nunca en una propia. La entrega de `aviso_solicitado` es sincrona y en el
    mismo hilo, asi que el INSERT en `aviso_correo` cae en el commit del negocio: o se confirman la
    incidencia y su aviso juntos, o no se confirma ninguno de los dos. Si el consumidor abriese su
    propio `atomic` (o encolase en `on_commit` invertido), el aviso sobreviviria al rollback y se
    enviaria un correo anunciando una incidencia que no existe.

    Para revertir de verdad -no simulado- se publica el evento dentro de un `transaction.atomic()` y
    se levanta una excepcion propia de la prueba ANTES de salir del bloque: asi Django emite el
    ROLLBACK real contra Oracle, que es como se cae una transaccion de negocio. La excepcion se
    captura fuera para poder seguir interrogando a la base.

    La incidencia de apoyo se siembra FUERA del `atomic` que se revierte: si cayera dentro, el
    rollback se la llevaria tambien y el recuento final valdria cero por el motivo equivocado, sin
    probar nada sobre el aviso.

    Oraculo: cero filas `NEW_INCIDENT_ALERT` en `aviso_correo` para esa incidencia, releidas de
    Oracle despues del rollback. La incidencia, en cambio, sigue ahi, lo que demuestra que el cero no
    viene de que la relectura no encuentre nada.
    """

    assert esquema_aplicado

    actor = _sembrar_actor("alta-revertida")
    with contexto_de_sesion(_contexto_de(actor, "66666666-6666-4666-8666-666666666666")):
        incidencia = _sembrar_incidencia("INC-DISPARO-ALTA-02")

    clave_esperada = clave_aviso_alta(incidencia.incident_id)

    class _AltaRevertida(Exception):
        """Fallo deliberado del alta: obliga a Django a revertir la transaccion de negocio."""

    # --- Alta que publica su aviso y acto seguido se va al traste ------------------
    with pytest.raises(_AltaRevertida):
        with transaction.atomic():
            respuestas = publicar_alta_incidencia(incident_id=incidencia.incident_id)
            resultado = respuestas[0][1]
            assert resultado.creado is True, (
                "dentro de la transaccion el aviso SI se encola (es lo que luego debe revertirse); "
                f"creado={resultado.creado}"
            )
            raise _AltaRevertida("el alta falla despues de encolar su aviso")

    # --- Oraculo: tras el ROLLBACK no queda ni rastro de la solicitud --------------
    solicitudes = AvisoCorreoEntity.objects.filter(
        incident_id=incidencia.incident_id,
        notification_type=TIPO_ALTA,
    ).count()
    assert solicitudes == 0, (
        "un alta revertida no puede dejar NINGUNA solicitud de aviso (REQ-132 / ADR-006: la solicitud "
        f"se confirma con el alta o no se confirma); en Oracle quedan {solicitudes} filas"
    )
    assert not AvisoCorreoEntity.objects.filter(notification_key=clave_esperada).exists(), (
        f"la clave {clave_esperada!r} no debe existir en aviso_correo tras el rollback del alta"
    )

    # Testigo de que el cero anterior es un cero REAL y no un efecto de haberlo perdido todo.
    assert IncidenciaEntity.objects.filter(pk=incidencia.pk).exists(), (
        "la incidencia de apoyo se sembro FUERA del atomic revertido y debe seguir en Oracle: si no "
        "estuviera, el recuento de avisos valdria cero por el motivo equivocado"
    )


@SALTAR_SIN_DOCKER
@pytest.mark.django_db(transaction=True)
def test_AC_AVI_02_los_cambios_de_estado_posteriores_no_generan_una_nueva_solicitud(
    esquema_aplicado: bool,
) -> None:
    """
    [AC-AVI-02] «cambios de estado posteriores (en curso/resuelta/cerrada) no generan una nueva solicitud».

    Una incidencia no se queda quieta en ABIERTA: recorre EN_CURSO, RESUELTA y CERRADA, y cada
    transicion publica su propio evento por el MISMO canal interno `aviso_solicitado`, esta vez con
    `notification_type = STATUS_CHANGE_ALERT`. Lo que se pone a prueba aqui es si el consumidor del
    alta sabe quedarse al margen de esos eventos. Si no lo hiciera, cada paso del ciclo de vida
    encolaria OTRO `NEW_INCIDENT_ALERT` de la misma incidencia: el equipo de mantenimiento recibiria
    cuatro correos anunciando un alta que ocurrio una sola vez, y la promesa de AC-AVI-01
    («exactamente una solicitud por incidencia») quedaria rota por la puerta de atras.

    Los tres eventos se publican enviando la señal directamente, que es la cara de publicacion
    generica del canal: `publicar_alta_incidencia` solo sabe publicar altas (fija `TIPO_ALTA` y
    `history_entry_id=None`), de modo que no sirve para hacer de productor de los cambios de estado.
    Cada transicion viaja con su propio `history_entry_id`, como haria el asiento de historico real.

    Oraculo: la tabla `aviso_correo` del Oracle del contenedor, RELEIDA despues de los tres eventos.
    No basta con mirar el `ResultadoDisparo` devuelto -un consumidor roto podria devolver
    `ignorado=True` y haber insertado igualmente-, por eso se comprueba contra la base que (a) sigue
    habiendo UNA sola fila `NEW_INCIDENT_ALERT`, y que es LA MISMA de antes (misma `notification_key`
    y mismo `notification_id`: ni se duplico ni se reemplazo), y (b) hay CERO filas
    `STATUS_CHANGE_ALERT`, porque esas las encola `encolar_aviso_cambio_estado` con su propia clave
    por asiento de historial y no este consumidor.
    """

    assert esquema_aplicado

    actor = _sembrar_actor("ac-avi-02")
    with contexto_de_sesion(_contexto_de(actor, "77777777-7777-4777-8777-777777777777")):
        incidencia = _sembrar_incidencia("INC-DISPARO-ALTA-03")

    clave_esperada = clave_aviso_alta(incidencia.incident_id)

    # --- Precondicion: el alta ya esta publicada y consumida -----------------------
    respuestas_alta = publicar_alta_incidencia(incident_id=incidencia.incident_id)
    resultado_alta = respuestas_alta[0][1]
    assert resultado_alta.creado is True, (
        f"la precondicion exige que el alta deje su solicitud encolada; creado={resultado_alta.creado} "
        f"con motivo {resultado_alta.motivo!r}"
    )

    alta_encolada = AvisoCorreoEntity.objects.get(notification_key=clave_esperada)
    notification_id_original = alta_encolada.notification_id

    # --- Las transiciones posteriores publican por el MISMO canal ------------------
    transiciones = (
        ("ABIERTA->EN_CURSO", 9001),
        ("EN_CURSO->RESUELTA", 9002),
        ("RESUELTA->CERRADA", 9003),
    )
    for etiqueta, history_entry_id in transiciones:
        respuestas = aviso_solicitado.send(
            sender=None,
            notification_type=TIPO_CAMBIO_ESTADO,
            incident_id=incidencia.incident_id,
            history_entry_id=history_entry_id,
        )
        assert len(respuestas) == 1, (
            f"el evento de la transicion {etiqueta} debe entregarse a UN unico receptor; respondieron {len(respuestas)}"
        )

        resultado = respuestas[0][1]
        assert resultado.ignorado is True, (
            f"el consumidor de alta debe IGNORAR el evento {TIPO_CAMBIO_ESTADO} de la transicion {etiqueta}; "
            f"devolvio ignorado={resultado.ignorado}"
        )
        assert resultado.creado is False, (
            f"la transicion {etiqueta} no puede crear ninguna solicitud de alta; creado={resultado.creado}"
        )
        assert resultado.notification_key is None, (
            f"un evento ignorado no lleva clave de aviso asociada; en {etiqueta} llego {resultado.notification_key!r}"
        )
        assert resultado.motivo, (
            f"el consumidor debe explicar POR QUE ignora el evento de la transicion {etiqueta}; "
            f"motivo={resultado.motivo!r}"
        )

    # --- Oraculo: lo que quedo en Oracle tras las tres transiciones ----------------
    altas = AvisoCorreoEntity.objects.filter(
        incident_id=incidencia.incident_id,
        notification_type=TIPO_ALTA,
    ).count()
    assert altas == 1, (
        "tras las tres transiciones de estado debe seguir habiendo EXACTAMENTE una solicitud "
        f"{TIPO_ALTA} en aviso_correo para esa incidencia (AC-AVI-02); en Oracle hay {altas}"
    )

    alta_releida = AvisoCorreoEntity.objects.get(
        incident_id=incidencia.incident_id,
        notification_type=TIPO_ALTA,
    )
    assert alta_releida.notification_key == clave_esperada, (
        f"la unica solicitud de alta debe conservar la clave {clave_esperada!r}; en Oracle vale "
        f"{alta_releida.notification_key!r}"
    )
    assert alta_releida.notification_id == notification_id_original, (
        "los cambios de estado no pueden reemplazar la solicitud de alta: se encolo con "
        f"notification_id={notification_id_original} y ahora en Oracle vale {alta_releida.notification_id}"
    )

    cambios_de_estado = AvisoCorreoEntity.objects.filter(
        incident_id=incidencia.incident_id,
        notification_type=TIPO_CAMBIO_ESTADO,
    ).count()
    assert cambios_de_estado == 0, (
        f"el consumidor del alta no encola nada por los eventos {TIPO_CAMBIO_ESTADO} (de eso se ocupa "
        f"encolar_aviso_cambio_estado con su clave por asiento de historial); en Oracle hay "
        f"{cambios_de_estado} filas"
    )
