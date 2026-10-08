"""
Repositorio base del nucleo: acceso a datos SIN ninguna operacion de borrado fisico.

El contrato es deliberadamente incompleto: no hay `borrar`, `eliminar`, `delete` ni `purgar`
en ningun punto de la jerarquia (REQ-047). La unica forma de dar de baja una entidad es la
baja LOGICA, que escribe estado, motivo, fecha y actor sin tocar la fila existente.
"""

from datetime import timedelta
from typing import Any

from django.db import models

from apps.core import mensajes
from apps.core.contexto import contexto_requerido, utc_now
from apps.core.errores import ErrorDominio


# Retencion de los datos de un usuario desactivado antes de su anonimizacion.
DIAS_RETENCION_DESACTIVACION = 730

ESTADO_USUARIO_INACTIVO = "INACTIVO"


class RepositorioBase:
    """
    Repositorio generico sobre un modelo del ORM.

    Las subclases fijan `modelo` como atributo de clase o lo resuelven de forma perezosa en
    `__init__` (los modelos no se pueden importar a nivel de modulo antes de `django.setup()`).
    """

    modelo: type[models.Model]

    def __init__(self, modelo: type[models.Model] | None = None) -> None:
        if modelo is not None:
            self.modelo = modelo

    def obtener(self, pk: Any) -> models.Model | None:
        """Devuelve la entidad por clave primaria, o `None` si no existe."""

        return self.modelo.objects.filter(pk=pk).first()

    def obtener_o_error(self, pk: Any, mensaje: str = mensajes.RECURSO_NO_ENCONTRADO) -> models.Model:
        """Devuelve la entidad por clave primaria o lanza `ErrorDominio` con el mensaje en espanol."""

        entidad = self.obtener(pk)
        if entidad is None:
            raise ErrorDominio(mensaje)
        return entidad

    def listar(self, **filtros: Any) -> models.QuerySet:
        """Devuelve el queryset filtrado; el predicado viaja a la consulta, nunca a memoria."""

        return self.modelo.objects.filter(**filtros)

    def crear(self, **datos: Any) -> models.Model:
        """Instancia y persiste una entidad nueva (la atribucion la pone el mixin, no el payload)."""

        entidad = self.modelo(**datos)
        entidad.save()
        return entidad

    def guardar(self, entidad: models.Model) -> models.Model:
        """Persiste los cambios de una entidad ya existente."""

        entidad.save()
        return entidad

    def existe(self, **filtros: Any) -> bool:
        return self.modelo.objects.filter(**filtros).exists()

    def contar(self, **filtros: Any) -> int:
        return self.modelo.objects.filter(**filtros).count()


class RepositorioUsuario(RepositorioBase):
    """Acceso a datos de usuarios. La baja es logica: nunca se borra la fila (REQ-047)."""

    def __init__(self, modelo: type[models.Model] | None = None) -> None:
        if modelo is None:
            from apps.core.models import UsuarioEntity

            modelo = UsuarioEntity
        super().__init__(modelo)

    def desactivar(self, usuario: models.Model, *, reason_code: str, nota: str | None = None) -> models.Model:
        """
        Da de baja LOGICA al usuario: marca estado, motivo, fecha, actor y horizonte de retencion.

        El actor y la fecha salen del contexto de sesion y del reloj del servidor, nunca del payload.
        """

        momento = utc_now()
        usuario.status = ESTADO_USUARIO_INACTIVO
        usuario.deactivated_at = momento
        usuario.deactivated_by = contexto_requerido().user_id
        usuario.deactivation_reason_code = reason_code
        usuario.deactivation_note = nota
        usuario.retention_until = (momento + timedelta(days=DIAS_RETENCION_DESACTIVACION)).date()
        usuario.save()
        return usuario


class RepositorioIncidencia(RepositorioBase):
    """Acceso a datos de incidencias."""

    def __init__(self, modelo: type[models.Model] | None = None) -> None:
        if modelo is None:
            from apps.core.models import IncidenciaEntity

            modelo = IncidenciaEntity
        super().__init__(modelo)

    def listar_por_reportante(self, user_id: int) -> models.QuerySet:
        """Incidencias reportadas por el usuario indicado; el filtro se resuelve en la consulta SQL."""

        return self.modelo.objects.filter(reported_by=user_id)


# Nombres que NINGUN repositorio del servicio puede publicar: el borrado fisico no existe (REQ-047).
OPERACIONES_PROHIBIDAS = ("delete", "borrar", "eliminar", "purgar", "hard_delete")


def expone_borrado_fisico(clase: type) -> bool:
    """Indica si la clase publica alguna operacion de borrado fisico (oraculo del DoD)."""

    return any(hasattr(clase, nombre) for nombre in OPERACIONES_PROHIBIDAS)
