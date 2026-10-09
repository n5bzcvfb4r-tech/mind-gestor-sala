"""
Ciclo de vida de la sesion opaca server-side (ARC-013, ARC-112, tabla `sesion_usuario`).

Este modulo es la FACHADA del paquete: los consumidores importan `ServicioCicloVidaSesion` y
los motivos de revocacion desde `apps.identidad.sesiones`, no desde los submodulos, para que
la reorganizacion interna no rompa a nadie.
"""

from apps.identidad.sesiones.servicio import (
    MOTIVO_REVOCACION_CADUCIDAD,
    MOTIVO_REVOCACION_LOGOUT,
    ServicioCicloVidaSesion,
)


__all__ = [
    "MOTIVO_REVOCACION_CADUCIDAD",
    "MOTIVO_REVOCACION_LOGOUT",
    "ServicioCicloVidaSesion",
]
