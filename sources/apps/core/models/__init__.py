"""Modelos ORM del esquema T.5 (21 tablas) del servicio de incidencias de salas.

Punto de entrada unico del paquete: todo consumidor importa desde `apps.core.models`,
nunca desde los submodulos, para que el orden de carga y las referencias perezosas
entre modulos queden resueltas en un solo sitio.

Organizacion por naturaleza del dato (T.5):
  * `catalogos`    -> 10 tablas de catalogo maestro (las siembra el changelog dml de ARC-016).
  * `transaccional`-> 6 tablas de negocio vivo (usuario, incidencia y sus satelites).
  * `eventos`      -> 5 tablas de traza append-only (historicos y auditoria).

Todas son `managed = False`: el DDL lo gobierna Liquibase (ARC-016), no Django.
"""

from apps.core.models.catalogos import (
    CategoriaIncidenciaEntity,
    ConfiguracionSmtpEntity,
    EstadoIncidenciaEntity,
    MotivoDesactivacionEntity,
    OficinaEntity,
    OperacionEntity,
    PermisoRolOperacionEntity,
    RolEntity,
    SalaEntity,
    TransicionIncidenciaEntity,
)
from apps.core.models.eventos import (
    AuditoriaAccesoEntity,
    AvisoCorreoIntentoEntity,
    IncidenciaHistoricoEntity,
    ResolucionDestinatarioLogEntity,
    UsuarioHistoricoEntity,
)
from apps.core.models.transaccional import (
    AvisoCorreoEntity,
    IncidenciaAdjuntoEntity,
    IncidenciaEntity,
    SesionUsuarioEntity,
    UsuarioEntity,
    UsuarioPasswordHistoricoEntity,
)

#: Correspondencia tabla fisica -> modelo, para los chequeos transversales
#: (verificacion de catalogos, prueba de persistencia) sin repetir literales.
MODELOS_POR_TABLA: dict[str, type] = {
    # Catalogos
    "cat_rol": RolEntity,
    "cat_operacion": OperacionEntity,
    "permiso_rol_operacion": PermisoRolOperacionEntity,
    "cat_oficina": OficinaEntity,
    "cat_sala": SalaEntity,
    "cat_categoria_incidencia": CategoriaIncidenciaEntity,
    "cat_estado_incidencia": EstadoIncidenciaEntity,
    "cat_transicion_incidencia": TransicionIncidenciaEntity,
    "cat_motivo_desactivacion": MotivoDesactivacionEntity,
    "configuracion_smtp": ConfiguracionSmtpEntity,
    # Transaccionales
    "usuario": UsuarioEntity,
    "usuario_password_historico": UsuarioPasswordHistoricoEntity,
    "sesion_usuario": SesionUsuarioEntity,
    "incidencia": IncidenciaEntity,
    "incidencia_adjunto": IncidenciaAdjuntoEntity,
    "aviso_correo": AvisoCorreoEntity,
    # Registros de evento (append-only)
    "usuario_historico": UsuarioHistoricoEntity,
    "auditoria_acceso": AuditoriaAccesoEntity,
    "incidencia_historico": IncidenciaHistoricoEntity,
    "aviso_correo_intento": AvisoCorreoIntentoEntity,
    "resolucion_destinatario_log": ResolucionDestinatarioLogEntity,
}

__all__ = [
    "MODELOS_POR_TABLA",
    "AuditoriaAccesoEntity",
    "AvisoCorreoEntity",
    "AvisoCorreoIntentoEntity",
    "CategoriaIncidenciaEntity",
    "ConfiguracionSmtpEntity",
    "EstadoIncidenciaEntity",
    "IncidenciaAdjuntoEntity",
    "IncidenciaEntity",
    "IncidenciaHistoricoEntity",
    "MotivoDesactivacionEntity",
    "OficinaEntity",
    "OperacionEntity",
    "PermisoRolOperacionEntity",
    "ResolucionDestinatarioLogEntity",
    "RolEntity",
    "SalaEntity",
    "SesionUsuarioEntity",
    "TransicionIncidenciaEntity",
    "UsuarioEntity",
    "UsuarioHistoricoEntity",
    "UsuarioPasswordHistoricoEntity",
]
