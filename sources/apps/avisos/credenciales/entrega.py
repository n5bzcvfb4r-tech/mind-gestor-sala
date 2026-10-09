"""
Entrega sincrona de los avisos de credencial de acceso (ARC-014, REQ-038, REQ-073).

Este modulo entrega los dos avisos del catalogo que transportan un secreto hasta el buzon del
usuario: `CREDENTIAL_ISSUED`, la credencial inicial que acompana al alta de una cuenta (REQ-038,
AC-USR-03 y AC-USR-04), y `PASSWORD_RESET`, el acceso temporal que emite un ADMINISTRADOR al
restablecer la contrasena de otro usuario (REQ-073, AC-RST-01 y AC-RST-03). Comparten modulo porque
comparten el unico problema que de verdad condiciona el diseno: el contenido del correo NO se puede
persistir.

POR QUE LA CREDENCIAL NUNCA SE PERSISTE
=======================================
La credencial temporal la genera el modulo de identidad y llega aqui YA GENERADA, como un argumento
en memoria. Ni se deriva aqui, ni se guarda, ni se devuelve al llamante. REQ-073 regla 4 y REQ-038
regla 1 prohiben expresamente conservarla en claro: lo unico que la aplicacion almacena es su
verificador (hash) en la tabla de usuario.

De ahi se sigue todo lo demas. El cuerpo del correo se compone EN MEMORIA, en el instante exacto de
la entrega, y `aviso_correo.body_text` se queda a NULO. Esa columna es NULABLE a proposito
(changeset `ddl-0.0.1-14-01`, DECISION 4) precisamente para admitir esta familia de avisos: congelar
el cuerpo en la fila equivaldria a escribir la contrasena en claro en la base de datos, que es justo
lo que los requisitos prohiben. Lo UNICO del contenido que se persiste es el `subject`, que describe
el motivo del correo y no lleva ningun secreto (ver `asunto_persistible`).

POR QUE LA ENTREGA ES SINCRONA Y NO LA HACE EL DESPACHADOR DE FONDO
===================================================================
El despachador de fondo solo sabe reenviar el contenido CONGELADO de la fila, y la credencial no
esta en la fila: si le tocara entregar este aviso, enviaria un correo sin contrasena o directamente
ninguno. La solicitud se encola igualmente (patron outbox, ADR-006) DENTRO de la transaccion de
negocio, para que el alta del usuario o el restablecimiento queden confirmados aunque despues falle
el SMTP (AC-AVI-05); pero la ENTREGA ocurre justo despues del commit, en la misma peticion, con la
credencial todavia viva en memoria. Como efecto colateral, esto satisface de sobra la ventana de
<=5 minutos que exige AC-RST-03: el correo sale en el mismo segundo, no en el siguiente barrido.

Si el proceso muere entre el commit y la entrega, la solicitud queda tomada en `ENVIANDO` y la
recuperacion de atascados la devuelve a `PENDIENTE`. El despachador la encontrara entonces SIN
contenido y la cerrara como `COMPOSICION_INCOMPLETA`. Esa traza no es un defecto: es la traza
VERDADERA. Ese correo nunca fue entregable, porque el secreto que debia llevar ya no existe en
ninguna parte, y la unica salida correcta es REEMITIR la credencial, no reenviar la solicitud.

QUE PASA SI EL SMTP FALLA
=========================
El usuario queda CREADO y el restablecimiento queda CONFIRMADO: es lo que fija el DoD de la tarea.
El llamante devuelve 502 con el mensaje en castellano que publica este modulo
(`MENSAJE_502_POR_TIPO`) y la operacion es REINTENTABLE sin duplicar registro: reintentar significa
REEMITIR, es decir generar una credencial NUEVA con un `discriminante` nuevo y encolar una solicitud
nueva, nunca crear un segundo registro de usuario ni reutilizar el secreto anterior.

Queda anotado que REQ-073 (escenario 6) sugiere lo contrario para el restablecimiento -no confirmar
el cambio si la entrega del correo falla- y que el DoD de la tarea manda que si se confirme. Se
transcribe el DoD, que es la fuente que gobierna esta unidad; la discrepancia se deja escrita aqui
para que quien la revise no la tome por un descuido.

SECRETOS Y DATO PERSONAL (REQ-076, REQ-079)
===========================================
La credencial temporal no aparece JAMAS en un log, ni en un `error_message` persistido, ni en un
`repr`. El `__repr__` de `DatosCredencial` esta reescrito justamente para eso (ver su docstring). En
las trazas de este modulo solo viajan identificadores, el tipo de aviso y codigos de resultado;
nunca el contenido del correo, nunca la direccion en claro y nunca la contrasena.
"""

import html
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from django.db import transaction

from apps.avisos.alta.composicion import configuracion_composicion
from apps.avisos.motor.configuracion import ConfiguracionMotorAvisos, configuracion_motor
from apps.avisos.motor.despachador import RESULTADO_ENVIADO
from apps.avisos.motor.errores import ErrorMotorAvisos
from apps.avisos.motor.estados import RESULTADOS_INTENTO, EstadoAviso, validar_transicion
from apps.avisos.motor.outbox import (
    TIPO_CREDENCIAL_EMITIDA,
    TIPO_RESTABLECIMIENTO,
    TIPOS_CREDENCIAL,
    ServicioOutboxAvisos,
    outbox,
)
from apps.avisos.motor.repositorio import RepositorioAvisoCorreo
from apps.avisos.motor.transporte import (
    RESULTADO_TRANSITORIO,
    MensajeCorreo,
    ResultadoEntrega,
    TransporteCorreo,
    transporte_por_defecto,
)
from apps.core.contexto import utc_now

if TYPE_CHECKING:  # pragma: no cover - solo anotaciones: los modelos no se importan antes de django.setup()
    from apps.core.models import AvisoCorreoEntity

logger = logging.getLogger(__name__)

#: Modulo consumidor que queda en la traza de entrega y de resolucion.
MODULO_ENTREGA: str = "ARC-014"

#: Ancho de la columna `aviso_correo.subject`. El asunto compuesto NUNCA lo excede.
LONGITUD_MAXIMA_ASUNTO: int = 255

#: Longitud minima exigible a una credencial temporal (REQ-073, regla 4). Aqui NO se genera ninguna
#: credencial: solo se rechaza la que llega por debajo del minimo, porque entregarla seria publicar
#: un secreto debil con la firma de la aplicacion.
LONGITUD_MINIMA_CREDENCIAL: int = 12

#: Sustituto con el que la credencial aparece en cualquier `repr`. Es longitud fija y no revela ni
#: siquiera cuantos caracteres tiene el secreto.
MASCARA_CREDENCIAL: str = "***"

#: Formato de las fechas que viajan al correo. Las marcas del motor son naive en UTC, asi que la
#: zona se nombra en el propio literal para que nadie la lea como hora local.
FORMATO_FECHA: str = "%d/%m/%Y %H:%M UTC"

# --- Mensajes que el llamante publica en el 502 (literales del requisito, CON acentos) ----
#: REQ-038: fallo de entrega del correo con la credencial inicial.
MENSAJE_502_CREDENCIAL = "No se ha podido enviar el correo con la credencial; reinténtalo"

#: REQ-073: fallo de entrega del correo con el acceso temporal del restablecimiento.
MENSAJE_502_RESTABLECIMIENTO = "No se ha podido enviar el correo con el acceso temporal; reinténtalo"

#: Mensaje de 502 por tipo de aviso. El llamante no elige texto: lo indexa por el tipo que encolo,
#: de modo que el usuario lee siempre el literal del requisito que corresponde a su operacion.
MENSAJE_502_POR_TIPO: dict[str, str] = {
    TIPO_CREDENCIAL_EMITIDA: MENSAJE_502_CREDENCIAL,
    TIPO_RESTABLECIMIENTO: MENSAJE_502_RESTABLECIMIENTO,
}

# --- Literales que viajan al correo (en castellano correcto, CON acentos; NFR-004) --------
ASUNTO_CREDENCIAL = "Tus credenciales de acceso a Gestión de Incidencias"
ASUNTO_RESTABLECIMIENTO = "Se ha restablecido tu contraseña de acceso"
SALUDO = "Hola, {nombre}:"
PARRAFO_CREDENCIAL = "Se ha creado tu cuenta en la aplicación de Gestión de Incidencias. Estos son tus datos de acceso."
PARRAFO_RESTABLECIMIENTO = (
    "Un administrador ha restablecido tu contraseña de acceso. Tu contraseña anterior ya no es válida: "
    "a partir de ahora debes entrar con la contraseña temporal que aparece más abajo."
)
ETIQUETA_USUARIO = "Usuario"
ETIQUETA_CREDENCIAL = "Contraseña temporal"
ETIQUETA_CADUCIDAD = "Válida hasta"
ETIQUETA_ACCESO = "Dirección de acceso"
ADVERTENCIA_UN_SOLO_USO = (
    "Esta contraseña temporal es de un solo uso: el sistema te exigirá establecer una contraseña nueva "
    "en tu primer acceso, antes de poder realizar ninguna otra operación."
)
ADVERTENCIA_CONFIDENCIALIDAD = (
    "No compartas esta contraseña con nadie. Ningún administrador conoce ni puede consultar esta contraseña, "
    "así que nadie del equipo te la pedirá nunca."
)
CIERRE_AVISO = "Si no esperabas este correo, avisa cuanto antes al administrador de la aplicación."

# --- Textos de error de validacion (sin acentos: son mensajes tecnicos de excepcion) ------
TIPO_NO_ES_DE_CREDENCIAL = "El tipo de aviso «{tipo}» no es un aviso de credencial; los admitidos son: {catalogo}."
USUARIO_INVALIDO = "El campo «user_id» debe ser un entero positivo para componer el aviso de credencial «{tipo}»."
NOMBRE_VACIO = "El campo «full_name» es obligatorio para componer el aviso de credencial «{tipo}» del usuario «{usuario}»."
CORREO_VACIO = "El campo «corporate_email» es obligatorio para componer el aviso de credencial «{tipo}» del usuario «{usuario}»."
CREDENCIAL_VACIA = "No hay credencial temporal que entregar en el aviso «{tipo}» del usuario «{usuario}»: el correo no tendria contenido."
CREDENCIAL_DEMASIADO_CORTA = (
    "La credencial temporal del aviso «{tipo}» del usuario «{usuario}» mide {longitud} caracteres y el minimo exigido es {minimo} "
    "(REQ-073, regla 4)."
)
CONTENIDO_VACIO = "La composicion del aviso de credencial «{tipo}» del usuario «{usuario}» ha producido un {parte} vacio."

# --- Textos de traza (identificadores y codigos, nunca contenido ni secretos) -------------
TRAZA_CONTENIDO_COMPUESTO = "Contenido del aviso de credencial compuesto en memoria: el cuerpo no se persiste"
TRAZA_COMPOSICION_IMPOSIBLE = "Datos insuficientes para componer el aviso de credencial"


class ErrorEntregaCredencial(ErrorMotorAvisos):
    """
    Los datos recibidos no bastan para componer el aviso de credencial.

    Es un defecto del LLAMANTE -un uso incorrecto de este servicio, con el contrato de entrada
    incompleto-, NO un fallo de entrega. Un fallo de entrega (SMTP caido, rechazo del servidor) se
    reporta como resultado y se traduce a 502 reintentable; esto, en cambio, significa que la
    operacion de negocio ha invocado el aviso sin nombre, sin correo o sin credencial, y reintentarlo
    tal cual volveria a fallar igual.

    Hereda de `ErrorMotorAvisos` (y por tanto de `ErrorDominio`) para que el texto en castellano
    viaje en `self.mensaje`. Ese texto NUNCA contiene la credencial: como mucho, su longitud.
    """


@dataclass(frozen=True, slots=True, repr=False)
class DatosCredencial:
    """
    Contrato de entrada del aviso de credencial: todo lo necesario para redactarlo, y nada mas.

    Transporta el secreto en memoria y solo durante la peticion que lo genero. Es inmutable
    (`frozen=True`) para que nadie pueda sustituir la credencial a mitad de la composicion, y usa
    `slots=True` para que no admita atributos nuevos: lo que no esta declarado aqui no puede acabar
    en un buzon.

    `expires_at` es naive en UTC, como el resto de marcas del motor, y es opcional porque no todas
    las politicas de emision fijan caducidad. `discriminante` identifica la EMISION concreta (es el
    mismo valor que distingue la clave del aviso en el outbox): al reemitir una credencial se genera
    uno nuevo, de modo que dos emisiones consecutivas al mismo usuario son dos solicitudes distintas
    y no un duplicado.
    """

    notification_type: str
    user_id: int
    full_name: str
    corporate_email: str
    credencial_temporal: str
    expires_at: datetime | None = None
    discriminante: str | None = None

    def __repr__(self) -> str:
        """
        Representacion con la credencial SIEMPRE enmascarada.

        El `repr` generado por `dataclass` volcaria la contrasena temporal en claro en cualquier
        traza de excepcion, en cualquier `logger.exception` que incluyera el argumento y en cualquier
        sesion de depuracion. Este metodo es la UNICA barrera que lo impide, y por eso la mascara no
        es condicional ni configurable: no hay ningun modo en que esta estructura se imprima con el
        secreto visible.

        Returns:
            str: representacion con los identificadores y `MASCARA_CREDENCIAL` en lugar del secreto.
        """

        return (
            f"{type(self).__name__}(notification_type={self.notification_type!r}, user_id={self.user_id!r}, "
            f"full_name={self.full_name!r}, corporate_email={self.corporate_email!r}, "
            f"credencial_temporal={MASCARA_CREDENCIAL!r}, expires_at={self.expires_at!r}, "
            f"discriminante={self.discriminante!r})"
        )

    @property
    def es_restablecimiento(self) -> bool:
        """Indica si el aviso es un restablecimiento por ADMINISTRADOR (`PASSWORD_RESET`, REQ-073)."""

        return self.notification_type == TIPO_RESTABLECIMIENTO

    def validar(self) -> None:
        """
        Comprueba que los datos bastan para redactar un correo de credencial con sentido.

        Se valida ANTES de componer y no durante: un correo de credencial a medio redactar es peor
        que ningun correo, porque llega al usuario sin poder usarlo y el secreto ya se ha quemado.
        Cada mensaje nombra el campo culpable para que el defecto se diagnostique sin depurar, y
        NINGUNO incluye la credencial: del secreto solo se publica su longitud, que es lo unico que
        hace falta para entender por que se rechazo.

        Raises:
            ErrorEntregaCredencial: si el tipo no pertenece al catalogo de avisos de credencial, si
                `user_id` no es un entero positivo, si falta el nombre o el correo del destinatario,
                o si la credencial esta vacia o es mas corta que `LONGITUD_MINIMA_CREDENCIAL`.
        """

        if self.notification_type not in TIPOS_CREDENCIAL:
            catalogo = ", ".join(sorted(TIPOS_CREDENCIAL))
            raise ErrorEntregaCredencial(TIPO_NO_ES_DE_CREDENCIAL.format(tipo=self.notification_type, catalogo=catalogo))

        if not isinstance(self.user_id, int) or isinstance(self.user_id, bool) or self.user_id <= 0:
            raise ErrorEntregaCredencial(USUARIO_INVALIDO.format(tipo=self.notification_type))

        if not (self.full_name or "").strip():
            raise ErrorEntregaCredencial(NOMBRE_VACIO.format(tipo=self.notification_type, usuario=self.user_id))

        if not (self.corporate_email or "").strip():
            raise ErrorEntregaCredencial(CORREO_VACIO.format(tipo=self.notification_type, usuario=self.user_id))

        credencial = self.credencial_temporal or ""
        if not credencial:
            raise ErrorEntregaCredencial(CREDENCIAL_VACIA.format(tipo=self.notification_type, usuario=self.user_id))
        if len(credencial) < LONGITUD_MINIMA_CREDENCIAL:
            raise ErrorEntregaCredencial(
                CREDENCIAL_DEMASIADO_CORTA.format(
                    tipo=self.notification_type,
                    usuario=self.user_id,
                    longitud=len(credencial),
                    minimo=LONGITUD_MINIMA_CREDENCIAL,
                )
            )


@dataclass(frozen=True, slots=True)
class ContenidoCredencial:
    """
    Correo de credencial ya redactado: asunto y cuerpos, sin destinatarios y sin remitente.

    El `subject` es lo UNICO que se escribe en `aviso_correo` (ver `asunto_persistible`): describe el
    motivo del correo y no lleva secreto. Los dos cuerpos viven SOLO en memoria, el tiempo que dura
    la peticion, y se entregan directamente al transporte SMTP; nunca se asignan a `body_text` ni a
    `body_html` de la fila, porque contienen la credencial en claro.
    """

    subject: str
    body_text: str
    body_html: str | None = None


def componer_asunto(datos: DatosCredencial) -> str:
    """
    Compone el asunto del aviso de credencial, distinto segun el tipo.

    El asunto describe el MOTIVO del correo y nada mas: ni el nombre del usuario, ni su correo, ni
    rastro de la credencial. Es lo unico del contenido que acaba persistido, asi que se mantiene
    deliberadamente generico. Se recorta a `LONGITUD_MAXIMA_ASUNTO`, que es el ancho de la columna
    `aviso_correo.subject`, para que la escritura no pueda fallar por longitud.

    Args:
        datos: datos del aviso de credencial.

    Returns:
        str: asunto en castellano, de `LONGITUD_MAXIMA_ASUNTO` caracteres como maximo.
    """

    asunto = ASUNTO_RESTABLECIMIENTO if datos.es_restablecimiento else ASUNTO_CREDENCIAL
    return asunto[:LONGITUD_MAXIMA_ASUNTO]


def componer_cuerpo_texto(datos: DatosCredencial) -> str:
    """
    Compone el cuerpo en TEXTO PLANO del aviso de credencial (REQ-038, REQ-073).

    El texto plano es el cuerpo PRINCIPAL, no un sucedaneo del HTML: es lo que lee quien tiene el
    cliente de correo en modo texto y lo que queda legible en cualquier archivador. Lleva, en este
    orden: saludo con el nombre, el parrafo que explica POR QUE llega este correo (alta de cuenta o
    restablecimiento por un administrador), el usuario de acceso, la contrasena temporal, la
    caducidad si la hay, la advertencia de que es de un solo uso y de que el sistema exigira
    establecer una contrasena nueva en el primer acceso (REQ-038, regla 3), la advertencia de
    confidencialidad, la direccion de acceso a la aplicacion y el cierre.

    La direccion de acceso se toma de `configuracion_composicion()[0]`, la misma base de la SPA que
    ya usa el aviso de alta: la lectura de `settings` no se reimplementa aqui.

    Args:
        datos: datos del aviso de credencial.

    Returns:
        str: cuerpo de texto completo, sin marcadores de plantilla pendientes.
    """

    url_acceso = configuracion_composicion()[0].rstrip("/")
    presentacion = PARRAFO_RESTABLECIMIENTO if datos.es_restablecimiento else PARRAFO_CREDENCIAL

    lineas: list[str] = [
        SALUDO.format(nombre=datos.full_name.strip()),
        "",
        presentacion,
        "",
        f"{ETIQUETA_USUARIO}: {datos.corporate_email.strip()}",
        f"{ETIQUETA_CREDENCIAL}: {datos.credencial_temporal}",
    ]
    if datos.expires_at is not None:
        lineas.append(f"{ETIQUETA_CADUCIDAD}: {datos.expires_at.strftime(FORMATO_FECHA)}")
    lineas.extend(
        [
            "",
            ADVERTENCIA_UN_SOLO_USO,
            "",
            ADVERTENCIA_CONFIDENCIALIDAD,
            "",
            f"{ETIQUETA_ACCESO}: {url_acceso}",
            "",
            CIERRE_AVISO,
        ]
    )
    return "\n".join(lineas)


def componer_cuerpo_html(datos: DatosCredencial) -> str:
    """
    Compone el cuerpo HTML del aviso: la MISMA informacion que el texto plano, con marcado minimo.

    No hay una sola interpolacion sin escapar. El nombre, el correo, la credencial y la URL pasan por
    `html.escape`, y la URL ademas con `quote=True`: el nombre lo teclea un administrador en el alta
    y la credencial la genera el modulo de identidad con un alfabeto que puede incluir `&`, `<` o
    comillas, de modo que interpolarlos en crudo partiria el marcado o, peor, lo convertiria en
    marcado activo. La URL viaja como texto y no como `<a href=...>` a proposito: un enlace en un
    correo que contiene una contrasena es exactamente el patron que los filtros antifraude -y los
    propios usuarios- deben desconfiar.

    Args:
        datos: datos del aviso de credencial.

    Returns:
        str: documento HTML bien formado, autocontenido y sin recursos externos.
    """

    url_acceso = html.escape(configuracion_composicion()[0].rstrip("/"), quote=True)
    presentacion = PARRAFO_RESTABLECIMIENTO if datos.es_restablecimiento else PARRAFO_CREDENCIAL

    caducidad = ""
    if datos.expires_at is not None:
        caducidad = f"<p><strong>{html.escape(ETIQUETA_CADUCIDAD)}:</strong> {html.escape(datos.expires_at.strftime(FORMATO_FECHA))}</p>"

    return (
        '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8" />'
        f"<title>{html.escape(componer_asunto(datos))}</title></head><body>"
        f"<p>{html.escape(SALUDO.format(nombre=datos.full_name.strip()))}</p>"
        f"<p>{html.escape(presentacion)}</p>"
        f"<p><strong>{html.escape(ETIQUETA_USUARIO)}:</strong> {html.escape(datos.corporate_email.strip())}</p>"
        f"<p><strong>{html.escape(ETIQUETA_CREDENCIAL)}:</strong> {html.escape(datos.credencial_temporal)}</p>"
        f"{caducidad}"
        f"<p>{html.escape(ADVERTENCIA_UN_SOLO_USO)}</p>"
        f"<p>{html.escape(ADVERTENCIA_CONFIDENCIALIDAD)}</p>"
        f"<p><strong>{html.escape(ETIQUETA_ACCESO)}:</strong> {url_acceso}</p>"
        f"<p>{html.escape(CIERRE_AVISO)}</p>"
        "</body></html>"
    )


def componer_contenido(datos: DatosCredencial) -> ContenidoCredencial:
    """
    Redacta el aviso de credencial completo: valida, compone asunto y cuerpos y comprueba el resultado.

    Es el UNICO punto de entrada que deberia usar el llamante; `componer_asunto` y los dos cuerpos se
    exponen por separado para poder verificarlos pieza a pieza.

    Las POSTCONDICIONES no son decorativas: si alguien toca los literales y deja el asunto o el
    cuerpo vacios, el fallo tiene que saltar AQUI, en castellano, y no en forma de correo en blanco
    con una contrasena que el usuario nunca llegara a leer. La traza final registra longitudes, nunca
    contenido.

    Args:
        datos: datos del aviso de credencial.

    Returns:
        ContenidoCredencial: asunto, cuerpo de texto y cuerpo HTML listos para el transporte.

    Raises:
        ErrorEntregaCredencial: datos insuficientes (ver `DatosCredencial.validar`) o resultado
            invalido (asunto o cuerpo vacios).
    """

    datos.validar()

    subject = componer_asunto(datos)
    body_text = componer_cuerpo_texto(datos)
    body_html = componer_cuerpo_html(datos)

    if not subject.strip():
        raise ErrorEntregaCredencial(CONTENIDO_VACIO.format(tipo=datos.notification_type, usuario=datos.user_id, parte="asunto"))
    if not body_text.strip():
        raise ErrorEntregaCredencial(CONTENIDO_VACIO.format(tipo=datos.notification_type, usuario=datos.user_id, parte="cuerpo de texto"))

    logger.debug(
        TRAZA_CONTENIDO_COMPUESTO,
        extra={
            "data": {
                "notification_type": datos.notification_type,
                "user_id": datos.user_id,
                "longitud_asunto": len(subject),
                "longitud_cuerpo": len(body_text),
            }
        },
    )
    return ContenidoCredencial(subject=subject, body_text=body_text, body_html=body_html)


def asunto_persistible(datos: DatosCredencial) -> str:
    """
    Devuelve lo UNICO del contenido que se escribe en `aviso_correo`: el asunto.

    Es un alias explicito de `componer_asunto`, y existe para que el punto del codigo que rellena la
    fila del outbox lo diga con su propio nombre. Cualquier intento futuro de persistir tambien el
    cuerpo tropieza con que esta funcion no lo devuelve: el nombre es la barrera documental de que
    `body_text` se queda a NULO a proposito (REQ-038 regla 1, REQ-073 regla 4).

    Args:
        datos: datos del aviso de credencial.

    Returns:
        str: asunto del aviso, sin ningun secreto, listo para `aviso_correo.subject`.
    """

    return componer_asunto(datos)


@dataclass(frozen=True, slots=True)
class SolicitudCredencial:
    """
    Solicitud de aviso de credencial ya encolada, en viaje entre las dos fases de la entrega.

    Es lo que el servicio de negocio recibe al ENCOLAR (fase 1, dentro de su `transaction.atomic()`) y
    lo que despues le devuelve al servicio para ENTREGAR (fase 2, ya fuera de la transaccion). Existe
    precisamente porque las dos fases NO pueden ocurrir en el mismo punto del codigo: entre una y otra
    hay un commit, y algo tiene que cruzarlo llevando la fila que se acaba de encolar. Ese «algo» es
    esta estructura, y por eso es inmutable (`frozen=True`) y cerrada (`slots=True`): no transporta
    nada mas que la referencia a la solicitud, su clave de idempotencia y si la creo ESTA peticion.

    `creada=False` significa que la solicitud ya estaba encolada con esa misma `notification_key`
    (REQ-130, AC-AVI-01): la entrega no la ha generado esta peticion y, por tanto, esta peticion no la
    ha tomado para si. No lleva la credencial: el secreto viaja aparte, en `DatosCredencial`, y nunca
    se guarda en una estructura que pueda acabar en una traza de la fila.
    """

    aviso: "AvisoCorreoEntity"
    notification_key: str
    creada: bool

    @property
    def notification_id(self) -> str:
        """Identificador de la solicitud para trazas y respuestas; es un UUID y viaja siempre como texto."""

        return str(self.aviso.pk)


# --- Textos de traza de la entrega en dos fases (identificadores, nunca secretos) ---------
TRAZA_SOLICITUD_ENCOLADA = "Solicitud de aviso de credencial encolada en el outbox dentro de la transaccion de negocio"
TRAZA_SOLICITUD_TOMADA = "Solicitud de aviso de credencial tomada para la entrega sincrona de esta peticion"
TRAZA_SOLICITUD_PREEXISTENTE = "La solicitud de aviso de credencial ya estaba encolada: no se toma ni se reescribe su estado"


# --- Textos de error de entrega (sin acentos: son mensajes tecnicos persistidos) ----------
#: Los dos mensajes de usuario del 502 NO se redeclaran aqui: ya los publica este modulo mas arriba
#: (`MENSAJE_502_CREDENCIAL`, `MENSAJE_502_RESTABLECIMIENTO`) y los indexa `MENSAJE_502_POR_TIPO`.
#: Son los literales CON acentos de REQ-038 y REQ-073, y la entrega los reutiliza tal cual.

#: Motivo de no haber llegado siquiera a intentar la entrega. Va a `aviso_correo_intento.error_message`
#: (maximo 500 caracteres), asi que es tecnico y sin acentos, y nombra la tabla donde mirar.
SIN_CONFIGURACION_SMTP = (
    "No hay configuracion SMTP activa y completa en `configuracion_smtp`: el aviso de credencial no se intenta entregar."
)

#: Motivo de un fallo que el transporte no supo clasificar. Se registra como intento REINTENTABLE
#: porque un error desconocido no demuestra que el correo sea inentregable, solo que esta entrega no
#: prospero; la excepcion original nunca se vuelca aqui, para que su `repr` no arrastre la credencial.
FALLO_INESPERADO_ENTREGA = "Fallo inesperado al entregar el aviso de credencial ({tipo}): se registra un intento reintentable."

# --- Textos de traza de la fase de entrega (identificadores y codigos, nunca secretos) ----
TRAZA_ENTREGADO = "Aviso de credencial entregado al buzon corporativo del usuario"
TRAZA_NO_ENTREGADO = "El intento de entrega del aviso de credencial no ha prosperado: la solicitud queda FALLIDO"
TRAZA_SIN_CONFIGURACION = "Sin configuracion SMTP activa: el aviso de credencial no se intenta entregar"
TRAZA_ENTREGA_OMITIDA = "La entrega del aviso de credencial se omite: la solicitud no pertenece a esta peticion o ya consta entregada"
TRAZA_FALLO_INESPERADO = "Fallo inesperado del transporte al entregar el aviso de credencial"


@dataclass(frozen=True, slots=True)
class ResultadoEntregaCredencial:
    """
    Desenlace de la fase 2: lo que el servicio de negocio recibe DESPUES del commit.

    `entregado=False` NUNCA significa «deshaz la operacion». Cuando este resultado llega, el usuario
    ya esta creado y el restablecimiento ya esta confirmado: la transaccion de negocio cerro en la
    fase 1 y no hay nada que revertir (DoD de TSK-020, AC-AVI-05, REQ-038 regla 6). Lo unico que
    procede es responder 502 con `mensaje_usuario` -el literal del requisito, ya en castellano- y
    dejar que el ADMINISTRADOR REEMITA la credencial, que es generar un secreto nuevo con un
    `discriminante` nuevo, no reintentar el envio del anterior.

    `omitida=True` separa el caso en que NO hay fallo que reportar: esta peticion no es duena de la
    solicitud -ya estaba encolada con la misma clave de idempotencia (REQ-130, AC-AVI-01) y la tomo
    otra- o la solicitud ya consta `ENVIADO`. Ahi no se intento entregar nada y, por tanto, NO se
    responde 502; por eso el llamante no puede mirar `entregado` a secas y mira `debe_responder_502`.

    El resultado NO transporta la credencial ni la direccion del destinatario: solo identificadores,
    el identificador de mensaje que devolvio el transporte y un codigo de error (REQ-076, REQ-079).
    """

    entregado: bool
    notification_id: str
    omitida: bool = False
    message_id: str | None = None
    codigo_error: str | None = None
    mensaje_usuario: str | None = None

    @property
    def debe_responder_502(self) -> bool:
        """
        Unica senal que el servicio de negocio necesita para decidir el 502 de REQ-038 y REQ-073.

        Se expone como propiedad y no se deja que el llamante componga la condicion a mano porque la
        omision es exactamente el caso que se escapa al leer solo `entregado`: una entrega omitida
        tambien tiene `entregado=False` y, sin embargo, no es un fallo que deba llegar al usuario.

        Returns:
            bool: `True` solo si hubo intento de entrega y no prospero.
        """

        return not self.entregado and not self.omitida

    @classmethod
    def entregado_con(cls, *, notification_id: str, message_id: str | None) -> "ResultadoEntregaCredencial":
        """
        Construye el desenlace de una entrega que SI prospero.

        Args:
            notification_id: identificador de la solicitud entregada.
            message_id: identificador que devolvio el transporte, si lo publico.

        Returns:
            ResultadoEntregaCredencial: resultado entregado, sin mensaje de usuario que publicar.
        """

        return cls(entregado=True, notification_id=notification_id, message_id=message_id)

    @classmethod
    def no_entregado(cls, *, notification_id: str, codigo_error: str | None, mensaje_usuario: str) -> "ResultadoEntregaCredencial":
        """
        Construye el desenlace de un intento de entrega que no prospero.

        `mensaje_usuario` es obligatorio a proposito: un fallo de entrega SIEMPRE acaba en una
        respuesta 502 que el usuario lee, y dejar ese texto opcional permitiria devolver un 502 mudo.

        Args:
            notification_id: identificador de la solicitud que no se pudo entregar.
            codigo_error: codigo de clasificacion del fallo, si el transporte lo publico.
            mensaje_usuario: literal en castellano que el llamante devuelve en el 502.

        Returns:
            ResultadoEntregaCredencial: resultado no entregado, con `debe_responder_502` a `True`.
        """

        return cls(
            entregado=False,
            notification_id=notification_id,
            codigo_error=codigo_error,
            mensaje_usuario=mensaje_usuario,
        )

    @classmethod
    def omitido(cls, *, notification_id: str) -> "ResultadoEntregaCredencial":
        """
        Construye el desenlace de una entrega que ni se intento, y que NO es un fallo.

        Args:
            notification_id: identificador de la solicitud cuya entrega se omite.

        Returns:
            ResultadoEntregaCredencial: resultado omitido, con `debe_responder_502` a `False`.
        """

        return cls(entregado=False, notification_id=notification_id, omitida=True)


class ServicioEntregaCredencial:
    """
    Entrega de los avisos de credencial en DOS FASES, que es la unica forma en que el DoD se sostiene.

    FASE 1 - `encolar(datos)`, DENTRO del `transaction.atomic()` del alta de usuario o del
    restablecimiento. Es el patron outbox (ADR-006, REQ-132): la solicitud se inserta en la misma
    transaccion que crea el usuario o confirma el restablecimiento, de modo que si el SMTP esta caido
    el usuario queda CREADO igualmente y el cambio queda CONFIRMADO (AC-AVI-05, REQ-038 regla 6). Un
    fallo de correo no revierte jamas una operacion de negocio que ya es valida.

    FASE 2 - `entregar(...)`, DESPUES del commit, NUNCA con la transaccion abierta. Abrir una conexion
    SMTP dentro de la transaccion es el anti-patron que el handbook declara prohibido, y aqui tiene
    ademas una consecuencia muy concreta: una conexion colgada (un servidor que acepta el TCP y no
    responde) mantendria abierta la transaccion y con ella el bloqueo de la fila del usuario recien
    creado durante todo el timeout, atascando cualquier otra operacion sobre ese usuario. Por eso no
    puede ser una sola llamada: no es una comodidad de diseno, es que las dos mitades pertenecen a dos
    contextos transaccionales distintos y solo el LLAMANTE conoce la frontera entre ambos.

    El uso correcto, que es el que deben copiar el alta y el restablecimiento:

    ```
    servicio = ServicioEntregaCredencial()
    with transaction.atomic():
        usuario = repositorio.crear(...)  # alta o restablecimiento
        solicitud = servicio.encolar(datos)  # outbox, misma transaccion
    resultado = servicio.entregar(solicitud, datos)  # ya fuera: SMTP real
    if not resultado.entregado:
        raise AppError(502, resultado.mensaje_usuario)
    ```

    Los colaboradores se inyectan por constructor para poder verificar el comportamiento sin base de
    datos y sin servidor de correo, pero la POLITICA no es sustituible: que el cuerpo no se persista,
    que la solicitud se tome en la fase 1 y que un fallo de entrega no revierta nada se deciden aqui.
    """

    def __init__(
        self,
        *,
        outbox_servicio: ServicioOutboxAvisos | None = None,
        config: ConfiguracionMotorAvisos | None = None,
        transporte: TransporteCorreo | None = None,
        repositorio: RepositorioAvisoCorreo | None = None,
    ) -> None:
        """
        Construye el servicio guardando sus colaboradores SIN resolverlos.

        La resolucion es PEREZOSA, igual que en `ServicioOutboxAvisos`: este modulo se importa al
        cargar las URLs y los servicios de negocio, antes de que `django.setup()` haya terminado, y
        tanto el outbox y el repositorio (que tocan modelos del ORM) como `configuracion_motor()`
        (que lee `settings`) y el transporte (que lee la fila activa de `configuracion_smtp`)
        reventarian si se construyeran a nivel de modulo o en el constructor.

        Args:
            outbox_servicio: servicio de encolado; por defecto, el real (`outbox()`).
            config: configuracion del motor, de donde sale el identificador del trabajador; por
                defecto, la vigente en `settings.AVISOS_MOTOR`.
            transporte: puerto de entrega SMTP; por defecto, el que fabrica `transporte_por_defecto`
                con la configuracion SMTP activa.
            repositorio: acceso a datos de la cola para la traza del intento y el cierre de la
                transicion; por defecto, el real (`RepositorioAvisoCorreo()`).
        """

        self._outbox: ServicioOutboxAvisos | None = outbox_servicio
        self._config: ConfiguracionMotorAvisos | None = config
        self._transporte: TransporteCorreo | None = transporte
        self._repositorio: RepositorioAvisoCorreo | None = repositorio

    # --- Resolucion perezosa de dependencias -------------------------------

    def _resolver_outbox(self) -> ServicioOutboxAvisos:
        """Devuelve el outbox inyectado o construye el real en el primer uso, nunca a nivel de modulo."""

        if self._outbox is None:
            self._outbox = outbox()
        return self._outbox

    def _resolver_config(self) -> ConfiguracionMotorAvisos:
        """Devuelve la configuracion inyectada o la lee de `settings.AVISOS_MOTOR` en el primer uso."""

        if self._config is None:
            self._config = configuracion_motor()
        return self._config

    def _resolver_repositorio(self) -> RepositorioAvisoCorreo:
        """
        Devuelve el repositorio inyectado o construye el real en el primer uso, nunca a nivel de modulo.

        NO se reutiliza el repositorio del outbox a proposito: `ServicioOutboxAvisos` lo mantiene
        PRIVADO (`_repositorio`, resuelto por `_resolver_repositorio`) y no publica ningun accesor,
        asi que alcanzarlo seria hurgar en el estado interno de otro servicio y atar esta fase a un
        detalle suyo que puede cambiar. `RepositorioAvisoCorreo` no guarda estado de negocio -solo
        los modelos que resuelve de forma perezosa- y la conexion de base de datos la gestiona
        Django por hilo, de modo que una instancia propia no abre nada adicional.

        Returns:
            RepositorioAvisoCorreo: acceso a datos de la cola de avisos y de su traza de intentos.
        """

        if self._repositorio is None:
            self._repositorio = RepositorioAvisoCorreo()
        return self._repositorio

    def _resolver_transporte(self) -> TransporteCorreo | None:
        """
        Devuelve el transporte inyectado o fabrica el de produccion con la configuracion SMTP activa.

        NO se memoriza el resultado de la fabrica: la fila activa de `configuracion_smtp` la puede
        cambiar el ADMINISTRADOR en caliente y cada entrega debe trabajar con la vigente.

        Devolver `None` NO es un error ni habilita ningun modo simulado: significa que no hay fila
        activa en `configuracion_smtp`, y quien llama lo traduce a un intento `CONFIG_ERROR` con la
        solicitud cerrada como `FALLIDO`. Sin transporte no se marca NADA como entregado.

        Returns:
            TransporteCorreo | None: el puerto de entrega vigente, o `None` si no hay configuracion
            SMTP activa.
        """

        if self._transporte is not None:
            return self._transporte
        return transporte_por_defecto(self._resolver_config())

    # --- Fase 1: encolado dentro de la transaccion de negocio ---------------

    def encolar(self, datos: DatosCredencial) -> SolicitudCredencial:
        """
        Encola la solicitud del aviso de credencial y la TOMA para entregarla en esta misma peticion.

        Se invoca DENTRO del `transaction.atomic()` del alta o del restablecimiento. No abre ni cierra
        transaccion alguna: la frontera transaccional es del servicio de negocio que encola.

        De la solicitud se persiste el `subject` y NADA del cuerpo. Los datos se validan antes de
        tocar la base, porque un aviso de credencial incompleto es un defecto del llamante y debe
        saltar en castellano y no como una restriccion de la base en mitad del alta.

        Args:
            datos: datos del aviso de credencial, con el secreto todavia en memoria.

        Returns:
            SolicitudCredencial: la solicitud encolada, lista para pasarsela a `entregar` tras el commit.

        Raises:
            ErrorEntregaCredencial: si los datos no bastan para redactar el aviso (ver `DatosCredencial.validar`).
            ErrorMotorAvisos: si el tipo de aviso no pertenece al catalogo de avisos de credencial.
            TransicionAvisoNoPermitidaError: si la solicitud recien creada no admite pasar a `ENVIANDO`.
        """

        datos.validar()

        resultado = self._resolver_outbox().encolar_aviso_credencial(
            notification_type=datos.notification_type,
            recipient_user_id=datos.user_id,
            discriminante=datos.discriminante,
            recipient_email=datos.corporate_email.strip(),
            subject=asunto_persistible(datos),
            # DECISION CENTRAL DE ESTE METODO: `body_text` y `body_html` NO se pasan, de modo que las
            # dos columnas quedan a NULO. El cuerpo lleva la credencial temporal EN CLARO y REQ-073
            # (regla 4) y REQ-038 (regla 1) prohiben persistirla: congelarla en la fila seria escribir
            # la contrasena en la base de datos. El cuerpo se compone en memoria al entregar.
        )

        aviso = resultado.aviso
        if resultado.creado:
            self._tomar_para_entrega(aviso)
        else:
            # Idempotencia (REQ-130, AC-AVI-01): la solicitud ya existia con esa misma clave, asi que
            # NO se fuerza a `ENVIANDO`. Reescribir el estado de una solicitud ajena le robaria la fila
            # a quien la tomo. Reemitir una credencial es encolar con un `discriminante` NUEVO -una
            # solicitud distinta-, nunca reaprovechar la anterior.
            logger.info(
                TRAZA_SOLICITUD_PREEXISTENTE,
                extra={"data": {"notification_id": str(aviso.pk), "notification_type": datos.notification_type}},
            )

        # Traza con identificadores y nada mas: ni la credencial ni la direccion del destinatario, que
        # es dato personal y no viaja a los logs (REQ-076, REQ-079).
        logger.info(
            TRAZA_SOLICITUD_ENCOLADA,
            extra={
                "data": {
                    "notification_id": str(aviso.pk),
                    "notification_type": datos.notification_type,
                    "recipient_user_id": datos.user_id,
                    "creada": resultado.creado,
                }
            },
        )
        return SolicitudCredencial(aviso=aviso, notification_key=resultado.notification_key, creada=resultado.creado)

    def _tomar_para_entrega(self, aviso: "AvisoCorreoEntity") -> None:
        """
        Marca la solicitud recien creada como `ENVIANDO` a nombre de este proceso, aqui y no despues.

        POR QUE SE TOMA DENTRO DE LA TRANSACCION DE NEGOCIO: porque es lo que cierra la carrera con el
        despachador de fondo. En cuanto el commit publica la fila, el barrido de fondo puede verla; y
        el despachador, que solo sabe reenviar el contenido CONGELADO de la fila, encontraria un aviso
        sin cuerpo y lo cerraria como `COMPOSICION_INCOMPLETA` mientras esta peticion todavia lo esta
        entregando con la credencial viva en memoria. Dejandola en `ENVIANDO` ya desde la transaccion
        eso no puede pasar: `tomar_pendientes` solo mira solicitudes en `PENDIENTE`, de modo que la
        fila no es visible para el despachador y nadie puede robarla.

        SI EL PROCESO MUERE ANTES DE ENTREGAR, la solicitud se queda tomada y la recuperacion de
        atascados (`recuperar_atascados`) la devuelve a `PENDIENTE` pasada la ventana; el despachador
        la encontrara entonces sin contenido y la cerrara como `COMPOSICION_INCOMPLETA`. Esa traza NO
        es un defecto: es la traza VERDADERA. Ese correo nunca fue entregable, porque el secreto que
        debia llevar ya no existe en ninguna parte, y la unica salida correcta es REEMITIR la
        credencial, no reenviar la solicitud.

        Args:
            aviso: solicitud recien creada, en `PENDIENTE`.

        Raises:
            TransicionAvisoNoPermitidaError: si el estado de la solicitud no admite pasar a `ENVIANDO`.
        """

        notification_id = str(aviso.pk)
        validar_transicion(aviso.status, EstadoAviso.ENVIANDO, notification_id=notification_id)

        aviso.status = EstadoAviso.ENVIANDO.value
        aviso.locked_by = self._resolver_config().identificador_worker
        aviso.locked_at = utc_now()
        # `update_fields` acota el UPDATE al testigo de trabajador: los CLOB `body_text` y `body_html`
        # NO se reescriben, que es justo lo que mantiene el cuerpo a NULO (REQ-038, REQ-073).
        aviso.save(update_fields=["status", "locked_by", "locked_at"])

        logger.debug(
            TRAZA_SOLICITUD_TOMADA,
            extra={"data": {"notification_id": notification_id, "status": aviso.status, "locked_by": aviso.locked_by}},
        )

    # --- Fase 2: entrega SMTP real, ya fuera de la transaccion de negocio ---

    def entregar(self, solicitud: SolicitudCredencial, datos: DatosCredencial) -> ResultadoEntregaCredencial:
        """
        Entrega el aviso de credencial por SMTP y cierra su ciclo de vida, DESPUES del commit.

        Se invoca SIEMPRE con la transaccion de negocio ya confirmada y NUNCA con ella abierta: abrir
        una conexion SMTP dentro de la transaccion mantendria bloqueada la fila del usuario durante
        todo el timeout del servidor de correo, atascando cualquier otra operacion sobre ese usuario.
        El uso correcto es el que documenta la clase:

        ```
        servicio = servicio_entrega_credencial()
        with transaction.atomic():
            usuario = repositorio.crear(...)  # alta o restablecimiento
            solicitud = servicio.encolar(datos)  # outbox, misma transaccion
        resultado = servicio.entregar(solicitud, datos)  # ya fuera: SMTP real
        if resultado.debe_responder_502:
            raise AppError(502, resultado.mensaje_usuario)
        ```

        El cuerpo del correo se compone AQUI, en memoria, con la credencial todavia viva, y no se
        persiste en ningun momento (ver la cabecera del modulo). El transporte no puede tumbar la
        peticion: cualquier excepcion se captura y se convierte en un intento reintentable, porque la
        operacion de negocio ya esta comprometida y no hay nada que revertir.

        Args:
            solicitud: la solicitud que devolvio `encolar` en la fase 1, ya confirmada en base.
            datos: los mismos datos del aviso, con el secreto todavia en memoria.

        Returns:
            ResultadoEntregaCredencial: entregado, no entregado (con el literal del 502 que publica
            `MENSAJE_502_POR_TIPO`) u omitido si la solicitud no pertenece a esta peticion o ya
            constaba `ENVIADO`.

        Raises:
            ErrorEntregaCredencial: si los datos no bastan para redactar el aviso (defecto del
                llamante; ver `DatosCredencial.validar`).
            TransicionAvisoNoPermitidaError: si el estado de la solicitud no admite el cierre que
                corresponde al desenlace de la entrega.
        """

        notification_id = solicitud.notification_id
        aviso = solicitud.aviso

        # GUARDA DE PROPIEDAD E IDEMPOTENCIA. Son dos casos distintos con el mismo desenlace:
        # - `creada=False`: la solicitud ya estaba encolada con esa misma clave (REQ-130, AC-AVI-01),
        #   asi que pertenece a OTRA emision y esta peticion no la tomo. Entregarla aqui duplicaria
        #   el correo de aquella emision, y ademas con un secreto que no es el suyo.
        # - `ENVIADO`: es estado TERMINAL (REQ-142 regla 2). Ese correo ya viajo y no se reenvia.
        # Ninguno de los dos es un fallo, de modo que el llamante NO responde 502.
        if not solicitud.creada or aviso.status == EstadoAviso.ENVIADO.value:
            logger.info(
                TRAZA_ENTREGA_OMITIDA,
                extra={
                    "data": {
                        "notification_id": notification_id,
                        "notification_type": datos.notification_type,
                        "recipient_user_id": datos.user_id,
                        "status": aviso.status,
                        "creada": solicitud.creada,
                    }
                },
            )
            return ResultadoEntregaCredencial.omitido(notification_id=notification_id)

        # El contenido se redacta en MEMORIA y no se escribe en la fila: lleva la credencial en claro
        # (REQ-038 regla 1, REQ-073 regla 4). Se valida antes de tocar el transporte.
        datos.validar()
        contenido = componer_contenido(datos)

        # Foto del destinatario del intento, en el MISMO formato que `componer_snapshot` del aviso de
        # alta (REQ-135, AC-SMTP-11): array JSON con EXACTAMENTE `user_id`, `full_name` y
        # `corporate_email`. Un aviso de credencial tiene siempre UN destinatario: el dueno del
        # secreto. La direccion solo vive aqui, en la columna de auditoria; nunca en un log.
        snapshot = json.dumps(
            [
                {
                    "user_id": datos.user_id,
                    "full_name": datos.full_name.strip(),
                    "corporate_email": datos.corporate_email.strip(),
                }
            ],
            ensure_ascii=False,
        )
        recuento = 1

        transporte = self._resolver_transporte()
        if transporte is None:
            # Sin fila activa en `configuracion_smtp` no se intenta NADA y no se finge ningun envio:
            # se deja un intento `CONFIG_ERROR` trazado y la solicitud cerrada sin entrega.
            logger.warning(
                TRAZA_SIN_CONFIGURACION,
                extra={
                    "data": {
                        "notification_id": notification_id,
                        "notification_type": datos.notification_type,
                        "recipient_user_id": datos.user_id,
                    }
                },
            )
            resultado = ResultadoEntrega.de_configuracion(SIN_CONFIGURACION_SMTP)
        else:
            try:
                resultado = transporte.enviar(
                    MensajeCorreo(
                        destinatarios=(datos.corporate_email.strip(),),
                        asunto=contenido.subject,
                        cuerpo_texto=contenido.body_text,
                        cuerpo_html=contenido.body_html,
                    )
                )
            except Exception:
                # El contrato del puerto dice que `enviar` no lanza, pero un transporte mal
                # implementado no puede llevarse por delante un alta que ya esta confirmada. El
                # detalle de la excepcion queda en el log (su `repr` no publica la credencial: ver
                # `DatosCredencial.__repr__`) y lo que se persiste es un texto fijo y sin secretos.
                logger.exception(
                    TRAZA_FALLO_INESPERADO,
                    extra={
                        "data": {
                            "notification_id": notification_id,
                            "notification_type": datos.notification_type,
                            "recipient_user_id": datos.user_id,
                        }
                    },
                )
                resultado = ResultadoEntrega.transitorio(FALLO_INESPERADO_ENTREGA.format(tipo=datos.notification_type))

        return self._cerrar_entrega(aviso, resultado=resultado, snapshot=snapshot, recuento=recuento, datos=datos)

    def _cerrar_entrega(
        self,
        aviso: "AvisoCorreoEntity",
        *,
        resultado: ResultadoEntrega,
        snapshot: str,
        recuento: int,
        datos: DatosCredencial,
    ) -> ResultadoEntregaCredencial:
        """
        Persiste el desenlace de la entrega: traza del intento y transicion, en UNA sola transaccion.

        El intento y la transicion van juntos en el mismo `transaction.atomic()` a proposito: si se
        escribieran por separado, un fallo entre ambos dejaria un intento trazado sin su transicion
        -o una solicitud cerrada sin rastro de por que- y la auditoria de REQ-135 dejaria de cuadrar.

        DECISION CENTRAL: un fallo NO devuelve la solicitud a `PENDIENTE`, a diferencia de lo que
        hace el despachador de fondo con el resto de avisos. El motivo es el mismo que justifica todo
        este modulo: el cuerpo NO esta persistido, porque lleva la credencial en claro. El despachador
        de fondo solo sabe reenviar el contenido CONGELADO de la fila, de modo que reprogramar un
        reintento solo conseguiria que encontrase la solicitud sin contenido y la cerrara como
        `COMPOSICION_INCOMPLETA`: un reintento que no puede prosperar jamas. La via de reintento
        correcta de REQ-038 (AC-USR-04) y de REQ-073 es REEMITIR la credencial -una emision nueva, con
        un `discriminante` nuevo, que encola una solicitud distinta sin duplicar el registro del
        usuario-, nunca reenviar esta. Por eso el cierre es `FALLIDO`, que es el estado honesto: sin
        continuacion automatica y visible para el ADMINISTRADOR en las vistas de EP-047 y EP-048.

        Args:
            aviso: la solicitud tomada en la fase 1, en `ENVIANDO`.
            resultado: desenlace que devolvio el transporte (o el `CONFIG_ERROR` sintetico si no lo hay).
            snapshot: foto JSON del destinatario del intento.
            recuento: numero de destinatarios del intento (siempre 1 en los avisos de credencial).
            datos: datos del aviso, de donde salen el tipo y el literal del 502.

        Returns:
            ResultadoEntregaCredencial: entregado o no entregado, nunca omitido.

        Raises:
            TransicionAvisoNoPermitidaError: si el estado de la solicitud no admite el cierre.
            ErrorMotorAvisos: si el transporte dijo `entregado` sin publicar `message_id`.
        """

        notification_id = str(aviso.pk)
        repositorio = self._resolver_repositorio()

        # Misma regla que `MotorAvisos._resultado_de_intento` (es privado, asi que se replica): un
        # codigo ausente o ajeno al catalogo cerrado `RESULTADOS_INTENTO` se trata como
        # `TRANSIENT_ERROR`, que es el lado seguro y evita tumbar el INSERT del intento con la CHECK
        # `ck_aviso_intento_result`.
        codigo = (resultado.error_code or "").strip().upper()
        result_code = RESULTADO_ENVIADO if resultado.entregado else (codigo if codigo in RESULTADOS_INTENTO else RESULTADO_TRANSITORIO)

        with transaction.atomic():
            # El contador sube en memoria y lo persiste el UPDATE del cierre, que es lo que espera
            # `registrar_intento` para numerar el intento (`uk_aviso_intento_correlativo`).
            repositorio.incrementar_intento(aviso)
            repositorio.registrar_intento(
                aviso,
                result_code=result_code,
                smtp_response_code=resultado.smtp_response_code,
                error_code=resultado.error_code,
                error_message=resultado.error_message,
                recipients_snapshot=snapshot,
                recipient_count=recuento,
                message_id=resultado.message_id,
            )
            if resultado.entregado:
                repositorio.marcar_enviado(aviso, message_id=resultado.message_id)
            else:
                repositorio.marcar_fallido(
                    aviso,
                    last_error_code=resultado.error_code,
                    last_error_message=resultado.error_message,
                )

        # Trazas con IDENTIFICADORES y codigos unicamente: ni la credencial, ni el cuerpo, ni la
        # direccion corporativa del destinatario (REQ-063, REQ-076, REQ-079).
        traza = {
            "notification_id": notification_id,
            "notification_type": datos.notification_type,
            "recipient_user_id": datos.user_id,
            "status": aviso.status,
            "result": result_code,
            "smtp_response_code": resultado.smtp_response_code,
            "message_id": resultado.message_id,
        }
        if resultado.entregado:
            logger.info(TRAZA_ENTREGADO, extra={"data": traza})
            return ResultadoEntregaCredencial.entregado_con(notification_id=notification_id, message_id=resultado.message_id)

        logger.warning(TRAZA_NO_ENTREGADO, extra={"data": traza})
        return ResultadoEntregaCredencial.no_entregado(
            notification_id=notification_id,
            codigo_error=resultado.error_code,
            mensaje_usuario=MENSAJE_502_POR_TIPO[datos.notification_type],
        )


def servicio_entrega_credencial() -> ServicioEntregaCredencial:
    """
    Punto de entrada de PRODUCCION del aviso de credencial: el servicio con sus colaboradores reales.

    Es el UNICO punto que cablean el alta de usuario (EP-007, REQ-038) y el restablecimiento de
    contrasena (EP-017, REQ-073), de modo que ninguno de los dos instancia la clase a mano ni decide
    que transporte usar. Todos los colaboradores quedan sin resolver: el repositorio, el outbox, la
    configuracion y el transporte se construyen en su primer uso, ya con `django.setup()` hecho.

    Aqui NO existe ningun modo simulado ni de pruebas: si no hay configuracion SMTP activa, el
    transporte es `None` y la entrega se cierra como `FALLIDO` con un intento `CONFIG_ERROR`. Los
    dobles se inyectan por constructor desde las pruebas, nunca desde esta fabrica.

    Returns:
        ServicioEntregaCredencial: servicio listo para `encolar` (fase 1) y `entregar` (fase 2).
    """

    return ServicioEntregaCredencial()
