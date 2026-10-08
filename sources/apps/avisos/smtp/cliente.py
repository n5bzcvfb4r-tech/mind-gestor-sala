"""
Cliente SMTP de envio UNICO con destinatarios en copia oculta (ARC-014, REQ-133 / AC-SMTP-01).

QUE RESUELVE ESTE MODULO
========================
El aviso de alta se entrega en UN SOLO mensaje dirigido a todos los destinatarios resueltos, pero
ninguno de ellos puede ver las direcciones de los demas: son correos corporativos, es decir, dato
personal (REQ-079). La unica forma estandar de conseguirlo es poner a TODOS los destinatarios en la
cabecera `Bcc` y no escribir ninguna direccion en `To`.

POR QUE `send_message` GARANTIZA LA COPIA OCULTA
===============================================
`smtplib.SMTP.send_message()` calcula los destinatarios de SOBRE (los `RCPT TO`) leyendo las
cabeceras `To`, `Cc` y `Bcc`, y transmite una COPIA LOCAL del mensaje de la que ha borrado `Bcc` y
`Resent-Bcc`. Es decir: todos reciben el correo, y en el texto que viaja no aparece ninguna
direccion. Por eso aqui no se recorre la lista enviando un mensaje por persona (seria N envios y N
intentos que registrar) ni se usa `sendmail()` con una lista aparte. La cabecera `To` se fija al
grupo literal `undisclosed-recipients:;` -un grupo RFC 5322 con CERO miembros-, que aporta un `To`
legible para los clientes de correo sin aportar ningun destinatario de sobre ni exponer direccion
alguna.

QUE NO HAY AQUI: MODO SIMULADO
==============================
No existe ningun camino que finja un envio. Si no hay configuracion SMTP activa y completa,
`cliente_bcc_activo()` devuelve `None` y es el llamante quien registra el intento con `CONFIG_ERROR`
SIN intentar conexion (REQ-133, regla 6). Si el conjunto de destinatarios validos queda vacio,
`enviar()` devuelve `NO_RECIPIENTS` tambien sin abrir conexion (REQ-133, regla 3). Un aviso solo se
marca ENVIADO tras la aceptacion 250 del servidor, que es lo que estampa `ResultadoEntrega.aceptado`.

SECRETOS Y DATO PERSONAL (REQ-076 / AC-SMTP-11, REQ-079)
========================================================
Ni el usuario ni la contrasena SMTP aparecen jamas en el log ni en `error_message`: todo texto de
error pasa por el saneado de `apps.avisos.motor.transporte` y el `__repr__` delega en
`ParametrosSmtp`, que ya enmascara la credencial. Las direcciones descartadas se trazan SIEMPRE
enmascaradas (`j***@dominio`) y de los rechazos del servidor solo se registran recuento y codigo.

REUTILIZACION
=============
`ParametrosSmtp` y `ResultadoEntrega` son los del motor (`apps.avisos.motor.transporte`): este
modulo NO los redefine, para que el desenlace que produce encaje sin traduccion en
`aviso_correo_intento`.
"""

import logging
import re
import smtplib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from apps.avisos.motor.estados import RESULTADOS_INTENTO
from apps.avisos.motor.transporte import (
    DOMINIO_MESSAGE_ID_POR_DEFECTO,
    MASCARA_SECRETO,
    RESULTADO_CONFIGURACION,
    ParametrosSmtp,
    ResultadoEntrega,
    _codigo_de_respuesta,
    _primer_codigo_de_rechazos,
    _recortar,
    _sanear,
)

logger = logging.getLogger(__name__)

#: Resultado cuando no queda ningun destinatario valido: no se abre conexion (REQ-133, regla 3).
RESULTADO_SIN_DESTINATARIOS: str = "NO_RECIPIENTS"

#: Resultado cuando falta configuracion SMTP (host o remitente): no se intenta (REQ-133, regla 6).
RESULTADO_CONFIGURACION_AUSENTE: str = RESULTADO_CONFIGURACION

#: Motivo de descarte de una direccion que no cumple el formato admitido (REQ-133, validacion 3).
MOTIVO_FORMATO_INVALIDO: str = "FORMATO_INVALIDO"

#: Valor de la cabecera `To`: grupo RFC 5322 vacio, sin miembros y sin destinatario de sobre.
DESTINATARIOS_NO_REVELADOS: str = "undisclosed-recipients:;"

#: Longitud maxima de una direccion de correo (RFC 5321: 254 caracteres de ruta inversa/directa).
LONGITUD_MAXIMA_DIRECCION: int = 254

#: Formato admitido: un unico `@`, parte local no vacia, dominio con al menos un punto y sin espacios.
PATRON_DIRECCION: re.Pattern[str] = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")

#: Texto de error cuando no queda ningun destinatario al que entregar.
TEXTO_SIN_DESTINATARIOS: str = "No queda ningun destinatario valido tras la validacion de direcciones"

if RESULTADO_SIN_DESTINATARIOS not in RESULTADOS_INTENTO:  # pragma: no cover - defensa de catalogo
    raise RuntimeError(f"El resultado «{RESULTADO_SIN_DESTINATARIOS}» no pertenece al catalogo cerrado RESULTADOS_INTENTO.")


@dataclass(frozen=True, slots=True)
class DireccionDescartada:
    """
    Direccion que no entra en el envio, con el motivo por el que se descarto.

    La direccion se guarda tal cual llego porque el llamante puede necesitar correlacionarla con el
    destinatario de origen, pero para TRAZAR se usa siempre `enmascarada`: un correo corporativo es
    dato personal y no puede acabar en claro en un log (REQ-079).
    """

    direccion: str
    motivo: str

    @property
    def enmascarada(self) -> str:
        """Direccion apta para log: parte local reducida a su inicial (`j***@mapfre.com`)."""

        local, arroba, dominio = (self.direccion or "").partition("@")
        if not arroba or not dominio:
            return MASCARA_SECRETO
        return f"{local[:1]}{MASCARA_SECRETO}@{dominio}"


def validar_direcciones(direcciones: Iterable[str]) -> tuple[tuple[str, ...], tuple[DireccionDescartada, ...]]:
    """
    Separa las direcciones utilizables de las que hay que descartar y trazar (REQ-133, validacion 3).

    Es una funcion PURA: no traza ni toca la red ni la base de datos; quien la usa decide que hacer
    con lo descartado. Cada entrada se recorta de espacios y se conserva con las mayusculas que
    traia (no se fuerza minusculas: la parte local es sensible a mayusculas en el RFC). Las
    repeticiones se eliminan comparando sin distinguir mayusculas y se conserva la PRIMERA forma
    vista, para no entregar dos veces el mismo aviso a la misma persona.

    Args:
        direcciones: direcciones candidatas, en el orden en que las resolvio el llamante.

    Returns:
        tuple: `(validas, descartadas)`; `validas` son las direcciones utilizables sin repetir y
        `descartadas` las que no cumplen el formato, con `MOTIVO_FORMATO_INVALIDO`.
    """

    validas: list[str] = []
    descartadas: list[DireccionDescartada] = []
    vistas: set[str] = set()
    for candidata in direcciones or ():
        direccion = str(candidata).strip() if candidata else ""
        if not _es_direccion_valida(direccion):
            descartadas.append(DireccionDescartada(direccion=direccion or str(candidata or ""), motivo=MOTIVO_FORMATO_INVALIDO))
            continue
        clave = direccion.casefold()
        if clave in vistas:
            continue
        vistas.add(clave)
        validas.append(direccion)
    return tuple(validas), tuple(descartadas)


class ClienteSmtpBcc:
    """
    Cliente SMTP que entrega UN mensaje con todos los destinatarios en copia oculta (AC-SMTP-01).

    Abre una conexion por envio y la cierra siempre (`with`), con el timeout acotado que viaja en
    `ParametrosSmtp`. No redacta el mensaje: recibe asunto y cuerpos ya compuestos.
    """

    def __init__(self, parametros: ParametrosSmtp) -> None:
        self.parametros = parametros

    def __repr__(self) -> str:
        """Delegado en `ParametrosSmtp`, que ya enmascara la credencial (REQ-076 / AC-SMTP-11)."""

        return f"ClienteSmtpBcc({self.parametros!r})"

    def enviar(
        self,
        *,
        asunto: str,
        cuerpo_texto: str,
        destinatarios: Sequence[str],
        cuerpo_html: str | None = None,
    ) -> ResultadoEntrega:
        """
        Entrega el aviso en un unico mensaje con los destinatarios en `Bcc`; NUNCA propaga excepcion.

        Orden de comprobaciones: primero se validan y se trazan las direcciones descartadas; si no
        queda ninguna valida se devuelve `NO_RECIPIENTS` SIN abrir conexion; solo entonces se compone
        y se entrega. El `Message-ID` se genera y se fija como cabecera ANTES de enviar, de modo que
        el identificador devuelto es exactamente el que viajo.

        Args:
            asunto: asunto ya compuesto del aviso.
            cuerpo_texto: cuerpo en texto plano (parte obligatoria del multipart).
            destinatarios: direcciones resueltas; todas viajan en copia oculta.
            cuerpo_html: alternativa HTML opcional.

        Returns:
            ResultadoEntrega: aceptado (250), sin destinatarios, transitorio, permanente o de
            configuracion. Nunca lanza: el llamante persiste el intento con el codigo que reciba.
        """

        validas, descartadas = validar_direcciones(destinatarios or ())
        self._trazar_descartadas(descartadas)
        if not validas:
            # Sin destinatarios no hay nada que entregar: se devuelve el desenlace SIN abrir conexion
            # (REQ-133, regla 3). Se construye con el constructor porque `ResultadoEntrega` no expone
            # un constructor nombrado para este caso y este modulo no amplia esa clase.
            logger.warning(
                "Envio SMTP omitido: no queda ningun destinatario valido",
                extra={"data": {"resultado": RESULTADO_SIN_DESTINATARIOS, "descartadas": len(descartadas)}},
            )
            return ResultadoEntrega(
                entregado=False,
                error_code=RESULTADO_SIN_DESTINATARIOS,
                error_message=_recortar(TEXTO_SIN_DESTINATARIOS),
            )

        message_id = make_msgid(domain=self._dominio_message_id())
        try:
            correo = self._componer(
                asunto=asunto,
                cuerpo_texto=cuerpo_texto,
                destinatarios=validas,
                cuerpo_html=cuerpo_html,
                message_id=message_id,
            )
        except Exception as error:  # noqa: BLE001 - el contrato es no lanzar tampoco al componer
            return ResultadoEntrega.transitorio(self._texto(error))

        try:
            rechazos = self._entregar(correo, validas)
        except smtplib.SMTPAuthenticationError as error:
            # Credencial o usuario invalidos: ni es rechazo del buzon ni se arregla reintentando.
            return ResultadoEntrega.de_configuracion(self._texto(error), smtp_response_code=_codigo_de_respuesta(error.smtp_code))
        except smtplib.SMTPConnectError as error:
            return ResultadoEntrega.transitorio(self._texto(error), smtp_response_code=_codigo_de_respuesta(error.smtp_code))
        except smtplib.SMTPRecipientsRefused as error:
            # El texto de esta excepcion vuelca el diccionario de rechazos CON LAS DIRECCIONES, asi
            # que no se usa: solo recuento y codigo, para no republicar correos (REQ-079).
            codigo = _primer_codigo_de_rechazos(error.recipients)
            detalle = f"SMTPRecipientsRefused: {len(error.recipients or {})} destinatario(s) rechazado(s), codigo {codigo or 'sin codigo'}"
            return self._por_codigo(codigo, _sanear(detalle, self.parametros))
        except smtplib.SMTPResponseException as error:
            # Cubre SMTPSenderRefused, SMTPDataError y cualquier otra respuesta con codigo.
            return self._por_codigo(_codigo_de_respuesta(error.smtp_code), self._texto(error))
        except (TimeoutError, ConnectionRefusedError, OSError) as error:
            # `socket.timeout` es alias de `TimeoutError`: timeout, conexion rehusada o corte de red
            # son siempre reintentables (REQ-134).
            return ResultadoEntrega.transitorio(self._texto(error))
        except Exception as error:  # noqa: BLE001 - contrato: enviar() no lanza
            return ResultadoEntrega.transitorio(self._texto(error))

        if rechazos:
            # Rechazo PARCIAL: el servidor acepto el mensaje para al menos un destinatario, luego el
            # correo ya viajo y reintentarlo duplicaria el aviso. Se da por aceptado y el rechazo
            # queda en el log con recuento y codigo, sin direcciones.
            logger.warning(
                "Entrega SMTP parcial: algun destinatario fue rechazado",
                extra={
                    "data": {
                        "rechazados": len(rechazos),
                        "smtp_response_code": _primer_codigo_de_rechazos(rechazos) or "sin codigo",
                    }
                },
            )
        return ResultadoEntrega.aceptado(message_id)

    def _trazar_descartadas(self, descartadas: Sequence[DireccionDescartada]) -> None:
        """Traza las direcciones descartadas SIEMPRE enmascaradas, nunca en claro (REQ-079)."""

        for descartada in descartadas:
            logger.warning(
                "Direccion de aviso descartada por formato invalido",
                extra={
                    "data": {
                        "direccion": descartada.enmascarada,
                        "motivo": descartada.motivo,
                        "descartadas": len(descartadas),
                    }
                },
            )

    def _componer(
        self,
        *,
        asunto: str,
        cuerpo_texto: str,
        destinatarios: Sequence[str],
        cuerpo_html: str | None,
        message_id: str,
    ) -> EmailMessage:
        """
        Construye el mensaje unico: `To` al grupo vacio y TODOS los destinatarios en `Bcc`.

        `undisclosed-recipients:;` es un grupo RFC 5322 sin miembros: da un `To` presentable y no
        aporta ningun destinatario de sobre, de modo que los unicos `RCPT TO` salen del `Bcc`.
        """

        correo = EmailMessage()
        correo["Message-ID"] = message_id
        correo["Subject"] = asunto
        correo["From"] = formataddr((self.parametros.nombre_remitente, self.parametros.remitente))
        correo["To"] = DESTINATARIOS_NO_REVELADOS
        correo["Bcc"] = ", ".join(destinatarios)
        correo.set_content(cuerpo_texto)
        if cuerpo_html:
            correo.add_alternative(cuerpo_html, subtype="html")
        return correo

    def _entregar(self, correo: EmailMessage, destinatarios: Sequence[str]) -> dict[str, tuple[int, bytes]]:
        """
        Abre la conexion, autentica si procede y entrega el mensaje con `send_message`.

        `send_message` es lo que hace REAL la copia oculta: transmite una copia del mensaje con
        `Bcc` y `Resent-Bcc` BORRADAS, asi que todos reciben el aviso y ninguna direccion viaja en
        las cabeceras (REQ-133 / AC-SMTP-01).

        El sobre (`RCPT TO`) se pasa EXPLICITO en `to_addrs` en vez de dejar que `send_message` lo
        deduzca de las cabeceras. Si se deduce, el grupo vacio `undisclosed-recipients:;` de la
        cabecera `To` aporta una direccion VACIA al sobre y el cliente emitiria un `RCPT TO:<>` que
        los servidores reales rechazan. Pasar `to_addrs` no altera el borrado de `Bcc`, que
        `send_message` hace siempre sobre su copia local, de modo que la copia oculta se mantiene.

        Args:
            correo: mensaje ya compuesto, con los destinatarios en `Bcc`.
            destinatarios: direcciones validadas que forman el sobre de la entrega.

        Returns:
            dict: destinatarios rechazados (vacio si el servidor los acepto todos).
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
            return sesion.send_message(correo, to_addrs=list(destinatarios))

    def _por_codigo(self, codigo: str | None, texto: str) -> ResultadoEntrega:
        """Clasifica una respuesta con codigo: 5xx definitivo, el resto reintentable (REQ-141)."""

        if codigo and codigo.startswith("5"):
            return ResultadoEntrega.permanente(texto, smtp_response_code=codigo)
        return ResultadoEntrega.transitorio(texto, smtp_response_code=codigo)

    def _texto(self, error: BaseException) -> str:
        """Texto de la excepcion con el tipo delante y saneado de usuario y contrasena (REQ-076)."""

        return _sanear(f"{type(error).__name__}: {error}", self.parametros)

    def _dominio_message_id(self) -> str:
        """Dominio del `Message-ID`: el del remitente, para no publicar el host interno."""

        _, _, dominio = self.parametros.remitente.partition("@")
        return dominio.strip() or DOMINIO_MESSAGE_ID_POR_DEFECTO


def cliente_bcc_activo(*, timeout_segundos: int) -> ClienteSmtpBcc | None:
    """
    Fabrica el cliente de copia oculta a partir de la configuracion SMTP activa.

    Devolver `None` -y no un cliente simulado- es deliberado: cuando no hay fila activa en
    `configuracion_smtp` o le falta `smtp_host` o `sender_address`, el llamante registra el intento
    con `CONFIG_ERROR` SIN intentar conexion (REQ-133, regla 6 y validaciones 1-2). Aqui no existe
    ningun modo de simulacion: un envio fingido marcaria avisos como ENVIADO sin que nadie los
    reciba.

    Args:
        timeout_segundos: tope de espera de la conexion SMTP.

    Returns:
        ClienteSmtpBcc | None: el cliente listo para entregar, o `None` si la configuracion SMTP no
        esta activa o esta incompleta.
    """

    parametros = ParametrosSmtp.activa(timeout_segundos=timeout_segundos)
    if parametros is None:
        logger.warning(
            "No hay configuracion SMTP activa: el aviso no se puede entregar",
            extra={"data": {"resultado": RESULTADO_CONFIGURACION_AUSENTE}},
        )
        return None
    if not parametros.host or not parametros.remitente:
        logger.warning(
            "La configuracion SMTP activa esta incompleta (falta smtp_host o sender_address)",
            extra={
                "data": {
                    "resultado": RESULTADO_CONFIGURACION_AUSENTE,
                    "host": bool(parametros.host),
                    "remitente": bool(parametros.remitente),
                }
            },
        )
        return None
    return ClienteSmtpBcc(parametros)


def _es_direccion_valida(direccion: str) -> bool:
    """Comprueba el formato admitido: longitud acotada, un solo `@`, sin espacios y dominio con punto."""

    if not direccion or len(direccion) > LONGITUD_MAXIMA_DIRECCION:
        return False
    return PATRON_DIRECCION.match(direccion) is not None


__all__ = [
    "DESTINATARIOS_NO_REVELADOS",
    "LONGITUD_MAXIMA_DIRECCION",
    "MOTIVO_FORMATO_INVALIDO",
    "RESULTADO_CONFIGURACION_AUSENTE",
    "RESULTADO_SIN_DESTINATARIOS",
    "ClienteSmtpBcc",
    "DireccionDescartada",
    "cliente_bcc_activo",
    "validar_direcciones",
]
