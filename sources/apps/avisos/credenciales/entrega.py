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
import logging
from dataclasses import dataclass
from datetime import datetime

from apps.avisos.alta.composicion import configuracion_composicion
from apps.avisos.motor.errores import ErrorMotorAvisos
from apps.avisos.motor.outbox import TIPO_CREDENCIAL_EMITIDA, TIPO_RESTABLECIMIENTO, TIPOS_CREDENCIAL

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
