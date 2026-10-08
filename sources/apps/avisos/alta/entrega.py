"""
Entrega del aviso de alta al equipo de mantenimiento en copia oculta (ARC-014, REQ-133, REQ-135).

POR QUE LOS DESTINATARIOS SE RESUELVEN AQUI Y NO AL ENCOLAR
===========================================================
`alta/disparo.py` encola la solicitud `NEW_INCIDENT_ALERT` con `recipient_email` a NULO a proposito,
y este modulo es quien cierra ese hueco. Resolver el colectivo dentro de la transaccion del alta
tendria dos defectos graves: ataria el alta de una incidencia a que el directorio responda (un fallo
de lectura abortaria una operacion de negocio que ya es valida) y, sobre todo, CONGELARIA la lista de
destinatarios en el instante del alta. Entre el alta y la entrega -que puede ocurrir minutos despues,
o varios reintentos mas tarde- un tecnico puede haber sido dado de baja. REQ-089 exige que una cuenta
desactivada deje de recibir avisos, asi que la unica resolucion correcta es la que se hace en el
INSTANTE DE LA ENTREGA, que es exactamente lo que hace `entregar()`.

La resolucion NO se reimplementa: se delega integra en `ServicioResolucionDestinatarios`
(REQ-082/REQ-089), que ya filtra por rol `TECNICO_MANTENIMIENTO`, por estado ACTIVO y por correo
notificable, y que ya deja su propia traza auditable. Aqui no se vuelve a filtrar por rol ni por
estado: duplicar ese criterio significaria tener dos definiciones del colectivo que se separarian con
el primer cambio de requisito.

POR QUE LA FOTO Y EL SOBRE TIENEN QUE SER EL MISMO CONJUNTO
===========================================================
REQ-135 obliga a escribir en cada intento un `recipients_snapshot` con el identificador y el correo de
cada destinatario, y AC-SMTP-02 exige que la direccion de un tecnico desactivado no aparezca NI en el
mensaje NI en ese snapshot. Por eso el snapshot no se construye con lo que devolvio la resolucion,
sino con los destinatarios cuya direccion SOBREVIVE a `validar_direcciones`: la misma funcion, con el
mismo criterio y la misma deduplicacion que despues usa el cliente SMTP para formar el sobre. Si el
snapshot se construyera antes de validar, la fila del intento afirmaria haber escrito a alguien a
quien nunca se escribio, y la auditoria de REQ-135 dejaria de ser prueba de nada.

SIN DESTINATARIOS Y SIN CONFIGURACION: DOS DESENLACES DISTINTOS
===============================================================
Conjunto vacio es `NO_RECIPIENTS` y NO se toca el servidor SMTP; falta de `smtp_host` o de
`from_address` es `CONFIG_ERROR` y tampoco se intenta conexion (REQ-133, reglas 3 y 6). Son codigos
distintos porque describen problemas distintos: el primero es una situacion de negocio (no hay
tecnicos a quien avisar), el segundo es un despliegue incompleto. En el caso de `CONFIG_ERROR` el
snapshot SI viaja, porque los destinatarios ya estaban resueltos y la fila del intento debe dejar
constancia de a quien se habria escrito.

Un directorio momentaneamente inalcanzable (`ResolucionColectivo.degradado`) NO es "no hay tecnicos":
se traduce a `TRANSIENT_ERROR` para que el aviso se reintente, nunca a `NO_RECIPIENTS`, que es un
desenlace definitivo y cerraria el aviso sin que nadie lo reciba.

SECRETOS Y DATO PERSONAL (REQ-076 / AC-SMTP-11, REQ-079)
========================================================
Ninguna credencial SMTP aparece en este modulo: no se leen, no se trazan y no se persisten. En el log
solo viajan identificadores, recuentos y codigos de resultado; jamas una direccion en claro (las
descartadas se trazan enmascaradas). El unico dato personal que sale de aqui es el que admite
AC-SMTP-11: nombre y correo corporativo, y solo dentro del `recipients_snapshot`.

ESTE METODO NO LANZA
====================
`entregar()` replica el contrato del despachador: cualquier fallo se devuelve como `EntregaColectiva`
con su `ResultadoEntrega`. Una excepcion que escapase de aqui dejaria la solicitud tomada y sin fila
de intento, que es justo el estado que la cola no sabe recuperar.
"""

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from apps.avisos.motor.configuracion import ConfiguracionMotorAvisos, configuracion_motor
from apps.avisos.motor.errores import ErrorMotorAvisos
from apps.avisos.motor.estados import RESULTADOS_INTENTO
from apps.avisos.motor.transporte import ResultadoEntrega, _recortar
from apps.avisos.resolucion import (
    DestinatarioResuelto,
    ResolucionColectivo,
    ResultadoResolucion,
    ServicioResolucionDestinatarios,
)
from apps.avisos.smtp.cliente import (
    RESULTADO_SIN_DESTINATARIOS,
    ClienteSmtpBcc,
    DireccionDescartada,
    cliente_bcc_activo,
    validar_direcciones,
)

if TYPE_CHECKING:  # pragma: no cover - solo anotaciones: los modelos no se importan antes de django.setup()
    from apps.core.models import AvisoCorreoEntity

logger = logging.getLogger(__name__)


def _resultado_del_catalogo(codigo: str) -> str:
    """Devuelve el codigo comprobando que pertenece a `RESULTADOS_INTENTO` (CHECK `ck_aviso_intento_resultado`)."""

    if codigo not in RESULTADOS_INTENTO:
        catalogo = ", ".join(sorted(RESULTADOS_INTENTO))
        raise ErrorMotorAvisos(f"El resultado de intento «{codigo}» no pertenece al catalogo cerrado: {catalogo}.")
    return codigo


#: Modulo consumidor que queda en la traza de resolucion.
MODULO_ENTREGA: str = "ARC-014"

#: Resultado de intento cuando la solicitud llega sin contenido redactado (la composicion es previa).
RESULTADO_COMPOSICION: str = _resultado_del_catalogo("COMPOSE_ERROR")

#: Resultado de intento cuando no queda ningun destinatario al que entregar (REQ-133, regla 3).
RESULTADO_NO_RECIPIENTS: str = _resultado_del_catalogo(RESULTADO_SIN_DESTINATARIOS)

#: Resultado de intento cuando falta configuracion SMTP activa y completa (REQ-133, regla 6).
RESULTADO_CONFIGURACION: str = _resultado_del_catalogo("CONFIG_ERROR")

#: Resultado de intento de una entrega aceptada por el servidor.
RESULTADO_ENVIADO: str = _resultado_del_catalogo("SENT")

#: Recuento de destinatarios de un intento sin envio. La CHECK `ck_aviso_intento_sin_dest` solo
#: admite `recipient_count = 0` cuando el resultado es `NO_RECIPIENTS`.
SIN_DESTINATARIOS: int = 0

#: Desenlaces de resolucion que NO originan envio: ni hay a quien escribir ni se puede afirmar que lo haya.
RESULTADOS_SIN_ENVIO: frozenset[ResultadoResolucion] = frozenset({ResultadoResolucion.SIN_DESTINATARIOS, ResultadoResolucion.ERROR_TECNICO})

# --- Textos de `error_message` (sin acentos, como el resto del motor) -----
SIN_CONTENIDO = "La solicitud de aviso de alta llega sin asunto o sin cuerpo de texto: no hay nada entregable."
SIN_DESTINATARIOS_RESUELTOS = "La resolucion del equipo de mantenimiento no ha devuelto ningun destinatario notificable."
SIN_DIRECCIONES_VALIDAS = "Ninguna direccion del equipo de mantenimiento ha superado la validacion de formato."
DIRECTORIO_DEGRADADO = "El directorio de destinatarios no esta disponible: la resolucion del equipo de mantenimiento es degradada."
FALLO_RESOLUCION = "Fallo al resolver el equipo de mantenimiento ({tipo}): no se puede determinar a quien entregar el aviso."
SIN_CONFIGURACION_SMTP = "No hay configuracion SMTP activa y completa (smtp_host / from_address): el aviso no se intenta entregar."
FALLO_INESPERADO = "Fallo inesperado al entregar el aviso de alta ({tipo})."

# --- Textos de traza (identificadores y recuentos, nunca direcciones) -----
TRAZA_SIN_CONTENIDO = "Aviso de alta sin contenido redactado: no se intenta entregar"
TRAZA_FALLO_RESOLUCION = "Fallo al resolver el equipo de mantenimiento para entregar el aviso de alta"
TRAZA_SIN_DESTINATARIOS = "El equipo de mantenimiento no tiene destinatarios notificables: el aviso de alta no se entrega"
TRAZA_DIRECTORIO_DEGRADADO = "Resolucion degradada del equipo de mantenimiento: el aviso de alta se reintentara"
TRAZA_DIRECCION_DESCARTADA = "Direccion del equipo de mantenimiento descartada antes de entregar el aviso de alta"
TRAZA_SIN_CONFIGURACION = "Sin configuracion SMTP activa: el aviso de alta no se intenta entregar"
TRAZA_ENTREGADO = "Aviso de alta entregado al equipo de mantenimiento en copia oculta"
TRAZA_NO_ENTREGADO = "El intento de entrega del aviso de alta no ha prosperado"
TRAZA_FALLO_INESPERADO = "Fallo inesperado al entregar el aviso de alta: se registra un intento reintentable"


@dataclass(frozen=True, slots=True)
class EntregaColectiva:
    """
    Desenlace de la entrega del aviso de alta al colectivo, listo para la traza del intento.

    Los tres campos son exactamente lo que necesita la fila append-only de `aviso_correo_intento`
    (REQ-135): el desenlace en el vocabulario del motor, el `recipients_snapshot` serializado y su
    cardinalidad. `recipients_snapshot` queda a `None` cuando no llego a haber destinatarios, y en
    ese caso `recipient_count` es 0, que es el unico valor que la CHECK `ck_aviso_intento_sin_dest`
    admite junto al resultado `NO_RECIPIENTS`.
    """

    resultado: ResultadoEntrega
    recipients_snapshot: str | None
    recipient_count: int


class EntregaAvisoAlta:
    """
    Entrega el aviso `NEW_INCIDENT_ALERT` al equipo de mantenimiento resuelto en el momento del envio.

    Los tres colaboradores se inyectan por constructor para poder verificar el comportamiento sin
    directorio ni servidor SMTP, pero la POLITICA no es sustituible: el orden de las comprobaciones,
    que el snapshot y el sobre sean el mismo conjunto y que no exista ningun camino que finja un
    envio se deciden aqui y en un unico sitio.
    """

    def __init__(
        self,
        *,
        resolucion: ServicioResolucionDestinatarios | None = None,
        config: ConfiguracionMotorAvisos | None = None,
        fabrica_cliente: Callable[..., ClienteSmtpBcc | None] | None = None,
    ) -> None:
        """
        Construye la entrega con sus colaboradores.

        Args:
            resolucion: servicio de resolucion de destinatarios; por defecto, el real.
            config: configuracion del motor, de donde sale el timeout SMTP; por defecto, la vigente.
            fabrica_cliente: fabrica del cliente de copia oculta, invocada con `timeout_segundos`.
                Devuelve `None` cuando no hay configuracion SMTP activa y completa.
        """

        self._resolucion: ServicioResolucionDestinatarios = resolucion or ServicioResolucionDestinatarios()
        self._config: ConfiguracionMotorAvisos = config or configuracion_motor()
        self._fabrica_cliente: Callable[..., ClienteSmtpBcc | None] = fabrica_cliente or cliente_bcc_activo

    def entregar(self, aviso: "AvisoCorreoEntity") -> EntregaColectiva:
        """
        Resuelve el colectivo y entrega el aviso de alta en un unico mensaje en copia oculta.

        NUNCA propaga excepcion: cualquier fallo, incluido uno inesperado, vuelve como
        `EntregaColectiva` para que el llamante escriba su fila de intento y la cola siga viva.

        Args:
            aviso: solicitud de aviso ya tomada y con su contenido redactado.

        Returns:
            EntregaColectiva: desenlace del intento, con el snapshot de destinatarios y su recuento.
        """

        try:
            return self._entregar(aviso)
        except Exception as error:  # noqa: BLE001 - contrato: entregar() no lanza, el lote no se detiene
            logger.exception(TRAZA_FALLO_INESPERADO, extra={"data": {"notification_id": self._identificador(aviso)}})
            return self._sin_envio(ResultadoEntrega.transitorio(FALLO_INESPERADO.format(tipo=type(error).__name__)))

    # --- Camino nominal ---------------------------------------------------

    def _entregar(self, aviso: "AvisoCorreoEntity") -> EntregaColectiva:
        """Camino nominal de `entregar`, en el orden exacto que fija REQ-133; ver cada paso en linea."""

        notification_id = self._identificador(aviso)

        # 1. Guarda de contenido. Redactar es trabajo del compositor y ocurre ANTES; aqui solo se
        #    comprueba, porque entregar un correo sin asunto o sin cuerpo seria peor que no enviarlo.
        asunto = _texto(aviso.subject)
        cuerpo_texto = _texto(aviso.body_text)
        if not asunto or not cuerpo_texto:
            logger.warning(
                TRAZA_SIN_CONTENIDO,
                extra={"data": {"notification_id": notification_id, "result": RESULTADO_COMPOSICION}},
            )
            return self._sin_envio(
                ResultadoEntrega(entregado=False, error_code=RESULTADO_COMPOSICION, error_message=_recortar(SIN_CONTENIDO))
            )

        # 2. Resolucion en el instante de la entrega (REQ-082/REQ-089), delegada por completo.
        try:
            resolucion = self._resolucion.resolver_equipo_mantenimiento(modulo=MODULO_ENTREGA)
        except Exception as error:  # noqa: BLE001 - un directorio caido es reintentable, no un fallo del aviso
            logger.exception(TRAZA_FALLO_RESOLUCION, extra={"data": {"notification_id": notification_id}})
            # Solo el TIPO de la excepcion viaja al mensaje del intento: el texto podria arrastrar
            # datos del directorio y `error_message` se persiste y se consulta (REQ-079).
            return self._sin_envio(ResultadoEntrega.transitorio(FALLO_RESOLUCION.format(tipo=type(error).__name__)))

        # 3. Conjunto vacio: no se toca SMTP en absoluto.
        if self._sin_colectivo(resolucion):
            return self._sin_colectivo_resuelto(resolucion, notification_id=notification_id)

        # 4. Validacion de direcciones y foto de destinatarios. El snapshot se construye con los
        #    destinatarios cuya direccion SOBREVIVE, de modo que foto y sobre son el mismo conjunto.
        destinatarios = self._destinatarios_entregables(resolucion, notification_id=notification_id)
        if not destinatarios:
            logger.warning(
                TRAZA_SIN_DESTINATARIOS,
                extra={
                    "data": {
                        "notification_id": notification_id,
                        "outcome": resolucion.outcome.value,
                        "result": RESULTADO_NO_RECIPIENTS,
                        "recipient_count": SIN_DESTINATARIOS,
                    }
                },
            )
            return self._sin_envio(
                ResultadoEntrega(
                    entregado=False,
                    error_code=RESULTADO_NO_RECIPIENTS,
                    error_message=_recortar(SIN_DIRECCIONES_VALIDAS),
                )
            )

        snapshot = componer_snapshot(destinatarios)
        direcciones = tuple(_texto(destinatario.corporate_email) for destinatario in destinatarios)
        recuento = len(destinatarios)

        # 5. Configuracion SMTP. Sin host o sin remitente NO hay intento de conexion (REQ-133, regla
        #    6), pero el snapshot SI viaja: la fila del intento debe decir a quien se habria escrito.
        cliente = self._fabrica_cliente(timeout_segundos=self._config.smtp_timeout_segundos)
        if cliente is None:
            logger.warning(
                TRAZA_SIN_CONFIGURACION,
                extra={"data": {"notification_id": notification_id, "recipient_count": recuento, "result": RESULTADO_CONFIGURACION}},
            )
            return EntregaColectiva(
                resultado=ResultadoEntrega.de_configuracion(SIN_CONFIGURACION_SMTP),
                recipients_snapshot=snapshot,
                recipient_count=recuento,
            )

        # 6. Un unico mensaje con TODOS los destinatarios en copia oculta (AC-SMTP-01).
        resultado = cliente.enviar(
            asunto=asunto,
            cuerpo_texto=cuerpo_texto,
            destinatarios=direcciones,
            cuerpo_html=aviso.body_html or None,
        )
        self._trazar_desenlace(resultado, notification_id=notification_id, recuento=recuento)
        return EntregaColectiva(resultado=resultado, recipients_snapshot=snapshot, recipient_count=recuento)

    # --- Piezas del camino nominal ---------------------------------------

    @staticmethod
    def _sin_colectivo(resolucion: ResolucionColectivo) -> bool:
        """Indica si la resolucion no origina envio: sin destinatarios, sin desenlace util o no entregable."""

        return not resolucion.hay_destinatarios or not resolucion.debe_enviarse or resolucion.outcome in RESULTADOS_SIN_ENVIO

    def _sin_colectivo_resuelto(self, resolucion: ResolucionColectivo, *, notification_id: str) -> EntregaColectiva:
        """
        Traduce una resolucion que no origina envio, SIN tocar el servidor SMTP.

        DISTINCION DELIBERADA: una resolucion `degradado` significa que el directorio no se pudo leer
        con garantias, no que el equipo de mantenimiento este vacio. Cerrarla como `NO_RECIPIENTS`
        -que es un desenlace DEFINITIVO, sin reintento- daria por imposible un aviso que probablemente
        si tiene destinatarios, asi que se devuelve `TRANSIENT_ERROR` y el aviso vuelve a la cola.
        """

        datos = {"notification_id": notification_id, "outcome": resolucion.outcome.value, "degradado": resolucion.degradado}
        if resolucion.degradado:
            logger.warning(TRAZA_DIRECTORIO_DEGRADADO, extra={"data": datos})
            return self._sin_envio(ResultadoEntrega.transitorio(DIRECTORIO_DEGRADADO))

        logger.warning(
            TRAZA_SIN_DESTINATARIOS,
            extra={"data": {**datos, "result": RESULTADO_NO_RECIPIENTS, "recipient_count": SIN_DESTINATARIOS}},
        )
        return self._sin_envio(
            ResultadoEntrega(
                entregado=False,
                error_code=RESULTADO_NO_RECIPIENTS,
                error_message=_recortar(SIN_DESTINATARIOS_RESUELTOS),
            )
        )

    def _destinatarios_entregables(self, resolucion: ResolucionColectivo, *, notification_id: str) -> tuple[DestinatarioResuelto, ...]:
        """
        Devuelve los destinatarios cuya direccion supera `validar_direcciones`, en el orden resuelto.

        La validacion se ejecuta sobre `resolucion.direcciones` -la MISMA funcion que aplica despues
        el cliente SMTP- y el resultado se proyecta de vuelta sobre los `DestinatarioResuelto`. Asi la
        foto de REQ-135 y el sobre del mensaje contienen exactamente las mismas personas (AC-SMTP-02),
        sin reimplementar aqui ningun criterio de validez ni de deduplicacion.
        """

        validas, descartadas = validar_direcciones(resolucion.direcciones)
        self._trazar_descartadas(descartadas, notification_id=notification_id)

        admitidas = {direccion.casefold() for direccion in validas}
        entregables: list[DestinatarioResuelto] = []
        vistas: set[str] = set()
        for destinatario in resolucion.destinatarios:
            clave = _texto(destinatario.corporate_email).casefold()
            if clave not in admitidas or clave in vistas:
                continue
            vistas.add(clave)
            entregables.append(destinatario)
        return tuple(entregables)

    def _trazar_descartadas(self, descartadas: Sequence[DireccionDescartada], *, notification_id: str) -> None:
        """Traza cada direccion descartada SIEMPRE enmascarada: un correo corporativo es dato personal (REQ-079)."""

        for descartada in descartadas:
            logger.warning(
                TRAZA_DIRECCION_DESCARTADA,
                extra={
                    "data": {
                        "notification_id": notification_id,
                        "direccion": descartada.enmascarada,
                        "motivo": descartada.motivo,
                        "descartadas": len(descartadas),
                    }
                },
            )

    @staticmethod
    def _trazar_desenlace(resultado: ResultadoEntrega, *, notification_id: str, recuento: int) -> None:
        """Traza el desenlace del intento con identificadores y recuentos; nunca con direcciones ni credenciales."""

        datos = {
            "notification_id": notification_id,
            "recipient_count": recuento,
            "result": RESULTADO_ENVIADO if resultado.entregado else (resultado.error_code or ""),
            "smtp_response_code": resultado.smtp_response_code,
        }
        if resultado.entregado:
            logger.info(TRAZA_ENTREGADO, extra={"data": datos})
        else:
            logger.warning(TRAZA_NO_ENTREGADO, extra={"data": datos})

    @staticmethod
    def _sin_envio(resultado: ResultadoEntrega) -> EntregaColectiva:
        """Desenlace sin destinatarios conocidos: ni snapshot ni recuento, que es lo que exige la CHECK del intento."""

        return EntregaColectiva(resultado=resultado, recipients_snapshot=None, recipient_count=SIN_DESTINATARIOS)

    @staticmethod
    def _identificador(aviso: "AvisoCorreoEntity") -> str:
        """Identificador de la solicitud para la traza; es un UUID y viaja siempre como texto."""

        return str(getattr(aviso, "notification_id", "") or "")


def componer_snapshot(destinatarios: Sequence[DestinatarioResuelto]) -> str:
    """
    Serializa la foto de destinatarios del intento (REQ-135, validacion 7).

    El formato es un ARRAY JSON con un objeto por destinatario y EXACTAMENTE tres claves:
    `user_id`, `full_name` y `corporate_email`. No se incluye el rol, ni el estado de la cuenta, ni
    ningun otro atributo: AC-SMTP-11 limita el dato personal que puede salir de aqui al nombre y al
    correo corporativo, y la columna se consulta en auditoria. `ensure_ascii=False` conserva los
    nombres con acentos tal cual, de modo que la columna -con su CHECK `IS JSON`- guarda texto
    legible y no secuencias de escape.

    Args:
        destinatarios: destinatarios YA validados que forman el sobre del mensaje.

    Returns:
        str: documento JSON valido, en el mismo orden en que se resolvieron los destinatarios.
    """

    return json.dumps(
        [
            {
                "user_id": destinatario.user_id,
                "full_name": _texto(destinatario.full_name),
                "corporate_email": _texto(destinatario.corporate_email),
            }
            for destinatario in destinatarios
        ],
        ensure_ascii=False,
    )


def entrega_alta_por_defecto() -> EntregaAvisoAlta:
    """
    Devuelve la entrega de PRODUCCION del aviso de alta, con sus colaboradores reales.

    Es el punto unico que cablea el composition root del motor, de modo que ningun otro modulo
    instancie la clase a mano. No existe aqui ningun modo simulado: si la configuracion SMTP no esta
    activa, `cliente_bcc_activo` devuelve `None` y la respuesta es `CONFIG_ERROR`, nunca un envio
    fingido que marcaria como ENVIADO un aviso que nadie recibio.
    """

    return EntregaAvisoAlta(
        resolucion=ServicioResolucionDestinatarios(),
        config=configuracion_motor(),
        fabrica_cliente=cliente_bcc_activo,
    )


def _texto(valor: object) -> str:
    """Normaliza a cadena recortada cualquier valor, incluido `None` (que queda en cadena vacia)."""

    if valor is None:
        return ""
    return str(valor).strip()


__all__ = [
    "DIRECTORIO_DEGRADADO",
    "FALLO_INESPERADO",
    "FALLO_RESOLUCION",
    "MODULO_ENTREGA",
    "RESULTADOS_SIN_ENVIO",
    "RESULTADO_COMPOSICION",
    "RESULTADO_CONFIGURACION",
    "RESULTADO_ENVIADO",
    "RESULTADO_NO_RECIPIENTS",
    "SIN_CONFIGURACION_SMTP",
    "SIN_CONTENIDO",
    "SIN_DESTINATARIOS",
    "SIN_DESTINATARIOS_RESUELTOS",
    "SIN_DIRECCIONES_VALIDAS",
    "TRAZA_DIRECCION_DESCARTADA",
    "TRAZA_DIRECTORIO_DEGRADADO",
    "TRAZA_ENTREGADO",
    "TRAZA_FALLO_INESPERADO",
    "TRAZA_FALLO_RESOLUCION",
    "TRAZA_NO_ENTREGADO",
    "TRAZA_SIN_CONFIGURACION",
    "TRAZA_SIN_CONTENIDO",
    "TRAZA_SIN_DESTINATARIOS",
    "EntregaAvisoAlta",
    "EntregaColectiva",
    "componer_snapshot",
    "entrega_alta_por_defecto",
]
