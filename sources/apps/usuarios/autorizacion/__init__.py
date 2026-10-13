"""
Dominio de la AUTORIZACION: rol efectivo por peticion y matriz `permiso_rol_operacion` (ARC-013).

Este modulo es la FACHADA del paquete: los consumidores importan el servicio, su repositorio y los
tipos de la decision desde `apps.usuarios.autorizacion`, no desde los submodulos, para que la
reorganizacion interna no rompa a nadie.

Rige DENY BY DEFAULT y FAIL-CLOSED: sin fila en la matriz no hay permiso, y un rol o un alcance que
no se sabe interpretar deniegan en vez de degradarse a algo mas amplio.
"""

from apps.usuarios.autorizacion.repositorio import IdentidadAutorizable, RepositorioAutorizacion
from apps.usuarios.autorizacion.servicio import ALCANCES_VALIDOS, PermisoAutorizado, ServicioAutorizacion


__all__ = [
    "ALCANCES_VALIDOS",
    "IdentidadAutorizable",
    "PermisoAutorizado",
    "RepositorioAutorizacion",
    "ServicioAutorizacion",
]
