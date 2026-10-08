"""
Composicion del contenido del aviso de alta de incidencia `NEW_INCIDENT_ALERT` (REQ-131, AC-AVI-03, AC-AVI-04).

DOS CAPAS EN UN MISMO MODULO, Y LA DE ARRIBA ES PURA
====================================================
La PRIMERA mitad del fichero es la CAPA DE RENDER: convierte un puñado de datos ya resueltos
(`DatosAvisoAlta`) en el asunto y los cuerpos del correo (`ContenidoAviso`). No toca el ORM, no lee
`settings` y no consulta el reloj: todo lo que necesita viaja en el argumento. Esa pureza no es
estetica, es lo que permite verificar el contenido del correo -que es el comportamiento que piden
AC-AVI-03 y AC-AVI-04- sin base de datos y sin servidor SMTP.

La SEGUNDA mitad (`configuracion_composicion`, `url_detalle_incidencia`, `etiqueta_de_catalogo`,
`datos_de_incidencia` y `CompositorAvisoAlta`) es la CAPA DE LECTURA: es la unica que habla con el
ORM y con `settings`, y su trabajo termina justo donde empieza la de arriba, construyendo el
`DatosAvisoAlta`. `CompositorAvisoAlta` es la implementacion del puerto `CompositorAviso` que el
despachador invoca, y NUNCA propaga una excepcion: devuelve `False` y deja traza, para que una
solicitud irredactable se cierre como `COMPOSE_ERROR` sin detener el lote.

Los modelos del ORM se importan DENTRO de los metodos, nunca a nivel de modulo: el arranque arrastra
este paquete antes de que el registro de apps este listo. Es la misma disciplina del despachador.

El resultado es un `ContenidoAviso`, que encaja campo a campo con `MensajeCorreo` del transporte
(`apps.avisos.motor.transporte`): asunto, cuerpo de texto y alternativa HTML. Aqui NO se resuelven
destinatarios: el aviso de alta va al equipo de mantenimiento y esa resolucion es del despachador.

TODO EL TEXTO VISIBLE, EN ESPANOL (REQ-050)
===========================================
Las etiquetas y frases que viajan al buzon estan escritas en espanol correcto, CON acentos, y viven
como constantes arriba del modulo para que el texto del correo se pueda leer y revisar de un
vistazo. Los comentarios y docstrings siguen la convencion del repositorio y van sin acentos.

LA FOTO NO VIAJA EN EL CORREO (REQ-131)
=======================================
La incidencia puede tener una fotografia asociada, pero el correo NO la adjunta: un adjunto binario
en cada aviso multiplica el tamaño de la cola, puede rebotar por limites del servidor y saca del
perimetro de la aplicacion un fichero que ya esta custodiado. Cuando `has_photo` es cierto, el
cuerpo lo DICE explicitamente y remite al detalle. El enlace al detalle en la SPA
(`incident_detail_url`) aparece SIEMPRE, haya foto o no, porque es el unico camino para ver la
incidencia completa desde el correo.

DATOS PERSONALES: SOLO NOMBRE Y CORREO DEL REPORTANTE (REQ-049)
===============================================================
El cuerpo publica exclusivamente el nombre y el correo corporativo de quien reporta. No hay
telefono, ni DNI, ni identificador de usuario, ni ningun otro dato de la persona, y el modulo no
tiene forma de añadirlos porque `DatosAvisoAlta` no los transporta: el contrato de entrada es el
limite tecnico del dato que puede acabar en un buzon.

EL HTML SE ESCAPA ENTERO
========================
La descripcion la escribe un usuario final. Si se interpolara tal cual en el cuerpo HTML, cualquier
`<img onerror=...>` escrito en el formulario de alta se convertiria en marcado dentro del correo del
equipo de mantenimiento. Por eso TODO valor dinamico pasa por `html.escape`, y la URL del detalle
ademas con `quote=True`, que es lo que escapa las comillas dobles que cerrarian el atributo `href`.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist

from apps.avisos.motor.errores import ErrorMotorAvisos
from apps.avisos.motor.outbox import TIPO_ALTA

if TYPE_CHECKING:  # pragma: no cover - solo anotaciones: los modelos no se importan antes de django.setup()
    from apps.core.models import AvisoCorreoEntity, IncidenciaEntity

logger = logging.getLogger(__name__)

#: Ancho maximo del asunto del correo. El asunto compuesto NUNCA lo excede (ver `componer_asunto`).
LONGITUD_MAXIMA_ASUNTO: int = 200

#: Ancho maximo del extracto de descripcion que viaja al cuerpo (REQ-131).
LONGITUD_MAXIMA_DESCRIPCION: int = 500

#: Marca de texto recortado. Es U+2026 (HORIZONTAL ELLIPSIS), UN solo caracter, no tres puntos:
#: con tres puntos el recorte consumiria tres posiciones del limite en vez de una.
ELIPSIS: str = "…"

#: Formato de la fecha de alta en el correo. `created_at` es naive en UTC, asi que se nombra la
#: zona en el propio literal para que quien lea el aviso no la interprete como hora local.
FORMATO_FECHA: str = "%d/%m/%Y %H:%M UTC"

#: Prefijo del identificador visible cuando la incidencia todavia no tiene codigo de negocio.
PREFIJO_IDENTIFICADOR: str = "INC-"

#: Expresion regular ACOTADA de validacion de correo: una parte local sin espacios ni arrobas, una
#: arroba, un dominio con al menos un punto y un TLD alfabetico. No pretende implementar RFC 5322
#: (nadie lo hace bien en una linea); pretende rechazar lo que seguro no es una direccion. Las
#: clases son negadas y sin anidamiento, de modo que no hay retroceso exponencial posible.
PATRON_CORREO: re.Pattern[str] = re.compile(r"^[^@\s]{1,64}@[^@\s.]{1,63}(?:\.[^@\s.]{1,63})+\.?[A-Za-z]{0,}$")

# --- Literales que viajan al correo (en espanol correcto, con acentos) ----
PLANTILLA_ASUNTO = "Nueva incidencia {identificador}"
SEPARADOR_ASUNTO = " · "
ENCABEZADO_CUERPO = "Se ha registrado una nueva incidencia."
ETIQUETA_IDENTIFICADOR = "Identificador"
ETIQUETA_SALA = "Sala"
ETIQUETA_OFICINA = "Oficina"
ETIQUETA_CATEGORIA = "Categoría"
ETIQUETA_DESCRIPCION = "Descripción"
ETIQUETA_REPORTANTE = "Reportada por"
ETIQUETA_CORREO_REPORTANTE = "Correo de contacto"
ETIQUETA_FECHA = "Fecha de alta"
ETIQUETA_DETALLE = "Detalle de la incidencia"
TEXTO_ENLACE_DETALLE = "Ver el detalle de la incidencia"
AVISO_FOTO_NO_ADJUNTA = (
    "Esta incidencia tiene una fotografía asociada que no se adjunta a este correo; puede consultarse en el detalle de la incidencia."
)

# --- Mensajes de error (sin acentos, como el resto de mensajes del motor) --
INCIDENCIA_INVALIDA = "El campo «incident_id» debe ser un entero positivo para componer el aviso de alta de incidencia."
CAMPO_OBLIGATORIO = "El campo «{campo}» es obligatorio para componer el aviso de alta de la incidencia «{incidencia}»."
CORREO_REPORTANTE_INVALIDO = (
    "El campo «reporter_email» de la incidencia «{incidencia}» no tiene un formato de correo valido y no se puede componer el aviso."
)
DESCRIPCION_VACIA = "El campo «description» de la incidencia «{incidencia}» queda vacio tras normalizar: el aviso no tendria contenido."
ASUNTO_DEMASIADO_LARGO = (
    "El asunto compuesto para la incidencia «{incidencia}» ocupa {longitud} caracteres y excede el maximo de {maximo}."
)
CONTENIDO_VACIO = "La composicion del aviso de la incidencia «{incidencia}» ha producido un {parte} vacio."


class ErrorComposicionAviso(ErrorMotorAvisos):
    """
    No se puede componer el contenido del aviso de alta con los datos recibidos.

    Hereda de `ErrorMotorAvisos` (y por tanto de `ErrorDominio`) para que el texto en espanol viaje
    en `self.mensaje` y el despachador pueda tratarlo como lo que es: un aviso que no se puede
    redactar y que debe suprimirse con `COMPOSICION_INCOMPLETA`, no un fallo de entrega reintentable.
    """


@dataclass(frozen=True, slots=True)
class DatosAvisoAlta:
    """
    Datos ya resueltos de una incidencia, listos para redactar su aviso de alta.

    Es el CONTRATO DE ENTRADA de la capa de render y, a la vez, el limite de lo que puede acabar en
    un buzon: solo contiene los dos datos personales que REQ-049 admite (nombre y correo corporativo
    del reportante). Quien construye esta estructura a partir del ORM es el segundo incremento de la
    unidad; esta capa no sabe de donde salen los valores.

    `description` es la descripcion ORIGINAL, sin recortar: el recorte es una decision de
    presentacion y se aplica al leer `description_excerpt`, de modo que el dato de entrada no se
    falsea. `created_at` es naive en UTC, igual que el resto de marcas de tiempo del motor.
    """

    incident_id: int
    reference_code: str
    room_name: str
    office_name: str
    category_name: str
    description: str
    reporter_name: str
    reporter_email: str
    created_at: datetime
    has_photo: bool
    incident_detail_url: str

    @property
    def description_excerpt(self) -> str:
        """Descripcion recortada al ancho que admite el cuerpo del aviso (ver `recortar_descripcion`)."""

        return recortar_descripcion(self.description)


@dataclass(frozen=True, slots=True)
class ContenidoAviso:
    """
    Correo de aviso ya redactado: asunto y cuerpos, sin destinatarios y sin remitente.

    Los campos coinciden con los de `MensajeCorreo` (`apps.avisos.motor.transporte`) a proposito:
    el despachador le añade los destinatarios resueltos y lo entrega sin reescribir nada.
    `cuerpo_html` es opcional porque el transporte solo adjunta la alternativa HTML si existe.
    """

    asunto: str
    cuerpo_texto: str
    cuerpo_html: str | None = None


def recortar_descripcion(descripcion: str) -> str:
    """
    Normaliza los espacios laterales de la descripcion y la recorta al ancho del extracto.

    DECISION (la elipsis CUENTA dentro del limite): REQ-131 valida que «`description_excerpt` no
    puede exceder 500 caracteres», asi que el recorte se hace a `LONGITUD_MAXIMA_DESCRIPCION -
    len(ELIPSIS)` y despues se concatena `ELIPSIS`. El resultado de un texto largo mide EXACTAMENTE
    500 caracteres, no 501. La alternativa (cortar a 500 y añadir la marca) devolveria un valor que
    incumple la propia validacion que dice respetar, y lo haria justo en el caso que mas importa.

    Un texto que ya cabe se devuelve tal cual tras normalizar: no se le añade ninguna marca, porque
    la elipsis significa «aqui falta texto» y mentir sobre eso es peor que no marcarlo.

    Args:
        descripcion: descripcion original de la incidencia.

    Returns:
        str: la descripcion normalizada, de 500 caracteres como maximo, con `ELIPSIS` final si se
        ha tenido que recortar.
    """

    texto = (descripcion or "").strip()
    if len(texto) <= LONGITUD_MAXIMA_DESCRIPCION:
        return texto
    return texto[: LONGITUD_MAXIMA_DESCRIPCION - len(ELIPSIS)] + ELIPSIS


def componer_asunto(datos: DatosAvisoAlta) -> str:
    """
    Compone el asunto del aviso: identificador de la incidencia y nombre de la sala (REQ-131).

    DECISION (que identificador viaja al correo): el identificador visible es el CODIGO DE NEGOCIO
    `reference_code`, con formato `INC-AAAA-NNNNNN`, que ya codifica internamente la incidencia. La
    guia del proyecto PROHIBE exponer la clave primaria numerica interna como identificador publico
    en los correos: una PK en el asunto revela el volumen de altas del sistema y ata el texto que ve
    el usuario a un detalle de implementacion de la base de datos. El `incident_id` numerico solo
    viaja dentro de `incident_detail_url`, donde es inevitable porque es la ruta de la SPA. Si la
    incidencia todavia no tiene codigo de negocio informado, se compone uno legible con el prefijo
    `INC-` como ultimo recurso, para que el asunto nunca salga sin identificador.

    DECISION (que se recorta cuando el asunto no cabe): el limite es `LONGITUD_MAXIMA_ASUNTO` y lo
    que se sacrifica es SIEMPRE el nombre de la sala, nunca el identificador. Un asunto truncado por
    el servidor de correo o por el cliente es un asunto que puede perder el identificador; recortando
    aqui, el dato que permite encontrar la incidencia sobrevive y lo que se abrevia es la etiqueta
    descriptiva, cerrada con `ELIPSIS` para que se vea que esta abreviada.

    Args:
        datos: datos de la incidencia.

    Returns:
        str: asunto en espanol, con el identificador y la sala, de 200 caracteres como maximo.
    """

    identificador = (datos.reference_code or "").strip() or f"{PREFIJO_IDENTIFICADOR}{datos.incident_id}"
    cabecera = PLANTILLA_ASUNTO.format(identificador=identificador)
    sala = (datos.room_name or "").strip()

    asunto = f"{cabecera}{SEPARADOR_ASUNTO}{sala}" if sala else cabecera
    if len(asunto) <= LONGITUD_MAXIMA_ASUNTO:
        return asunto

    # Solo se abrevia la sala. `disponible` es lo que queda para el nombre una vez colocados la
    # cabecera con el identificador, el separador y la elipsis de cierre.
    disponible = LONGITUD_MAXIMA_ASUNTO - len(cabecera) - len(SEPARADOR_ASUNTO) - len(ELIPSIS)
    if disponible <= 0:
        # Caso degenerado: el identificador por si solo ya agota el asunto. Se va sin sala.
        return cabecera[:LONGITUD_MAXIMA_ASUNTO]
    return f"{cabecera}{SEPARADOR_ASUNTO}{sala[:disponible]}{ELIPSIS}"


def componer_cuerpo_texto(datos: DatosAvisoAlta) -> str:
    """
    Compone el cuerpo en TEXTO PLANO del aviso de alta (REQ-131, AC-AVI-03).

    Lleva, etiquetado y en espanol: identificador, sala, oficina, categoria, la descripcion YA
    recortada, nombre y correo del reportante, fecha de alta y el enlace al detalle en la SPA. La
    linea de la fotografia aparece UNICAMENTE cuando `has_photo` es cierto, y lo que dice es que la
    foto NO se adjunta y donde verla: anunciar un adjunto que no viaja seria peor que callarlo.

    El texto plano es el cuerpo PRINCIPAL del correo, no un sucedaneo del HTML: es lo que lee quien
    tiene el cliente en modo texto y lo que queda legible en cualquier archivador.

    Args:
        datos: datos de la incidencia.

    Returns:
        str: cuerpo de texto completo, sin marcadores de plantilla pendientes.
    """

    identificador = (datos.reference_code or "").strip() or f"{PREFIJO_IDENTIFICADOR}{datos.incident_id}"
    lineas: list[str] = [
        ENCABEZADO_CUERPO,
        "",
        f"{ETIQUETA_IDENTIFICADOR}: {identificador}",
        f"{ETIQUETA_SALA}: {(datos.room_name or '').strip()}",
        f"{ETIQUETA_OFICINA}: {(datos.office_name or '').strip()}",
        f"{ETIQUETA_CATEGORIA}: {(datos.category_name or '').strip()}",
        f"{ETIQUETA_REPORTANTE}: {(datos.reporter_name or '').strip()}",
        f"{ETIQUETA_CORREO_REPORTANTE}: {(datos.reporter_email or '').strip()}",
        f"{ETIQUETA_FECHA}: {datos.created_at.strftime(FORMATO_FECHA)}",
        "",
        f"{ETIQUETA_DESCRIPCION}:",
        datos.description_excerpt,
    ]
    if datos.has_photo:
        lineas.extend(["", AVISO_FOTO_NO_ADJUNTA])
    lineas.extend(["", f"{ETIQUETA_DETALLE}: {(datos.incident_detail_url or '').strip()}"])
    return "\n".join(lineas)


def componer_cuerpo_html(datos: DatosAvisoAlta) -> str:
    """
    Compone el cuerpo HTML del aviso: la MISMA informacion que el texto plano, con marcado minimo.

    No hay una sola interpolacion sin escapar. Cada valor dinamico pasa por `html.escape` y la URL
    del detalle, ademas, con `quote=True`: sin eso, una comilla doble dentro de la URL cerraria el
    atributo `href` y lo que siguiera se interpretaria como marcado. La descripcion la teclea un
    usuario final, asi que es la entrada hostil por definicion; sus saltos de linea se convierten en
    `<br />` DESPUES de escapar, que es el unico marcado que se le permite generar.

    Args:
        datos: datos de la incidencia.

    Returns:
        str: documento HTML bien formado, completo y autocontenido (sin CSS externo ni imagenes).
    """

    identificador = (datos.reference_code or "").strip() or f"{PREFIJO_IDENTIFICADOR}{datos.incident_id}"
    asunto = html.escape(componer_asunto(datos))
    descripcion = html.escape(datos.description_excerpt).replace("\n", "<br />")
    url = html.escape((datos.incident_detail_url or "").strip(), quote=True)

    filas = "".join(
        f"<li><strong>{html.escape(etiqueta)}:</strong> {html.escape(valor)}</li>"
        for etiqueta, valor in (
            (ETIQUETA_IDENTIFICADOR, identificador),
            (ETIQUETA_SALA, (datos.room_name or "").strip()),
            (ETIQUETA_OFICINA, (datos.office_name or "").strip()),
            (ETIQUETA_CATEGORIA, (datos.category_name or "").strip()),
            (ETIQUETA_REPORTANTE, (datos.reporter_name or "").strip()),
            (ETIQUETA_CORREO_REPORTANTE, (datos.reporter_email or "").strip()),
            (ETIQUETA_FECHA, datos.created_at.strftime(FORMATO_FECHA)),
        )
    )
    bloque_foto = f"<p>{html.escape(AVISO_FOTO_NO_ADJUNTA)}</p>" if datos.has_photo else ""
    return (
        '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8" />'
        f"<title>{asunto}</title></head><body>"
        f"<p>{html.escape(ENCABEZADO_CUERPO)}</p>"
        f"<ul>{filas}</ul>"
        f"<p><strong>{html.escape(ETIQUETA_DESCRIPCION)}:</strong><br />{descripcion}</p>"
        f"{bloque_foto}"
        f'<p><a href="{url}">{html.escape(TEXTO_ENLACE_DETALLE)}</a></p>'
        "</body></html>"
    )


def validar_datos(datos: DatosAvisoAlta) -> None:
    """
    Comprueba que los datos bastan para redactar un aviso con sentido (REQ-131, validaciones).

    Se valida ANTES de componer y no durante: un correo a medio redactar es peor que ningun correo,
    porque llega al equipo de mantenimiento con huecos y nadie puede actuar sobre el. Cada mensaje
    nombra el campo culpable para que el defecto se diagnostique sin depurar.

    Lo que se exige: `incident_id` positivo, sala, categoria, nombre y correo del reportante y
    enlace al detalle no vacios; `reporter_email` con formato de correo; y descripcion no vacia tras
    normalizar. `office_name` y `has_photo` NO son motivo de error: la oficina es una etiqueta
    descriptiva cuya resolucion con respaldo pertenece a la capa de lectura, y `has_photo` solo
    decide si aparece una linea mas.

    Args:
        datos: datos de la incidencia.

    Raises:
        ErrorComposicionAviso: si falta algun obligatorio, el correo no es valido o la descripcion
            queda vacia.
    """

    if not isinstance(datos.incident_id, int) or isinstance(datos.incident_id, bool) or datos.incident_id <= 0:
        raise ErrorComposicionAviso(INCIDENCIA_INVALIDA)

    obligatorios = (
        ("room_name", datos.room_name),
        ("category_name", datos.category_name),
        ("reporter_name", datos.reporter_name),
        ("reporter_email", datos.reporter_email),
        ("incident_detail_url", datos.incident_detail_url),
    )
    for campo, valor in obligatorios:
        if not (valor or "").strip():
            raise ErrorComposicionAviso(CAMPO_OBLIGATORIO.format(campo=campo, incidencia=datos.incident_id))

    if not PATRON_CORREO.match(datos.reporter_email.strip()):
        raise ErrorComposicionAviso(CORREO_REPORTANTE_INVALIDO.format(incidencia=datos.incident_id))

    if not datos.description_excerpt:
        raise ErrorComposicionAviso(DESCRIPCION_VACIA.format(incidencia=datos.incident_id))


def componer_contenido(datos: DatosAvisoAlta) -> ContenidoAviso:
    """
    Redacta el aviso de alta completo: valida, compone asunto y cuerpos y comprueba el resultado.

    Es el UNICO punto de entrada que deberia usar el llamante; `componer_asunto` y los dos cuerpos
    se exponen por separado para poder verificarlos pieza a pieza.

    Las POSTCONDICIONES no son decorativas: el asunto se recorta por construccion, pero si algun dia
    alguien toca la plantilla y rompe ese invariante, el fallo tiene que saltar aqui, en espanol, y
    no en el servidor SMTP con un asunto partido. Lo mismo con un cuerpo vacio: un aviso sin texto no
    se envia, se rechaza.

    Args:
        datos: datos de la incidencia.

    Returns:
        ContenidoAviso: asunto, cuerpo de texto y cuerpo HTML listos para el transporte.

    Raises:
        ErrorComposicionAviso: datos insuficientes (ver `validar_datos`) o resultado invalido.
    """

    validar_datos(datos)

    asunto = componer_asunto(datos)
    cuerpo_texto = componer_cuerpo_texto(datos)
    cuerpo_html = componer_cuerpo_html(datos)

    if len(asunto) > LONGITUD_MAXIMA_ASUNTO:
        raise ErrorComposicionAviso(
            ASUNTO_DEMASIADO_LARGO.format(incidencia=datos.incident_id, longitud=len(asunto), maximo=LONGITUD_MAXIMA_ASUNTO)
        )
    if not asunto.strip():
        raise ErrorComposicionAviso(CONTENIDO_VACIO.format(incidencia=datos.incident_id, parte="asunto"))
    if not cuerpo_texto.strip():
        raise ErrorComposicionAviso(CONTENIDO_VACIO.format(incidencia=datos.incident_id, parte="cuerpo de texto"))

    return ContenidoAviso(asunto=asunto, cuerpo_texto=cuerpo_texto, cuerpo_html=cuerpo_html)


# =========================================================================
# CAPA DE LECTURA DE DATOS (ORM) Y COMPOSITOR DEL PUERTO `CompositorAviso`
# =========================================================================

#: Clave de `settings` que parametriza la composicion del aviso de alta.
CLAVE_SETTINGS: str = "AVISOS_COMPOSICION"

#: Claves dentro de `settings.AVISOS_COMPOSICION`.
CLAVE_URL_BASE: str = "url_base_spa"
CLAVE_RUTA_DETALLE: str = "ruta_detalle_incidencia"

#: Valor por defecto de la direccion base publica de la SPA (gap declarado del RFP, ver
#: `url_detalle_incidencia`). Es el origen del servidor de desarrollo de Angular, NUNCA un host de
#: produccion: en cualquier entorno real lo fija `AVISOS_URL_BASE_SPA`.
URL_BASE_SPA_POR_DEFECTO: str = "http://localhost:4200"

#: Ruta del detalle de una incidencia dentro de la SPA. El unico marcador admitido es
#: `{incident_id}`, que es como la SPA identifica la incidencia en su enrutado.
RUTA_DETALLE_POR_DEFECTO: str = "/incidencias/{incident_id}"

#: Columnas de `aviso_correo` que escribe el compositor. Es un UPDATE acotado a proposito: el
#: compositor no toca estado, intentos, bloqueo ni destinatario, que pertenecen al despachador.
CAMPOS_CONTENIDO: tuple[str, ...] = ("subject", "body_text", "body_html")

#: Nombres de catalogo para la traza de respaldo de etiquetas (REQ-131 validacion 5).
CATALOGO_SALA: str = "sala"
CATALOGO_OFICINA: str = "oficina"
CATALOGO_CATEGORIA: str = "categoria"

# --- Mensajes de error de la capa de lectura (sin acentos) ----------------
SIN_URL_BASE = (
    "No esta disponible la direccion base para construir el enlace al detalle de la incidencia «{incidencia}»: "
    "informe «{clave}[{campo}]» en la configuracion del entorno."
)
RUTA_DETALLE_INVALIDA = (
    "La ruta de detalle «{ruta}» configurada en «{clave}[{campo}]» no se puede formatear: el unico marcador "
    "admitido es «{{incident_id}}»."
)
ETIQUETA_IRRESOLUBLE = (
    "No se puede resolver el campo «{campo}» de la incidencia «{incidencia}»: el catalogo de {catalogo} no "
    "devuelve etiqueta ni codigo y la incidencia tampoco conserva la denominacion del alta."
)

# --- Mensajes de traza (identificadores y longitudes, nunca contenido) ----
TRAZA_ETIQUETA_POR_CODIGO = "Etiqueta de catalogo no resuelta al componer un aviso de alta: se usa el codigo del catalogo"
TRAZA_ETIQUETA_POR_DENOMINACION = (
    "Catalogo inalcanzable al componer un aviso de alta: se usa la denominacion congelada en el alta de la incidencia"
)
TRAZA_COMPUESTO = "Contenido del aviso de alta compuesto y persistido en la solicitud"
TRAZA_TIPO_NO_SOPORTADO = "El compositor del alta no sabe redactar este tipo de aviso: no se compone nada"
TRAZA_SIN_INCIDENCIA = "Solicitud de aviso de alta sin incidencia asociada: no hay nada que redactar"
TRAZA_INCIDENCIA_NO_ENCONTRADA = "La incidencia de la solicitud de aviso de alta ya no existe: el aviso no se puede componer"
TRAZA_COMPOSICION_IMPOSIBLE = "Datos insuficientes para componer el aviso de alta de la incidencia"
TRAZA_FALLO_COMPOSICION = "Fallo inesperado al componer el aviso de alta: la solicitud se dejara sin contenido"


def _texto(valor: object) -> str:
    """Normaliza a cadena recortada cualquier valor del ORM, incluido `None` (que queda en cadena vacia)."""

    if valor is None:
        return ""
    return str(valor).strip()


def _atributo(origen: object | None, nombre: str) -> object | None:
    """
    Devuelve el atributo `nombre` de `origen` (relacion o campo), o `None` si no es alcanzable.

    Las claves ajenas de estos modelos son `DO_NOTHING` contra tablas `managed = False`: una fila de
    catalogo borrada a mano deja la relacion colgando y el acceso al atributo levanta
    `ObjectDoesNotExist`. Aqui ESO NO ES UN ERROR: es justamente el caso que la cadena de respaldo de
    `etiqueta_de_catalogo` tiene que cubrir, asi que se traduce a `None` y la decision se toma alli.
    """

    if origen is None:
        return None
    try:
        return getattr(origen, nombre, None)
    except ObjectDoesNotExist:
        return None


def configuracion_composicion() -> tuple[str, str]:
    """
    Lee de `settings.AVISOS_COMPOSICION` la direccion base de la SPA y la ruta del detalle.

    Se lee en CADA composicion y no se memoriza: el valor es un parametro de despliegue y leerlo de
    `settings` cuesta un acceso a diccionario, de modo que no hay ninguna ganancia en cachearlo y si
    una perdida (un cambio de configuracion no se veria sin reiniciar el proceso).

    Ambas claves tienen un valor por defecto DOCUMENTADO en este modulo, asi que la ausencia de la
    clave en `settings`, un valor vacio o un valor que solo contiene espacios no rompen la
    composicion: se cae al defecto. Un `settings.AVISOS_COMPOSICION` que no sea un mapa se trata como
    ausente, porque tampoco hay forma sensata de interpretarlo.

    Returns:
        tuple[str, str]: la direccion base de la SPA y la ruta del detalle, en ese orden.
    """

    bruto = getattr(settings, CLAVE_SETTINGS, None) or {}
    if not isinstance(bruto, Mapping):
        bruto = {}
    url_base = _texto(bruto.get(CLAVE_URL_BASE)) or URL_BASE_SPA_POR_DEFECTO
    ruta_detalle = _texto(bruto.get(CLAVE_RUTA_DETALLE)) or RUTA_DETALLE_POR_DEFECTO
    return url_base, ruta_detalle


def url_detalle_incidencia(incident_id: int) -> str:
    """
    Compone la URL ABSOLUTA del detalle de la incidencia en la SPA (REQ-131).

    GAP DECLARADO DEL RFP: el RFP no fija la direccion publica de la SPA, y el correo necesita una
    URL absoluta porque se lee fuera del navegador que sirvio la aplicacion (una ruta relativa en un
    buzon no lleva a ninguna parte). No se resuelve esa indefinicion por cuenta propia ni se incrusta
    un host en el codigo: se expone como parametro de entorno (`AVISOS_URL_BASE_SPA` y
    `AVISOS_RUTA_DETALLE_INCIDENCIA`, recogidos en `settings.AVISOS_COMPOSICION`) con un valor por
    defecto documentado, de forma que cada despliegue fije el suyo sin tocar fuente y la decision
    quede visible en la configuracion.

    Tampoco se deriva la base de la peticion HTTP en curso: el aviso se compone en el despachador,
    un proceso de fondo donde NO hay peticion, y confiar en una cabecera `Host` del cliente seria
    ademas un vector de envenenamiento del enlace que viaja en el correo.

    La base se normaliza quitando la barra final y la ruta se garantiza con barra inicial, de modo
    que `http://host/` y `http://host` producen exactamente la misma URL.

    Args:
        incident_id: identificador interno de la incidencia; es el que la SPA usa en su enrutado.

    Returns:
        str: URL absoluta del detalle de la incidencia.

    Raises:
        ErrorComposicionAviso: si la direccion base queda vacia tras normalizar (no hay enlace que
            ofrecer y el aviso no se puede redactar) o si la ruta configurada no se puede formatear.
    """

    url_base, ruta_detalle = configuracion_composicion()
    base = url_base.rstrip("/")
    if not base:
        raise ErrorComposicionAviso(SIN_URL_BASE.format(incidencia=incident_id, clave=CLAVE_SETTINGS, campo=CLAVE_URL_BASE))

    ruta = ruta_detalle if ruta_detalle.startswith("/") else f"/{ruta_detalle}"
    try:
        ruta_resuelta = ruta.format(incident_id=incident_id)
    except (KeyError, IndexError, ValueError) as error:
        raise ErrorComposicionAviso(
            RUTA_DETALLE_INVALIDA.format(ruta=ruta_detalle, clave=CLAVE_SETTINGS, campo=CLAVE_RUTA_DETALLE)
        ) from error
    return f"{base}{ruta_resuelta}"


def etiqueta_de_catalogo(
    elemento: object | None,
    *,
    campo_etiqueta: str,
    campo_codigo: str,
    respaldo: str | None,
    incidencia: int,
    catalogo: str,
) -> str:
    """
    Resuelve una etiqueta de catalogo con la cadena de respaldo de REQ-131 (validacion 5).

    La regla del requisito es explicita: «si no se resuelve la etiqueta se usa el codigo y se deja
    traza, en lugar de emitir el campo vacio». Un correo que dice «Sala: » es peor que uno que dice
    «Sala: SAL-012»: el segundo todavia permite localizar la sala, el primero obliga a entrar en la
    aplicacion para saber de que incidencia se habla. El orden es, por tanto:

    1. ETIQUETA VIGENTE del catalogo (`room_name` / `office_name` / `category_name`), AUNQUE el
       elemento este DESACTIVADO (`is_active = 'N'`). Esto es deliberado y es lo que piden REQ-102 y
       REQ-103: desactivar una sala o una categoria impide darla de alta en NUEVAS incidencias, pero
       las incidencias YA registradas siguen mostrando su etiqueta real. Por eso aqui se usa el
       manager por defecto, que no filtra `is_active`, y no una vista de elementos activos.
    2. CODIGO del catalogo (`room_code` / `office_code` / `category_code`), con traza de aviso.
    3. DENOMINACION CONGELADA en el alta de la incidencia (`*_name_snapshot`), tambien con traza: es
       el ultimo recurso cuando la fila de catalogo ni siquiera es alcanzable.

    La traza nombra la incidencia y el catalogo y NUNCA incluye contenido del correo.

    Args:
        elemento: entidad de catalogo ya cargada, o `None` si no es alcanzable.
        campo_etiqueta: atributo con la etiqueta vigente dentro del elemento de catalogo.
        campo_codigo: atributo con el codigo del elemento de catalogo.
        respaldo: denominacion congelada en el alta de la incidencia.
        incidencia: identificador de la incidencia, solo para la traza y el mensaje de error.
        catalogo: nombre legible del catalogo, solo para la traza y el mensaje de error.

    Returns:
        str: etiqueta no vacia, resuelta por el primer escalon que haya dado valor.

    Raises:
        ErrorComposicionAviso: si fallan los tres escalones; el campo queda nombrado en el mensaje.
    """

    etiqueta = _texto(_atributo(elemento, campo_etiqueta))
    if etiqueta:
        return etiqueta

    datos_traza = {"incident_id": incidencia, "catalogo": catalogo, "campo": campo_etiqueta}

    codigo = _texto(_atributo(elemento, campo_codigo))
    if codigo:
        logger.warning(TRAZA_ETIQUETA_POR_CODIGO, extra={"data": {**datos_traza, "respaldo": campo_codigo}})
        return codigo

    congelada = _texto(respaldo)
    if congelada:
        logger.warning(TRAZA_ETIQUETA_POR_DENOMINACION, extra={"data": {**datos_traza, "respaldo": "snapshot"}})
        return congelada

    raise ErrorComposicionAviso(ETIQUETA_IRRESOLUBLE.format(campo=campo_etiqueta, incidencia=incidencia, catalogo=catalogo))


def datos_de_incidencia(incidencia: "IncidenciaEntity") -> DatosAvisoAlta:
    """
    Traduce una `IncidenciaEntity` YA CARGADA al contrato de entrada de la capa de render.

    Esta es la frontera entre el ORM y la redaccion: a partir de aqui no hay modelos, solo datos. La
    incidencia debe llegar con sus relaciones resueltas (`select_related("room", "room__office",
    "category", "reported_by")`); esta funcion no las vuelve a consultar una a una, que es como se
    cuelan los N+1 en un bucle de despacho.

    La `description` viaja ORIGINAL, sin recortar. El recorte a 500 caracteres es una decision de
    PRESENTACION y vive en `recortar_descripcion`, detras de `DatosAvisoAlta.description_excerpt`:
    adelantarlo aqui falsearia el dato de entrada y haria imposible distinguir, en una prueba, un
    recorte correcto de una descripcion que ya venia corta.

    Del reportante se leen UNICAMENTE el nombre y el correo corporativo (REQ-049). `has_photo` se
    resuelve con un `exists()` sobre los adjuntos: no se descarga ningun fichero ni metadato, solo se
    pregunta si hay alguno, porque la foto NO se adjunta al correo (ver cabecera del modulo).

    Args:
        incidencia: incidencia de la que se redacta el aviso de alta.

    Returns:
        DatosAvisoAlta: datos resueltos, listos para `componer_contenido`.

    Raises:
        ErrorComposicionAviso: si alguna etiqueta de catalogo no se puede resolver por ninguna via o
            si no hay direccion base para construir el enlace al detalle.
    """

    incident_id = incidencia.incident_id
    sala = _atributo(incidencia, "room")
    oficina = _atributo(sala, "office")
    categoria = _atributo(incidencia, "category")
    reportante = _atributo(incidencia, "reported_by")

    return DatosAvisoAlta(
        incident_id=incident_id,
        reference_code=_texto(incidencia.reference_code),
        room_name=etiqueta_de_catalogo(
            sala,
            campo_etiqueta="room_name",
            campo_codigo="room_code",
            respaldo=incidencia.room_name_snapshot,
            incidencia=incident_id,
            catalogo=CATALOGO_SALA,
        ),
        office_name=etiqueta_de_catalogo(
            oficina,
            campo_etiqueta="office_name",
            campo_codigo="office_code",
            respaldo=incidencia.office_name_snapshot,
            incidencia=incident_id,
            catalogo=CATALOGO_OFICINA,
        ),
        category_name=etiqueta_de_catalogo(
            categoria,
            campo_etiqueta="category_name",
            campo_codigo="category_code",
            respaldo=incidencia.category_name_snapshot,
            incidencia=incident_id,
            catalogo=CATALOGO_CATEGORIA,
        ),
        description=incidencia.description or "",
        reporter_name=_texto(_atributo(reportante, "full_name")),
        reporter_email=_texto(_atributo(reportante, "corporate_email")),
        created_at=incidencia.created_at,
        has_photo=incidencia.adjuntos.exists(),
        incident_detail_url=url_detalle_incidencia(incident_id),
    )


class CompositorAvisoAlta:
    """
    Implementacion del puerto `CompositorAviso` (AVI-02) para el aviso de alta de incidencia.

    POR QUE `componer` NO LEVANTA NUNCA
    ===================================
    El despachador invoca este puerto con la solicitud ya tomada, en `ENVIANDO`, y DENTRO del bucle
    del lote. Una excepcion que escapase de aqui dejaria la solicitud colgada hasta la ventana de
    recuperacion y, sobre todo, haria que un dato corrupto de UNA incidencia retrasara el aviso de
    todas las demas. Por eso cualquier fallo se traza y se devuelve `False`: el despachador comprueba
    despues `subject` y `body_text`, los encuentra vacios y cierra la solicitud como `COMPOSE_ERROR`
    con motivo `COMPOSICION_INCOMPLETA`. Ese cierre NO afecta a la incidencia dada de alta ni al
    empleado que la reporto (REQ-131, escenarios de error): el alta ya esta confirmada y el correo es
    una consecuencia asincrona.

    POR QUE AQUI SE PERSISTE (y no se deja al despachador)
    ======================================================
    `RepositorioAvisoCorreo` acota todas sus transiciones con `update_fields` y, a proposito, NUNCA
    reescribe `subject`, `body_text` ni `body_html`: son CLOB y reescribirlos en cada transicion
    costaria E/S por nada. La consecuencia es que, si el compositor se limitara a rellenar los
    atributos en memoria, el contenido viajaria al servidor SMTP pero JAMAS llegaria a la fila, y
    `aviso_correo.subject` / `body_text` -obligatorios en el modelo T.5 de ARC-115 y base de la
    inmutabilidad del contenido de REQ-142 regla 5- quedarian a nulo. Por eso el `save` acotado a
    `CAMPOS_CONTENIDO` forma parte de la composicion, no es un detalle del llamante.

    QUE SE TRAZA
    ============
    Solo identificadores y LONGITUDES. Ni el asunto, ni el cuerpo, ni el correo del reportante, ni el
    destinatario (REQ-063, REQ-076, REQ-079).
    """

    def __init__(self, *, cargar_incidencia: Callable[[int], "IncidenciaEntity"] | None = None) -> None:
        """
        Construye el compositor.

        Args:
            cargar_incidencia: lector alternativo de la incidencia, para pruebas. Por defecto se usa
                el acceso real al ORM. El constructor no tiene argumentos obligatorios porque el
                composition root lo cablea sin configuracion.
        """

        self._cargar_incidencia = cargar_incidencia

    def componer(self, aviso: "AvisoCorreoEntity") -> bool:
        """
        Redacta y persiste el contenido del aviso de alta de una solicitud (REQ-131, AC-AVI-03).

        Orden de decisiones:

        1. Si la solicitud YA trae asunto y cuerpo, se devuelve `True` SIN recomponer: el contenido
           es inmutable una vez compuesto y un reintento lo reutiliza tal cual (REQ-142 regla 5).
        2. Si el tipo no es `NEW_INCIDENT_ALERT`, se devuelve `False` sin tocar nada: la composicion
           de los demas tipos de aviso pertenece a otras tareas y este compositor solo sabe del alta.
        3. Sin incidencia asociada no hay nada que redactar.
        4. La incidencia se carga en UNA sola consulta con sus relaciones (`select_related`).
        5. Se compone, se asignan los tres campos y se persisten con `update_fields` acotado.

        Args:
            aviso: solicitud de aviso tomada por el despachador.

        Returns:
            bool: `True` si la solicitud queda con contenido; `False` en cualquier otro caso, sin
            propagar excepcion alguna.
        """

        from apps.core.models import IncidenciaEntity

        if self._contenido_completo(aviso):
            return True

        if aviso.notification_type != TIPO_ALTA:
            logger.debug(
                TRAZA_TIPO_NO_SOPORTADO,
                extra={"data": {"notification_id": str(aviso.pk), "notification_type": aviso.notification_type}},
            )
            return False

        incident_id = aviso.incident_id
        if incident_id is None:
            logger.warning(TRAZA_SIN_INCIDENCIA, extra={"data": {"notification_id": str(aviso.pk)}})
            return False

        datos_traza = {"notification_id": str(aviso.pk), "incident_id": incident_id}
        try:
            incidencia = self._leer_incidencia(incident_id)
            contenido = componer_contenido(datos_de_incidencia(incidencia))

            aviso.subject = contenido.asunto
            aviso.body_text = contenido.cuerpo_texto
            aviso.body_html = contenido.cuerpo_html
            aviso.save(update_fields=list(CAMPOS_CONTENIDO))
        except IncidenciaEntity.DoesNotExist:
            logger.warning(TRAZA_INCIDENCIA_NO_ENCONTRADA, extra={"data": datos_traza})
            return False
        except ErrorComposicionAviso as error:
            logger.warning(TRAZA_COMPOSICION_IMPOSIBLE, extra={"data": {**datos_traza, "motivo": error.mensaje}})
            return False
        except Exception:  # noqa: BLE001 - el despachador cierra la solicitud, el lote no se detiene
            logger.exception(TRAZA_FALLO_COMPOSICION, extra={"data": datos_traza})
            return False

        # Longitudes, nunca contenido: basta para auditar que el correo salio completo.
        logger.info(
            TRAZA_COMPUESTO,
            extra={
                "data": {
                    **datos_traza,
                    "longitud_asunto": len(contenido.asunto),
                    "longitud_cuerpo": len(contenido.cuerpo_texto),
                }
            },
        )
        return True

    def _leer_incidencia(self, incident_id: int) -> "IncidenciaEntity":
        """
        Carga la incidencia con sus relaciones en UNA consulta (`select_related`).

        Los modelos se importan DENTRO del metodo, nunca a nivel de modulo: el arranque arrastra este
        paquete antes de que el registro de apps este listo y un import de modelos arriba reventaria
        con `AppRegistryNotReady`. Es la misma disciplina que sigue el despachador.

        El manager por defecto NO filtra `is_active` en los catalogos relacionados, y eso es
        deliberado: una sala o una categoria desactivada debe seguir resolviendo su etiqueta en los
        avisos de incidencias ya registradas (REQ-102, REQ-103).
        """

        from apps.core.models import IncidenciaEntity

        if self._cargar_incidencia is not None:
            return self._cargar_incidencia(incident_id)
        return IncidenciaEntity.objects.select_related("room", "room__office", "category", "reported_by").get(pk=incident_id)

    @staticmethod
    def _contenido_completo(aviso: "AvisoCorreoEntity") -> bool:
        """Indica si la solicitud ya trae asunto y cuerpo de texto, que es el contenido minimo entregable."""

        return bool(_texto(aviso.subject)) and bool(_texto(aviso.body_text))


def compositor_por_defecto() -> CompositorAvisoAlta:
    """
    Devuelve el compositor de produccion del aviso de alta.

    Es el punto unico que cablea el composition root del motor (`MotorAvisos(compositor=...)`), de
    modo que el resto del codigo no instancia la clase directamente y cambiar la implementacion
    -o envolverla- se hace en un solo sitio.
    """

    return CompositorAvisoAlta()


__all__ = [
    "ASUNTO_DEMASIADO_LARGO",
    "AVISO_FOTO_NO_ADJUNTA",
    "CAMPOS_CONTENIDO",
    "CAMPO_OBLIGATORIO",
    "CATALOGO_CATEGORIA",
    "CATALOGO_OFICINA",
    "CATALOGO_SALA",
    "CLAVE_RUTA_DETALLE",
    "CLAVE_SETTINGS",
    "CLAVE_URL_BASE",
    "CONTENIDO_VACIO",
    "CORREO_REPORTANTE_INVALIDO",
    "DESCRIPCION_VACIA",
    "ELIPSIS",
    "ENCABEZADO_CUERPO",
    "ETIQUETA_CATEGORIA",
    "ETIQUETA_CORREO_REPORTANTE",
    "ETIQUETA_DESCRIPCION",
    "ETIQUETA_DETALLE",
    "ETIQUETA_FECHA",
    "ETIQUETA_IDENTIFICADOR",
    "ETIQUETA_IRRESOLUBLE",
    "ETIQUETA_OFICINA",
    "ETIQUETA_REPORTANTE",
    "ETIQUETA_SALA",
    "FORMATO_FECHA",
    "INCIDENCIA_INVALIDA",
    "LONGITUD_MAXIMA_ASUNTO",
    "LONGITUD_MAXIMA_DESCRIPCION",
    "PATRON_CORREO",
    "PLANTILLA_ASUNTO",
    "PREFIJO_IDENTIFICADOR",
    "RUTA_DETALLE_INVALIDA",
    "RUTA_DETALLE_POR_DEFECTO",
    "SEPARADOR_ASUNTO",
    "SIN_URL_BASE",
    "TEXTO_ENLACE_DETALLE",
    "TRAZA_COMPOSICION_IMPOSIBLE",
    "TRAZA_COMPUESTO",
    "TRAZA_ETIQUETA_POR_CODIGO",
    "TRAZA_ETIQUETA_POR_DENOMINACION",
    "TRAZA_FALLO_COMPOSICION",
    "TRAZA_INCIDENCIA_NO_ENCONTRADA",
    "TRAZA_SIN_INCIDENCIA",
    "TRAZA_TIPO_NO_SOPORTADO",
    "URL_BASE_SPA_POR_DEFECTO",
    "CompositorAvisoAlta",
    "ContenidoAviso",
    "DatosAvisoAlta",
    "ErrorComposicionAviso",
    "componer_asunto",
    "componer_contenido",
    "componer_cuerpo_html",
    "componer_cuerpo_texto",
    "compositor_por_defecto",
    "configuracion_composicion",
    "datos_de_incidencia",
    "etiqueta_de_catalogo",
    "recortar_descripcion",
    "url_detalle_incidencia",
    "validar_datos",
]
