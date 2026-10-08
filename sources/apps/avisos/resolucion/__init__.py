"""
Resolucion de destinatarios de aviso (REQ-089, REQ-140).

Decide a quien se le puede enviar un aviso por correo antes de componer nada: resuelve el
colectivo de mantenimiento y la notificabilidad de un destinatario individual contra el
directorio de usuarios, y deja constancia del intento para poder auditarlo despues.

Este modulo es la FACHADA del paquete: los consumidores importan desde `apps.avisos.resolucion`
y no desde los submodulos, para que la reorganizacion interna no rompa a nadie.

Importar el paquete es seguro ANTES de `django.setup()`: ni el repositorio ni el servicio
importan modelos a nivel de modulo (los resuelven de forma perezosa), de modo que la fachada no
fuerza la carga del registro de aplicaciones.
"""

from apps.avisos.resolucion.errores import (
    ColectivoDesconocidoError,
    ConsultaNoAutorizadaError,
    DirectorioNoDisponibleError,
)
from apps.avisos.resolucion.repositorio import (
    RepositorioDirectorioDestinatarios,
    RepositorioResolucionDestinatarioLog,
)
from apps.avisos.resolucion.resultados import (
    COLECTIVO_EQUIPO_MANTENIMIENTO,
    ESTADO_USUARIO_ACTIVO,
    LONGITUD_MAXIMA_CORREO,
    MOTIVO_SUPRESION_POR_MOTIVO,
    ROL_EQUIPO_MANTENIMIENTO,
    DestinatarioResuelto,
    MotivoNoNotificable,
    NotificabilidadDestinatario,
    ResolucionColectivo,
    ResultadoResolucion,
    TipoResolucion,
)
from apps.avisos.resolucion.servicio import (
    MODULO_POR_DEFECTO,
    ROL_ADMINISTRADOR,
    ServicioResolucionDestinatarios,
)


__all__ = [
    "COLECTIVO_EQUIPO_MANTENIMIENTO",
    "ESTADO_USUARIO_ACTIVO",
    "LONGITUD_MAXIMA_CORREO",
    "MODULO_POR_DEFECTO",
    "MOTIVO_SUPRESION_POR_MOTIVO",
    "ROL_ADMINISTRADOR",
    "ROL_EQUIPO_MANTENIMIENTO",
    "ColectivoDesconocidoError",
    "ConsultaNoAutorizadaError",
    "DestinatarioResuelto",
    "DirectorioNoDisponibleError",
    "MotivoNoNotificable",
    "NotificabilidadDestinatario",
    "RepositorioDirectorioDestinatarios",
    "RepositorioResolucionDestinatarioLog",
    "ResolucionColectivo",
    "ResultadoResolucion",
    "ServicioResolucionDestinatarios",
    "TipoResolucion",
]
