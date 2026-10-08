"""
Puerto de ENTREGA del motor de avisos y su implementacion real por SMTP (ARC-115, REQ-134, REQ-141).

QUE HAY AQUI Y POR QUE ESTA SEPARADO EN DOS PIEZAS
==================================================
El despachador no sabe enviar correo: sabe pedir que se entregue un `MensajeCorreo` y leer un
`ResultadoEntrega`. Esa frontera es el `Protocol` `TransporteCorreo`. Detras hay UNA sola
implementacion de producto, `TransporteSmtp`, que habla SMTP de verdad con `smtplib` de la
biblioteca estandar (STARTTLS o SSL implicito segun la configuracion activa de `configuracion_smtp`).

La separacion no es decorativa: permite que las pruebas sustituyan el transporte por un doble SIN
que el codigo de producto contenga ninguna simulacion. En este modulo NO existe ningun modo
"simulado": no hay un `logger.info("se enviaria...")` ni un retorno fijo. Si no hay configuracion
SMTP activa, la fabrica devuelve `None` y el despachador lo traduce a `CONFIG_ERROR`; nunca a un
envio fingido.

CLASIFICACION DEL RESULTADO
===========================
El contrato con el despachador es que `enviar()` NO LANZA. Una excepcion que subiera rompería el
bucle del despachador y pararia la cola entera, asi que todo camino de error acaba en un
`ResultadoEntrega` con uno de los codigos del catalogo cerrado `RESULTADOS_INTENTO`:

- respuesta 4xx del servidor, timeout, conexion rehusada o error de red -> `TRANSIENT_ERROR`
  (reintentable con backoff; REQ-134 / AC-SMTP-04).
- respuesta 5xx (por ejemplo 550, buzon inexistente) -> `PERMANENT_ERROR` (descartado sin reintento;
  REQ-141).
- fallo de autenticacion -> `CONFIG_ERROR`: no es culpa del mensaje y reintentar no lo arregla,
  pero tampoco es un rechazo del buzon.

SECRETOS (REQ-076 / AC-SMTP-07)
===============================
La contrasena SMTP no puede aparecer en la API, en la interfaz ni en los LOGS. Por eso
`ParametrosSmtp` redefine `__repr__`/`__str__` para enmascararla (un `repr()` de la dataclass en una
traza es exactamente como se filtra una credencial) y todo `error_message` pasa por `_sanear()`,
que borra del texto de la excepcion la contrasena y el usuario antes de recortarlo a 500 caracteres.
Tampoco se escriben en el log los destinatarios ni el cuerpo (REQ-063 / REQ-079): solo contadores y
codigos de respuesta.

DE DONDE SALE LA CONTRASENA
===========================
La tabla `configuracion_smtp` NO almacena credenciales: el DDL aplicado (`04-cat-soporte.xml`) tiene
`secreto_ref VARCHAR2(512)`, una REFERENCIA EXTERNA al secreto en el gestor de secretos, y el
changelog incluye un oraculo que verifica que no existe ninguna columna de contrasena. La
resolucion de esa referencia se hace contra el ENTORNO del proceso (`os.environ`), que es el
mecanismo de inyeccion de secretos del despliegue en contenedor: se busca una variable con el
nombre literal de `secreto_ref` y, si no existe, la variable de respaldo `AVISOS_SMTP_PASSWORD`.
Asi la credencial nunca viaja por la base de datos ni queda incrustada en el codigo.
"""

import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any, Protocol, runtime_checkable

from apps.avisos.motor.configuracion import ConfiguracionMotorAvisos
from apps.avisos.motor.errores import ErrorMotorAvisos
from apps.avisos.motor.estados import RESULTADOS_INTENTO

logger = logging.getLogger(__name__)


def _codigo_del_catalogo(codigo: str) -> str:
    """
    Devuelve el codigo de resultado comprobando que pertenece a `RESULTADOS_INTENTO`.

    El catalogo vive en `apps.avisos.motor.estados` y lo defiende ademas la CHECK
    `ck_aviso_intento_result`. Esta comprobacion se hace al importar el modulo para que un codigo
    inventado salte en el arranque y no al persistir el intento, ya dentro de la transaccion.
    """

    if codigo not in RESULTADOS_INTENTO:
        catalogo = ", ".join(sorted(RESULTADOS_INTENTO))
        raise ErrorMotorAvisos(f"El resultado de intento «{codigo}» no pertenece al catalogo cerrado: {catalogo}.")
    return codigo


#: Codigos de resultado que usa este transporte, tomados del catalogo cerrado del motor.
RESULTADO_TRANSITORIO: str = _codigo_del_catalogo("TRANSIENT_ERROR")
RESULTADO_PERMANENTE: str = _codigo_del_catalogo("PERMANENT_ERROR")
RESULTADO_CONFIGURACION: str = _codigo_del_catalogo("CONFIG_ERROR")

#: Codigo SMTP de aceptacion que se persiste cuando el servidor acepta el mensaje.
CODIGO_SMTP_ACEPTADO: str = "250"

#: Ancho de `aviso_correo_intento.smtp_response_code` (VARCHAR2(3 CHAR) con CHECK de 3 digitos).
LONGITUD_CODIGO_SMTP: int = 3

#: Ancho de `aviso_correo_intento.error_message`; todo texto de error se recorta aqui.
LONGITUD_MAXIMA_ERROR: int = 500

#: Puerto de SSL implicito: se conecta con `SMTP_SSL` y NO se hace STARTTLS.
PUERTO_SSL_IMPLICITO: int = 465

#: Testigo con el que se sustituye cualquier credencial en trazas y mensajes de error.
MASCARA_SECRETO: str = "***"

#: Valor de los indicadores S/N del catalogo (`use_tls`, `is_active`) que significa "si".
INDICADOR_SI: str = "Y"

#: Variable de entorno de respaldo cuando `configuracion_smtp.secreto_ref` esta vacio o no resuelve.
VARIABLE_PASSWORD_RESPALDO: str = "AVISOS_SMTP_PASSWORD"

#: Dominio con el que se genera el `Message-ID` si el remitente configurado no tiene uno legible.
DOMINIO_MESSAGE_ID_POR_DEFECTO: str = "avisos.local"


@dataclass(frozen=True, slots=True)
class MensajeCorreo:
    """
    Correo ya compuesto y listo para entregar: el transporte no decide ni redacta nada.

    `destinatarios` es una tupla para que el mensaje sea inmutable y comparable; el orden es el que
    fijo la composicion.
    """

    destinatarios: tuple[str, ...]
    asunto: str
    cuerpo_texto: str
    cuerpo_html: str | None = None


@dataclass(frozen=True, slots=True)
class ResultadoEntrega:
    """
    Desenlace de un intento de entrega, en el vocabulario de `aviso_correo_intento`.

    Los campos reproducen las columnas que persiste el despachador: `smtp_response_code` tiene que
    ser EXACTAMENTE tres digitos o nulo (CHECK `ck_aviso_intento_smtp_code`) y `error_message` cabe
    en 500 caracteres y jamas contiene credenciales (REQ-076).
    """

    entregado: bool
    message_id: str | None = None
    smtp_response_code: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def aceptado(cls, message_id: str | None, smtp_response_code: str = CODIGO_SMTP_ACEPTADO) -> "ResultadoEntrega":
        """El servidor acepto el mensaje: el aviso pasa a ENVIADO con el `Message-ID` que viajo."""

        return cls(
            entregado=True,
            message_id=message_id,
            smtp_response_code=_codigo_de_respuesta(smtp_response_code),
        )

    @classmethod
    def transitorio(cls, error_message: str, *, smtp_response_code: str | None = None) -> "ResultadoEntrega":
        """Fallo reintentable (4xx, timeout, conexion rehusada): el aviso vuelve a la cola con backoff."""

        return cls(
            entregado=False,
            smtp_response_code=_codigo_de_respuesta(smtp_response_code),
            error_code=RESULTADO_TRANSITORIO,
            error_message=_recortar(error_message),
        )

    @classmethod
    def permanente(cls, error_message: str, *, smtp_response_code: str | None = None) -> "ResultadoEntrega":
        """Rechazo definitivo del servidor (5xx, p. ej. 550 buzon inexistente): sin reintento."""

        return cls(
            entregado=False,
            smtp_response_code=_codigo_de_respuesta(smtp_response_code),
            error_code=RESULTADO_PERMANENTE,
            error_message=_recortar(error_message),
        )

    @classmethod
    def de_configuracion(cls, error_message: str, *, smtp_response_code: str | None = None) -> "ResultadoEntrega":
        """Falta configuracion SMTP activa o las credenciales no son validas: reintentar no lo arregla."""

        return cls(
            entregado=False,
            smtp_response_code=_codigo_de_respuesta(smtp_response_code),
            error_code=RESULTADO_CONFIGURACION,
            error_message=_recortar(error_message),
        )


@runtime_checkable
class TransporteCorreo(Protocol):
    """
    Puerto de entrega del motor: lo unico que el despachador conoce del mundo exterior.

    Implementarlo obliga a no lanzar: cualquier fallo se devuelve como `ResultadoEntrega`.
    """

    def enviar(self, mensaje: MensajeCorreo) -> ResultadoEntrega:
        """Entrega el mensaje y devuelve el desenlace; nunca propaga excepciones."""

        ...


@dataclass(frozen=True, slots=True, repr=False)
class ParametrosSmtp:
    """
    Parametros efectivos de conexion, resueltos desde la fila activa de `configuracion_smtp`.

    `password` se guarda en claro en memoria porque `smtplib.login()` la necesita asi, pero el
    `repr()` y el `str()` de esta clase la enmascaran: es la unica forma de que un volcado de
    objetos en una traza de error no publique la credencial (REQ-076 / AC-SMTP-07).
    """

    host: str
    puerto: int
    usar_tls: bool
    usuario: str | None
    password: str | None
    remitente: str
    nombre_remitente: str
    timeout_segundos: int

    def __repr__(self) -> str:
        """Representacion con la contrasena enmascarada: nunca imprime la credencial."""

        return (
            f"ParametrosSmtp(host={self.host!r}, puerto={self.puerto!r}, usar_tls={self.usar_tls!r}, "
            f"usuario={self.usuario!r}, password={MASCARA_SECRETO!r}, remitente={self.remitente!r}, "
            f"nombre_remitente={self.nombre_remitente!r}, timeout_segundos={self.timeout_segundos!r})"
        )

    def __str__(self) -> str:
        """Resumen legible del destino de la conexion, tambien sin credencial."""

        return f"SMTP {self.host}:{self.puerto} (tls={self.usar_tls}, usuario={self.usuario or '-'}, password={MASCARA_SECRETO})"

    @property
    def ssl_implicito(self) -> bool:
        """Indica si la conexion se abre ya cifrada (`SMTP_SSL`) en lugar de negociar STARTTLS."""

        return self.puerto == PUERTO_SSL_IMPLICITO

    @classmethod
    def desde_entidad(cls, entidad: Any, *, timeout_segundos: int) -> "ParametrosSmtp":
        """
        Traduce una fila de `configuracion_smtp` a parametros de conexion.

        Los nombres de los atributos son los FISICOS de la tabla (`smtp_host`, `smtp_port`,
        `use_tls`, `smtp_username`, `sender_address`, `sender_display_name`). La contrasena no
        esta en la tabla: se resuelve a partir de `secreto_ref` (ver la cabecera del modulo).

        Args:
            entidad: fila de `ConfiguracionSmtpEntity` con la configuracion activa.
            timeout_segundos: tope de espera de la conexion, de `ConfiguracionMotorAvisos`.

        Returns:
            ParametrosSmtp: parametros inmutables listos para `TransporteSmtp`.
        """

        usuario = (getattr(entidad, "smtp_username", None) or "").strip() or None
        return cls(
            host=str(entidad.smtp_host).strip(),
            puerto=int(entidad.smtp_port),
            usar_tls=str(getattr(entidad, "use_tls", "") or "").strip().upper() == INDICADOR_SI,
            usuario=usuario,
            password=_password_desde_referencia(getattr(entidad, "secreto_ref", None)),
            remitente=str(entidad.sender_address).strip(),
            nombre_remitente=str(getattr(entidad, "sender_display_name", "") or "").strip(),
            timeout_segundos=timeout_segundos,
        )

    @classmethod
    def activa(cls, *, timeout_segundos: int) -> "ParametrosSmtp | None":
        """
        Lee la unica fila con `is_active='Y'` de `configuracion_smtp`.

        El import del modelo es PEREZOSO a proposito: este modulo lo importan procesos que aun no
        han ejecutado `django.setup()`, y un import de modelos a nivel de modulo los romperia.

        Returns:
            ParametrosSmtp | None: los parametros activos, o `None` si no hay configuracion activa.
        """

        from apps.core.models.catalogos import ConfiguracionSmtpEntity

        entidad = ConfiguracionSmtpEntity.objects.filter(is_active=INDICADOR_SI).order_by("config_id").first()
        if entidad is None:
            return None
        return cls.desde_entidad(entidad, timeout_segundos=timeout_segundos)


class TransporteSmtp:
    """
    Implementacion real del puerto de entrega contra un servidor SMTP (REQ-134, REQ-141).

    Abre una conexion por mensaje y la cierra siempre (`with`), con el timeout acotado que fija
    `ConfiguracionMotorAvisos.smtp_timeout_segundos`: una conexion colgada bloquearia el hilo del
    despachador y con el la cola entera (REQ-141, validacion 4).
    """

    def __init__(self, parametros: ParametrosSmtp) -> None:
        self.parametros = parametros

    def __repr__(self) -> str:
        """Delegado en `ParametrosSmtp`, que ya enmascara la credencial."""

        return f"TransporteSmtp({self.parametros!r})"

    def enviar(self, mensaje: MensajeCorreo) -> ResultadoEntrega:
        """
        Entrega el mensaje por SMTP y clasifica el desenlace; NUNCA propaga una excepcion.

        El `Message-ID` se genera y se fija en la cabecera ANTES de enviar, de modo que el
        identificador que se persiste es exactamente el que viajo: es la base de la idempotencia
        de la entrega (AC-AVI-06) y lo que permite rastrear el correo en el servidor de salida.

        Args:
            mensaje: correo ya compuesto (destinatarios, asunto y cuerpos).

        Returns:
            ResultadoEntrega: aceptado, transitorio, permanente o de configuracion.
        """

        message_id = make_msgid(domain=self._dominio_message_id())
        try:
            correo = self._componer(mensaje, message_id)
        except Exception as error:  # noqa: BLE001 - la composicion tampoco puede tumbar el despachador
            return ResultadoEntrega.transitorio(self._texto(error))

        try:
            rechazos = self._entregar(correo)
        except smtplib.SMTPAuthenticationError as error:
            # No es un rechazo del buzon ni un fallo de red: la credencial o el usuario no valen.
            return ResultadoEntrega.de_configuracion(self._texto(error), smtp_response_code=_codigo_de_respuesta(error.smtp_code))
        except smtplib.SMTPConnectError as error:
            return ResultadoEntrega.transitorio(self._texto(error), smtp_response_code=_codigo_de_respuesta(error.smtp_code))
        except smtplib.SMTPRecipientsRefused as error:
            # Todos los destinatarios rechazados. El texto de la excepcion vuelca el diccionario
            # `recipients` CON LAS DIRECCIONES, asi que NO se usa: se compone un mensaje con el
            # recuento y el codigo, que es todo el diagnostico que hace falta y no republica ningun
            # correo corporativo en el log ni en `error_message` (REQ-063 / REQ-079).
            codigo = _primer_codigo_de_rechazos(error.recipients)
            detalle = f"SMTPRecipientsRefused: {len(error.recipients or {})} destinatario(s) rechazado(s), codigo {codigo or 'sin codigo'}"
            return self._por_codigo(codigo, _sanear(detalle, self.parametros))
        except smtplib.SMTPResponseException as error:
            # Cubre SMTPSenderRefused, SMTPDataError y cualquier otra respuesta con codigo.
            return self._por_codigo(_codigo_de_respuesta(error.smtp_code), self._texto(error))
        except (TimeoutError, ConnectionRefusedError, OSError) as error:
            # `socket.timeout` es alias de `TimeoutError` y ambos, como `SMTPException`, derivan de
            # OSError: timeout, conexion rehusada o corte de red son siempre reintentables (REQ-134).
            return ResultadoEntrega.transitorio(self._texto(error))
        except Exception as error:  # noqa: BLE001 - contrato del puerto: enviar() no lanza
            return ResultadoEntrega.transitorio(self._texto(error))

        if rechazos:
            # Entrega parcial: el servidor acepto el mensaje para al menos un destinatario, asi que
            # el correo YA viajo y reintentarlo duplicaria el aviso. Se registra como aceptado y el
            # rechazo queda en el log con su codigo y su recuento, sin direcciones.
            logger.warning(
                "Entrega SMTP parcial: %s destinatario(s) rechazado(s), codigo %s",
                len(rechazos),
                _primer_codigo_de_rechazos(rechazos) or "sin codigo",
            )
        return ResultadoEntrega.aceptado(message_id)

    def _componer(self, mensaje: MensajeCorreo, message_id: str) -> EmailMessage:
        """Construye el `EmailMessage` con cabeceras, cuerpo de texto y alternativa HTML si la hay."""

        correo = EmailMessage()
        correo["Message-ID"] = message_id
        correo["Subject"] = mensaje.asunto
        correo["From"] = formataddr((self.parametros.nombre_remitente, self.parametros.remitente))
        correo["To"] = ", ".join(mensaje.destinatarios)
        correo.set_content(mensaje.cuerpo_texto)
        if mensaje.cuerpo_html:
            correo.add_alternative(mensaje.cuerpo_html, subtype="html")
        return correo

    def _entregar(self, correo: EmailMessage) -> dict[str, tuple[int, bytes]]:
        """
        Abre la conexion, autentica si procede y entrega el mensaje.

        Returns:
            dict: destinatarios rechazados que devuelve `send_message` (vacio si los acepto todos).
        """

        parametros = self.parametros
        if parametros.ssl_implicito:
            cliente: smtplib.SMTP = smtplib.SMTP_SSL(parametros.host, parametros.puerto, timeout=parametros.timeout_segundos)
        else:
            cliente = smtplib.SMTP(parametros.host, parametros.puerto, timeout=parametros.timeout_segundos)
        with cliente as sesion:
            if parametros.usar_tls and not parametros.ssl_implicito:
                sesion.starttls()
                sesion.ehlo()
            if parametros.usuario:
                sesion.login(parametros.usuario, parametros.password or "")
            return sesion.send_message(correo)

    def _por_codigo(self, codigo: str | None, texto: str) -> ResultadoEntrega:
        """
        Clasifica una respuesta con codigo: 4xx reintentable, 5xx definitivo (REQ-141).

        Una respuesta sin codigo reconocible se trata como transitoria: ante la duda se reintenta,
        que es el lado seguro cuando el aviso todavia no ha llegado a nadie.
        """

        if codigo and codigo.startswith("5"):
            return ResultadoEntrega.permanente(texto, smtp_response_code=codigo)
        return ResultadoEntrega.transitorio(texto, smtp_response_code=codigo)

    def _texto(self, error: BaseException) -> str:
        """Texto de la excepcion saneado de credenciales y con el tipo delante para poder diagnosticar."""

        return _sanear(f"{type(error).__name__}: {error}", self.parametros)

    def _dominio_message_id(self) -> str:
        """Dominio del `Message-ID`: el del remitente configurado, para no publicar el host interno."""

        _, _, dominio = self.parametros.remitente.partition("@")
        return dominio.strip() or DOMINIO_MESSAGE_ID_POR_DEFECTO


def transporte_por_defecto(config: ConfiguracionMotorAvisos) -> TransporteCorreo | None:
    """
    Fabrica el transporte de produccion a partir de la configuracion SMTP activa.

    Args:
        config: parametros de operacion del motor (de ahi sale el timeout acotado).

    Returns:
        TransporteCorreo | None: el transporte SMTP, o `None` si no hay fila activa en
        `configuracion_smtp`. Devolver `None` en vez de lanzar es deliberado: el despachador lo
        traduce a un intento con resultado `CONFIG_ERROR` y la cola sigue viva.
    """

    parametros = ParametrosSmtp.activa(timeout_segundos=config.smtp_timeout_segundos)
    if parametros is None:
        logger.warning("No hay configuracion SMTP activa (configuracion_smtp.is_active='Y'): el motor no puede entregar avisos")
        return None
    return TransporteSmtp(parametros)


def _password_desde_referencia(secreto_ref: str | None) -> str | None:
    """
    Resuelve la contrasena SMTP a partir de la REFERENCIA al secreto de `configuracion_smtp`.

    La tabla no guarda credenciales (ver la cabecera del modulo): la referencia se busca como
    nombre de variable de entorno y, si no esta, se cae a `AVISOS_SMTP_PASSWORD`. Devuelve `None`
    cuando no hay nada que resolver, que es el caso del servidor de relay sin autenticacion.
    """

    referencia = (secreto_ref or "").strip()
    if referencia:
        valor = os.environ.get(referencia)
        if valor:
            return valor
    return os.environ.get(VARIABLE_PASSWORD_RESPALDO) or None


def _codigo_de_respuesta(valor: Any) -> str | None:
    """
    Normaliza un codigo de respuesta SMTP a EXACTAMENTE tres digitos, o `None`.

    La columna `aviso_correo_intento.smtp_response_code` es VARCHAR2(3 CHAR) con un CHECK de tres
    digitos: cualquier otra cosa (un -1 de `smtplib`, un texto, unos bytes) se descarta aqui y no
    llega a la base de datos a provocar ORA-02290.
    """

    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, bytes | bytearray):
        texto = bytes(valor).decode("ascii", errors="ignore")
    else:
        texto = str(valor)
    texto = texto.strip()
    if len(texto) == LONGITUD_CODIGO_SMTP and texto.isdigit():
        return texto
    return None


def _primer_codigo_de_rechazos(rechazos: dict[str, tuple[int, bytes]] | None) -> str | None:
    """Toma el codigo del primer destinatario rechazado; se ignora la direccion a proposito (REQ-079)."""

    for respuesta in (rechazos or {}).values():
        if isinstance(respuesta, tuple | list) and respuesta:
            codigo = _codigo_de_respuesta(respuesta[0])
            if codigo:
                return codigo
    return None


def _sanear(texto: str, parametros: ParametrosSmtp) -> str:
    """
    Borra del texto cualquier aparicion de la contrasena o del usuario SMTP y lo recorta a 500.

    Algunos servidores devuelven en el mensaje de error la linea de autenticacion que recibieron;
    ese texto acaba en `aviso_correo_intento.error_message` y en el log. REQ-076 / AC-SMTP-07
    exigen 0 ocurrencias de la credencial, asi que se enmascara antes de cualquier uso.
    """

    limpio = texto
    for secreto in (parametros.password, parametros.usuario):
        if secreto:
            limpio = limpio.replace(secreto, MASCARA_SECRETO)
    return _recortar(limpio)


def _recortar(texto: str) -> str:
    """Ajusta el texto al ancho de `aviso_correo_intento.error_message` (500 caracteres)."""

    return (texto or "").strip()[:LONGITUD_MAXIMA_ERROR]


__all__ = [
    "CODIGO_SMTP_ACEPTADO",
    "DOMINIO_MESSAGE_ID_POR_DEFECTO",
    "INDICADOR_SI",
    "LONGITUD_CODIGO_SMTP",
    "LONGITUD_MAXIMA_ERROR",
    "MASCARA_SECRETO",
    "PUERTO_SSL_IMPLICITO",
    "RESULTADO_CONFIGURACION",
    "RESULTADO_PERMANENTE",
    "RESULTADO_TRANSITORIO",
    "VARIABLE_PASSWORD_RESPALDO",
    "MensajeCorreo",
    "ParametrosSmtp",
    "ResultadoEntrega",
    "TransporteCorreo",
    "TransporteSmtp",
    "transporte_por_defecto",
]
