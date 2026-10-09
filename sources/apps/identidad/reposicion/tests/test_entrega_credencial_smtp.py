"""
Entrega REAL por SMTP del aviso de restablecimiento de credencial (AC-RST-03, REQ-073).

POR QUE ESTE FICHERO EXISTE: EL ORACULO DEL DoD
================================================
El DoD de la tarea es literal sobre que cuenta como evidencia de AC-RST-03: hace falta «un test con
servidor SMTP de prueba (aiosmtpd) que captura un mensaje dirigido al `corporate_email` del usuario»
y otro «de SMTP que rechaza la entrega», y advierte que **un log de "correo enviado" NO cuenta como
evidencia**. La advertencia no es retorica: un doble del transporte que devuelve `entregado=True`, o
un `logger.info("correo enviado")` observado con `caplog`, pasan en verde sin que ni un solo byte
haya salido del proceso, de modo que verificarian la llamada y no la ENTREGA. Si el correo nunca
llegara al buzon del usuario, esa prueba seguiria en verde y el usuario seguiria sin poder entrar.

Por eso aqui se levanta un servidor SMTP DE VERDAD, en proceso y escuchando en `127.0.0.1`
(`aiosmtpd.controller.Controller`), y se entrega contra el con el transporte de PRODUCCION
(`TransporteSmtp`, que habla `smtplib`). El oraculo no es lo que el codigo dice haber hecho, sino lo
que el servidor RECIBE: el sobre capturado, sus `rcpt_tos` y su contenido. El correo se redacta
ademas con el componedor real (`componer_contenido`) y con la credencial que emite el generador real
(`generar_credencial_temporal`), de modo que lo que viaja por el socket es exactamente el correo que
recibiria el usuario en produccion y no una maqueta escrita en el test.

POR QUE NO NECESITA ORACLE NI DOCKER
=====================================
Nada de lo que aqui se afirma se persiste: el transporte no toca el ORM, el componedor redacta en
memoria -el cuerpo lleva la credencial en claro y REQ-073 regla 4 prohibe persistirlo- y el
generador es dominio puro. Por eso el fichero NO lleva `django_db`, ni fixture `db`, ni el marcador
`integration`: se ejecuta tal cual, sin base de datos y sin contenedores. La evidencia de que el
intento y la transicion QUEDAN ESCRITOS en `aviso_correo` y `aviso_correo_intento` es otra cosa y
corresponde a las pruebas de integracion contra el motor real, no a este fichero.

EL TERCER TEST NO USA SMTP, Y ESTA AQUI POR SIMETRIA CON EL SECRETO
====================================================================
Los dos primeros acreditan que la credencial SI llega al buzon del usuario. El tercero acredita el
reverso exacto de la misma regla (AC-RST-01, REQ-073 regla 4): que NO sale por la respuesta de la
API, y que no puede salir, porque la estructura que se serializa no admite el campo.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Iterator
from datetime import datetime

import pytest
from aiosmtpd.controller import Controller

from apps.avisos.credenciales.entrega import DatosCredencial, ResultadoEntregaCredencial, componer_contenido
from apps.avisos.motor.outbox import TIPO_RESTABLECIMIENTO
from apps.avisos.motor.transporte import MensajeCorreo, ParametrosSmtp, TransporteSmtp
from apps.identidad.reposicion.generador import caducidad_credencial_temporal, generar_credencial_temporal
from apps.identidad.reposicion.serializers import PasswordResetResultSerializer
from apps.identidad.reposicion.servicio import ResultadoRestablecimiento

# `aiosmtpd` escribe en el logger `mail.log` una linea por comando SMTP (EHLO, MAIL, RCPT, DATA...).
# Es ruido de protocolo que ahoga la salida de pytest y no aporta nada al oraculo, que son los
# sobres capturados por el handler.
logging.getLogger("mail.log").setLevel(logging.WARNING)

#: Interfaz de escucha del servidor de prueba: SOLO loopback. El servidor acepta cualquier correo
#: sin autenticar, asi que no puede quedar expuesto fuera de la maquina que ejecuta la suite.
HOST_PRUEBA: str = "127.0.0.1"

#: Buzon corporativo del usuario DESTINO del restablecimiento. Es el oraculo de AC-RST-03: el
#: mensaje capturado tiene que ir dirigido a esta direccion y no a la del administrador que actua.
CORREO_DESTINO: str = "pepe@mapfre.es"

#: Identificador y nombre del usuario destino; solo sirven para redactar el correo.
USUARIO_DESTINO_ID: int = 4071
NOMBRE_DESTINO: str = "Pepe Pérez Gómez"

#: Discriminante de la EMISION. Al reemitir se genera uno nuevo, de modo que dos restablecimientos
#: consecutivos son dos solicitudes distintas y no un duplicado.
DISCRIMINANTE_EMISION: str = "reset-20261009-120000"

#: Instante de referencia del restablecimiento, NAIVE en UTC igual que `utc_now()` y que las
#: columnas TIMESTAMP del esquema. Mezclar naive y aware esta prohibido en el proyecto.
INSTANTE_RESTABLECIMIENTO: datetime = datetime(2026, 10, 9, 12, 0, 0)

#: Respuesta de ACEPTACION del servidor de prueba.
RESPUESTA_ACEPTADA: str = "250 OK"

#: Respuesta de RECHAZO PERMANENTE (5xx): buzon inexistente o indisponible. Es el caso que REQ-141
#: clasifica como `PERMANENT_ERROR`, sin reintento.
RESPUESTA_RECHAZADA: str = "550 Requested action not taken: mailbox unavailable"

#: Remitente configurado del motor de avisos. El dominio es ademas el del `Message-ID` que genera
#: el transporte, asi que aparece en el identificador que se afirma mas abajo.
REMITENTE: str = "avisos@mind.local"
NOMBRE_REMITENTE: str = "Gestión de Incidencias"

#: Tope de espera de la conexion SMTP. Holgado para un servidor en el mismo proceso, pero acotado:
#: un servidor que aceptara el TCP y no respondiera colgaria la suite entera.
TIMEOUT_SEGUNDOS: int = 10

#: Campos que `ResultadoRestablecimiento` puede transportar. La tupla es el oraculo ESTRUCTURAL de
#: AC-RST-01: lo que no esta aqui no puede llegar al serializador ni, por tanto, a la respuesta.
CAMPOS_RESULTADO_RESTABLECIMIENTO: tuple[str, ...] = (
    "user_id",
    "must_change_password",
    "password_expires_at",
    "reset_at",
    "reset_by_user_id",
    "sesiones_revocadas",
    "notification_id",
)

#: Nombres fisicos del material de credencial del esquema T.5 y alias habituales del secreto. Ni las
#: claves ni los valores del JSON de respuesta pueden contener ninguno (AC-RST-01, AC-RST-04).
SUBCADENAS_PROHIBIDAS: tuple[str, ...] = (
    "password_hash",
    "password_salt",
    "password_algorithm",
    "temporaryPassword",
    "temporary_password",
)


def _puerto_libre() -> int:
    """
    Reserva un puerto efimero libre y lo devuelve ya cerrado, para pasarselo al `Controller`.

    NO se puede usar `Controller(..., port=0)` y dejar que el sistema elija: la sonda de arranque
    del propio `Controller` se conecta al puerto que le pasaron -literalmente el 0- para comprobar
    que el servidor esta vivo, y falla con `ConnectionRefusedError`. Hay que darle un puerto
    concreto, asi que se reserva uno aqui y se libera en el acto.
    """

    s = socket.socket()
    s.bind((HOST_PRUEBA, 0))
    puerto = s.getsockname()[1]
    s.close()
    return puerto


class _BuzonSmtp:
    """
    Handler de `aiosmtpd` que ACUMULA los sobres recibidos y responde siempre lo mismo.

    Es el oraculo de estas pruebas: no cuenta llamadas al transporte, guarda lo que de verdad
    cruzo el socket. Cada `envelope` trae el remitente (`mail_from`), los destinatarios que el
    cliente declaro en los `RCPT TO` (`rcpt_tos`) y el mensaje completo (`content`).

    `respuesta` es lo que se contesta al `DATA`: `250 OK` acepta y `550 ...` rechaza de forma
    permanente. El rechazo se registra IGUALMENTE en `mensajes`, porque la prueba del caso de
    fallo necesita afirmar que el intento de entrega llego a hacerse de verdad.
    """

    def __init__(self, respuesta: str) -> None:
        self.respuesta = respuesta
        self.mensajes: list = []
        self.host: str = HOST_PRUEBA
        self.puerto: int = 0

    async def handle_DATA(self, server, session, envelope) -> str:  # noqa: N802 - nombre impuesto por aiosmtpd
        """Registra el sobre recibido y devuelve la respuesta configurada (aceptacion o rechazo)."""

        self.mensajes.append(envelope)
        return self.respuesta

    def unico_mensaje(self):
        """
        Devuelve el unico sobre capturado, fallando si no hay exactamente uno.

        Es un METODO y no una `property` a proposito: `aiosmtpd.smtp.SMTP.__init__` recorre el
        handler con `inspect.getmembers` para descubrir mecanismos `auth_*`, y eso EVALUA todas sus
        propiedades. Una `property` con un `assert` dentro reventaria al arrancar la conexion, antes
        incluso de que llegue ningun mensaje.
        """

        assert len(self.mensajes) == 1, f"Se esperaba exactamente 1 mensaje capturado y hay {len(self.mensajes)}"
        return self.mensajes[0]

    def texto_recibido(self) -> str:
        """Contenido del unico mensaje capturado, decodificado para poder buscar en el."""

        return self.unico_mensaje().content.decode("utf-8", errors="replace")


def _arrancar(respuesta: str) -> Iterator[_BuzonSmtp]:
    """
    Arranca un servidor SMTP real en loopback con la respuesta dada y GARANTIZA su parada.

    El `try/finally` no es defensivo por costumbre: un `Controller` sin parar deja vivos un hilo y
    un puerto en escucha durante el resto de la sesion de pytest, de modo que un fallo en mitad de
    un test contaminaria todos los siguientes.
    """

    handler = _BuzonSmtp(respuesta)
    handler.puerto = _puerto_libre()
    controller = Controller(handler, hostname=handler.host, port=handler.puerto)
    controller.start()
    try:
        yield handler
    finally:
        controller.stop()


@pytest.fixture
def buzon_smtp() -> Iterator[_BuzonSmtp]:
    """Servidor SMTP de prueba que ACEPTA la entrega (`250 OK`) y captura los sobres recibidos."""

    yield from _arrancar(RESPUESTA_ACEPTADA)


@pytest.fixture
def smtp_que_rechaza() -> Iterator[_BuzonSmtp]:
    """Servidor SMTP de prueba que RECHAZA la entrega con un 5xx permanente, registrando el intento."""

    yield from _arrancar(RESPUESTA_RECHAZADA)


def _parametros(host: str, puerto: int) -> ParametrosSmtp:
    """
    Parametros de conexion contra el servidor de prueba: sin TLS y sin autenticacion.

    Es la configuracion de un relay interno sin credenciales, que es un caso real admitido por
    `ParametrosSmtp` (`usuario=None` hace que el transporte no llame a `login`). Lo que se ejercita
    es la ENTREGA, no la negociacion de TLS.
    """

    return ParametrosSmtp(
        host=host,
        puerto=puerto,
        usar_tls=False,
        usuario=None,
        password=None,
        remitente=REMITENTE,
        nombre_remitente=NOMBRE_REMITENTE,
        timeout_segundos=TIMEOUT_SEGUNDOS,
    )


def _datos_del_restablecimiento(credencial: str) -> DatosCredencial:
    """Contrato de entrada del aviso `PASSWORD_RESET` con la credencial recien emitida."""

    return DatosCredencial(
        notification_type=TIPO_RESTABLECIMIENTO,
        user_id=USUARIO_DESTINO_ID,
        full_name=NOMBRE_DESTINO,
        corporate_email=CORREO_DESTINO,
        credencial_temporal=credencial,
        expires_at=caducidad_credencial_temporal(INSTANTE_RESTABLECIMIENTO),
        discriminante=DISCRIMINANTE_EMISION,
    )


def _emitir_credencial() -> str:
    """
    Emite una credencial temporal con el generador REAL de produccion.

    No se escribe una constante a mano: asi el correo que viaja por el socket lleva exactamente la
    clase de secreto que emite `ServicioReposicionCredencial`, con su alfabeto y su longitud reales.
    """

    return generar_credencial_temporal(corporate_email=CORREO_DESTINO)


def test_AC_RST_03_el_aviso_de_restablecimiento_llega_al_correo_corporativo_del_usuario(buzon_smtp: _BuzonSmtp) -> None:
    """[AC-RST-03] Un servidor SMTP de prueba captura un mensaje dirigido al corporate_email del usuario."""

    credencial = _emitir_credencial()
    contenido = componer_contenido(_datos_del_restablecimiento(credencial))

    transporte = TransporteSmtp(_parametros(buzon_smtp.host, buzon_smtp.puerto))
    resultado = transporte.enviar(
        MensajeCorreo(
            destinatarios=(CORREO_DESTINO,),
            asunto=contenido.subject,
            cuerpo_texto=contenido.body_text,
            cuerpo_html=contenido.body_html,
        )
    )

    assert resultado.entregado is True
    assert resultado.smtp_response_code == "250"

    # ORACULO DEL DoD: el servidor recibio UN mensaje y va dirigido al buzon corporativo del usuario
    # destino. Esto es lo que un log de "correo enviado" no puede acreditar.
    assert len(buzon_smtp.mensajes) == 1
    envelope = buzon_smtp.unico_mensaje()
    assert CORREO_DESTINO in envelope.rcpt_tos

    # Y el correo lleva la credencial: el usuario puede usarla de verdad, no es un correo vacio.
    assert credencial in buzon_smtp.texto_recibido()

    # El `Message-ID` que se persiste es el que viajo (base de la idempotencia, AC-AVI-06).
    assert resultado.message_id
    assert resultado.error_code is None


def test_AC_RST_03_un_smtp_que_rechaza_la_entrega_no_la_da_por_buena(smtp_que_rechaza: _BuzonSmtp) -> None:
    """
    [AC-RST-03] Un SMTP que rechaza la entrega produce un resultado NO entregado; el llamante responde 502
    y la credencial anterior sigue vigente.
    """

    credencial = _emitir_credencial()
    contenido = componer_contenido(_datos_del_restablecimiento(credencial))

    transporte = TransporteSmtp(_parametros(smtp_que_rechaza.host, smtp_que_rechaza.puerto))
    resultado = transporte.enviar(
        MensajeCorreo(
            destinatarios=(CORREO_DESTINO,),
            asunto=contenido.subject,
            cuerpo_texto=contenido.body_text,
            cuerpo_html=contenido.body_html,
        )
    )

    # El intento LLEGO A HACERSE: el servidor recibio el DATA y lo rechazo despues. Sin esto, un
    # fallo de conexion daria el mismo `entregado=False` sin que el correo se hubiera intentado.
    assert len(smtp_que_rechaza.mensajes) == 1

    assert resultado.entregado is False
    assert resultado.error_message

    # Valores OBSERVADOS contra el servidor real: `smtplib` eleva el 550 del DATA como
    # `SMTPDataError` (subclase de `SMTPResponseException`) con `smtp_code=550`, y el transporte lo
    # clasifica por su primer digito como rechazo definitivo.
    assert resultado.smtp_response_code == "550"
    assert resultado.error_code == "PERMANENT_ERROR"
    assert resultado.message_id is None

    # Lazo con el servicio: este desenlace es el que hace que `ServicioReposicionCredencial` aborte
    # con 502 SIN tocar la credencial, de modo que la anterior del usuario sigue vigente.
    no_entregado = ResultadoEntregaCredencial.no_entregado(
        notification_id="00000000-0000-0000-0000-000000000000",
        codigo_error=resultado.error_code,
        mensaje_usuario="No se ha podido enviar el correo con el acceso temporal; reinténtalo",
    )
    assert no_entregado.debe_responder_502 is True


def test_la_credencial_temporal_nunca_viaja_en_el_resultado_del_restablecimiento() -> None:
    """[AC-RST-01, REQ-073 regla 4] La credencial no se devuelve por la API: la estructura de respuesta no admite el campo."""

    credencial = _emitir_credencial()
    resultado = ResultadoRestablecimiento(
        user_id=USUARIO_DESTINO_ID,
        must_change_password=True,
        password_expires_at=caducidad_credencial_temporal(INSTANTE_RESTABLECIMIENTO),
        reset_at=INSTANTE_RESTABLECIMIENTO,
        reset_by_user_id=9001,
        sesiones_revocadas=2,
        notification_id="4a3b2c1d-0000-4000-8000-00000000abcd",
    )

    publicado = PasswordResetResultSerializer(resultado).data

    for clave, valor in publicado.items():
        texto = f"{clave}={valor}"
        assert credencial not in texto
        for prohibida in SUBCADENAS_PROHIBIDAS:
            assert prohibida not in clave
            assert prohibida not in str(valor)

    # Garantia ESTRUCTURAL, no una omision que haya que recordar: la clase esta cerrada a atributos
    # nuevos y ninguno de los que admite es material de credencial, asi que no hay atributo del que
    # un `source=` futuro pudiera tirar el secreto.
    assert ResultadoRestablecimiento.__slots__ == CAMPOS_RESULTADO_RESTABLECIMIENTO
    assert "credencial" not in ResultadoRestablecimiento.__slots__
