"""
Objetos de valor de la resolucion de destinatarios de aviso (REQ-089, REQ-140).

Este modulo es dominio puro: no toca la base de datos ni conoce el ORM. Describe QUE se ha
resuelto (destinatarios, resultado y motivo) para que el servicio y el repositorio compartan
un unico vocabulario y la persistencia del log sea una traduccion mecanica.

Conviven a proposito DOS vocabularios de "por que no se envia":

* `MotivoNoNotificable` es el motivo FUNCIONAL de REQ-089/REQ-140, el que explica la decision
  (destinatario inactivo, sin correo o no resoluble) y el que viaja hacia fuera del dominio.
* `aviso_correo.suppression_reason_code` es el codigo FISICO del DDL, acotado por el CHECK
  `ck_aviso_correo_motivo_sup`. Es un catalogo cerrado en base de datos y no se puede ampliar
  desde codigo.

Se mantienen separados porque cambian por razones distintas: el funcional al evolucionar el
requisito, el fisico solo con un changelog. `MOTIVO_SUPRESION_POR_MOTIVO` es el unico puente
entre ambos, para que ningun otro modulo escriba el literal fisico a mano.
"""

from dataclasses import dataclass
from enum import Enum


# --- Constantes del directorio de destinatarios --------------------------
# Rol que define el colectivo de mantenimiento: el equipo se resuelve por rol, no por una
# lista mantenida a mano, para que las altas y bajas de tecnicos no requieran despliegue.
ROL_EQUIPO_MANTENIMIENTO = "TECNICO_MANTENIMIENTO"
ESTADO_USUARIO_ACTIVO = "ACTIVO"
LONGITUD_MAXIMA_CORREO = 254

# Identificador del colectivo en el contrato (`GET /notification-groups/maintenance-team`).
COLECTIVO_EQUIPO_MANTENIMIENTO = "maintenance-team"


class TipoResolucion(str, Enum):
    """Tipo de resolucion solicitada; son los valores fisicos de `resolucion_destinatario_log.request_type`."""

    INDIVIDUAL = "INDIVIDUAL"
    COLECTIVO = "COLECTIVO"


class ResultadoResolucion(str, Enum):
    """
    Desenlace de una resolucion; valores fisicos de `resolucion_destinatario_log.outcome`.

    El enum es cerrado porque el DDL lo acota con un CHECK: agregar un miembro aqui sin el
    changelog correspondiente haria fallar la insercion del log.
    """

    OK = "OK"
    SIN_DESTINATARIOS = "SIN_DESTINATARIOS"
    NO_ENCONTRADO = "NO_ENCONTRADO"
    NO_NOTIFICABLE = "NO_NOTIFICABLE"
    ERROR_TECNICO = "ERROR_TECNICO"


class MotivoNoNotificable(str, Enum):
    """Motivo funcional por el que un destinatario resuelto no puede recibir el aviso (REQ-089, REQ-140)."""

    RECIPIENT_INACTIVE = "RECIPIENT_INACTIVE"
    RECIPIENT_WITHOUT_EMAIL = "RECIPIENT_WITHOUT_EMAIL"
    RECIPIENT_NOT_RESOLVABLE = "RECIPIENT_NOT_RESOLVABLE"


# Puente unico entre el motivo funcional y el catalogo CERRADO `aviso_correo.suppression_reason_code`
# (CHECK `ck_aviso_correo_motivo_sup`). Ningun otro modulo debe escribir estos literales a mano.
MOTIVO_SUPRESION_POR_MOTIVO: dict[MotivoNoNotificable, str] = {
    MotivoNoNotificable.RECIPIENT_INACTIVE: "USUARIO_DESACTIVADO",
    MotivoNoNotificable.RECIPIENT_WITHOUT_EMAIL: "SIN_CORREO",
    MotivoNoNotificable.RECIPIENT_NOT_RESOLVABLE: "DESTINATARIO_NO_RESOLUBLE",
}


@dataclass(frozen=True, slots=True)
class DestinatarioResuelto:
    """
    Destinatario ya validado como notificable: tiene correo corporativo y esta activo.

    Es inmutable a proposito: una vez resuelto, nadie puede reescribir la direccion a la que
    se enviara el aviso mientras la operacion esta en curso.
    """

    user_id: int
    full_name: str
    corporate_email: str
    role_code: str


@dataclass(frozen=True, slots=True)
class ResolucionColectivo:
    """
    Resultado de resolver un colectivo completo (por ejemplo, el equipo de mantenimiento).

    `degradado` indica que la resolucion termino sin poder consultar el directorio con
    garantias: la operacion de negocio sigue adelante, pero el aviso no se da por enviado.
    """

    colectivo: str
    destinatarios: tuple[DestinatarioResuelto, ...]
    outcome: ResultadoResolucion
    resolution_id: int | None = None
    degradado: bool = False

    @property
    def direcciones(self) -> tuple[str, ...]:
        """Direcciones de correo en el MISMO orden que `destinatarios`, para que el log sea reproducible."""

        return tuple(destinatario.corporate_email for destinatario in self.destinatarios)

    @property
    def user_ids(self) -> tuple[int, ...]:
        """Identificadores de los destinatarios resueltos, en el orden de `destinatarios`."""

        return tuple(destinatario.user_id for destinatario in self.destinatarios)

    @property
    def hay_destinatarios(self) -> bool:
        """Indica si la resolucion ha devuelto al menos un destinatario notificable."""

        return bool(self.destinatarios)

    @property
    def debe_enviarse(self) -> bool:
        """
        Indica si procede originar el envio del aviso.

        Una lista vacia NO origina envio (REQ-089, RN-08): sin destinatarios no hay correo que
        mandar, y fabricar uno "a nadie" dejaria un aviso fantasma en la trazabilidad.
        """

        return self.hay_destinatarios


@dataclass(frozen=True, slots=True)
class NotificabilidadDestinatario:
    """
    Resultado de comprobar si un destinatario individual puede recibir el aviso (REQ-140).

    Cuando `es_notificable` es falso, `motivo` explica por que y `destinatario` puede venir a
    `None`: no siempre hay ficha que devolver (por ejemplo, si no se ha podido resolver).
    """

    user_id: int
    es_notificable: bool
    destinatario: DestinatarioResuelto | None
    motivo: MotivoNoNotificable | None
    outcome: ResultadoResolucion
    resolution_id: int | None = None
    degradado: bool = False

    @property
    def suppression_reason_code(self) -> str | None:
        """Codigo fisico de supresion para `aviso_correo`, o `None` si no hubo motivo de supresion."""

        if self.motivo is None:
            return None
        return MOTIVO_SUPRESION_POR_MOTIVO[self.motivo]

    @property
    def direccion(self) -> str | None:
        """Direccion de correo del destinatario, o `None` si no hay ficha resuelta."""

        if self.destinatario is None:
            return None
        return self.destinatario.corporate_email


__all__ = [
    "COLECTIVO_EQUIPO_MANTENIMIENTO",
    "ESTADO_USUARIO_ACTIVO",
    "LONGITUD_MAXIMA_CORREO",
    "MOTIVO_SUPRESION_POR_MOTIVO",
    "ROL_EQUIPO_MANTENIMIENTO",
    "DestinatarioResuelto",
    "MotivoNoNotificable",
    "NotificabilidadDestinatario",
    "ResolucionColectivo",
    "ResultadoResolucion",
    "TipoResolucion",
]
