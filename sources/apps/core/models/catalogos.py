"""
Modelos de los catalogos maestros del esquema T.5 (ARC-100 .. ARC-109).

Son las 10 tablas de `data_kind = catalog`: roles, operaciones, matriz de permisos,
oficinas, salas, categorias, estados y transiciones de incidencia, motivos de
desactivacion y configuracion SMTP.

Tres reglas gobiernan este modulo:

* **`managed = False`**: el DDL NO lo gobierna Django. Las tablas, restricciones, indices
  y disparadores los crea y versiona Liquibase (ARC-016); aqui solo se mapea lo que ya
  existe en Oracle. Ninguna migracion de Django debe crear ni alterar estas tablas.
* **Los valores se LEEN de la base de datos**: las filas las aporta el changelog dml de
  semillas. Esta prohibido hardcodear los codigos de catalogo en enums de Python o del
  front; si un catalogo esta vacio al arrancar es un defecto de la tarea de datos y se
  reporta como health check `Warning` con `check: seeds`, no se tapa con valores por defecto.
* **Sin borrado fisico**: la baja de un elemento de catalogo es LOGICA, via `is_active`
  (REQ-047). Por eso todos heredan de `SinBorradoFisicoMixin` y las claves ajenas usan
  `on_delete=models.DO_NOTHING`.

Los nombres fisicos de tabla y columna son LITERALMENTE los del DDL, en minusculas y sin
comillas: Oracle los pliega a mayusculas y el proyecto no usa identificadores entrecomillados.
"""

from django.db import models

from apps.core.modelos_base import AtribucionMixin, SinBorradoFisicoMixin


# Indicador booleano de Oracle: CHAR(1) con CHECK IN ('Y','N'). No existe BOOLEAN en el esquema.
INDICADOR_SN: tuple[tuple[str, str], ...] = (("Y", "Si"), ("N", "No"))

# Alcance de datos que concede un permiso rol-operacion (ARC-102): propio o total.
ALCANCE_DATOS: tuple[tuple[str, str], ...] = (("OWN", "Propio"), ("ALL", "Todos"))


class RolEntity(SinBorradoFisicoMixin, models.Model):
    """ARC-100 `cat_rol`: catalogo cerrado de roles funcionales del sistema."""

    role_code = models.CharField(primary_key=True, max_length=32, db_column="role_code")
    role_name = models.CharField(max_length=60, db_column="role_name")
    role_description = models.CharField(max_length=200, null=True, blank=True, db_column="role_description")
    display_order = models.IntegerField(db_column="display_order")
    is_active = models.CharField(max_length=1, choices=INDICADOR_SN, default="Y", db_column="is_active")

    class Meta:
        managed = False
        db_table = "cat_rol"
        verbose_name = "rol"
        verbose_name_plural = "roles"
        ordering = ("display_order",)

    def __str__(self) -> str:
        return f"Rol {self.role_code}: {self.role_name}"


class OperacionEntity(SinBorradoFisicoMixin, models.Model):
    """ARC-101 `cat_operacion`: operaciones funcionales autorizables de la API."""

    operation_code = models.CharField(primary_key=True, max_length=50, db_column="operation_code")
    operation_name = models.CharField(max_length=100, db_column="operation_name")
    is_active = models.CharField(max_length=1, choices=INDICADOR_SN, default="Y", db_column="is_active")

    class Meta:
        managed = False
        db_table = "cat_operacion"
        verbose_name = "operacion"
        verbose_name_plural = "operaciones"
        ordering = ("operation_code",)

    def __str__(self) -> str:
        return f"Operacion {self.operation_code}: {self.operation_name}"


class PermisoRolOperacionEntity(SinBorradoFisicoMixin, models.Model):
    """
    ARC-102 `permiso_rol_operacion`: matriz rol x operacion con el alcance de datos.

    Es la fuente de verdad de la autorizacion: la AUSENCIA de fila significa denegado
    (deny by default); no hay permisos implicitos en codigo.

    En Oracle la clave primaria REAL es compuesta, `(role_code, operation_code)`, y
    `permiso_id` es una columna IDENTITY con restriccion UNIQUE. Django solo admite clave
    primaria simple, asi que aqui se declara `permiso_id` como clave primaria del ORM -lo
    que es legitimo porque es unica y autogenerada- y el par se expresa con
    `unique_together`. La unicidad real del par la sigue garantizando la PK compuesta de
    Oracle, no esta declaracion (la tabla es `managed = False`).
    """

    permiso_id = models.AutoField(primary_key=True, db_column="permiso_id")
    role_code = models.ForeignKey(
        RolEntity,
        on_delete=models.DO_NOTHING,
        db_column="role_code",
        related_name="permisos",
    )
    operation_code = models.ForeignKey(
        OperacionEntity,
        on_delete=models.DO_NOTHING,
        db_column="operation_code",
        related_name="permisos",
    )
    data_scope = models.CharField(max_length=10, choices=ALCANCE_DATOS, db_column="data_scope")

    class Meta:
        managed = False
        db_table = "permiso_rol_operacion"
        verbose_name = "permiso de rol sobre operacion"
        verbose_name_plural = "permisos de rol sobre operacion"
        ordering = ("role_code", "operation_code")
        unique_together = (("role_code", "operation_code"),)

    def __str__(self) -> str:
        return f"Permiso {self.role_code_id} -> {self.operation_code_id} (alcance {self.data_scope})"


class OficinaEntity(SinBorradoFisicoMixin, models.Model):
    """ARC-103 `cat_oficina`: oficinas de la organizacion a las que pertenecen las salas."""

    office_id = models.AutoField(primary_key=True, db_column="office_id")
    office_code = models.CharField(max_length=10, db_column="office_code")
    office_name = models.CharField(max_length=80, db_column="office_name")
    city = models.CharField(max_length=60, null=True, blank=True, db_column="city")
    is_active = models.CharField(max_length=1, choices=INDICADOR_SN, default="Y", db_column="is_active")

    class Meta:
        managed = False
        db_table = "cat_oficina"
        verbose_name = "oficina"
        verbose_name_plural = "oficinas"
        ordering = ("office_code",)

    def __str__(self) -> str:
        return f"Oficina {self.office_code}: {self.office_name}"


class SalaEntity(AtribucionMixin, SinBorradoFisicoMixin, models.Model):
    """
    ARC-104 `cat_sala`: salas de reuniones sobre las que se reportan incidencias.

    Lleva atribucion completa de alta y modificacion, con los nombres de columna por defecto
    del `AtribucionMixin` (`created_at`/`created_by`/`updated_at`/`updated_by`), por lo que no
    hace falta redefinir `CAMPO_*`.

    `created_by` y `updated_by` se mapean como entero y no como clave ajena porque el DDL
    aplicado todavia NO declara la restriccion contra `usuario`: esa tabla la crea una tarea
    posterior, que anadira las FK `fk_cat_sala_created_by` y `fk_cat_sala_updated_by`.
    """

    room_id = models.AutoField(primary_key=True, db_column="room_id")
    room_code = models.CharField(max_length=20, db_column="room_code")
    room_name = models.CharField(max_length=80, db_column="room_name")
    office = models.ForeignKey(
        OficinaEntity,
        on_delete=models.DO_NOTHING,
        db_column="office_id",
        related_name="salas",
    )
    is_active = models.CharField(max_length=1, choices=INDICADOR_SN, default="Y", db_column="is_active")
    created_at = models.DateTimeField(db_column="created_at")
    created_by = models.IntegerField(db_column="created_by")
    updated_at = models.DateTimeField(null=True, blank=True, db_column="updated_at")
    updated_by = models.IntegerField(null=True, blank=True, db_column="updated_by")

    class Meta:
        managed = False
        db_table = "cat_sala"
        verbose_name = "sala"
        verbose_name_plural = "salas"
        ordering = ("room_code",)

    def __str__(self) -> str:
        return f"Sala {self.room_code}: {self.room_name}"


class CategoriaIncidenciaEntity(AtribucionMixin, SinBorradoFisicoMixin, models.Model):
    """
    ARC-105 `cat_categoria_incidencia`: catalogo cerrado de categorias de la incidencia.

    Solo tiene atribucion de modificacion (`updated_at`/`updated_by`). Los atributos
    `CAMPO_ACTOR_ALTA`/`CAMPO_FECHA_ALTA` del `AtribucionMixin` se dejan por defecto: el mixin
    solo escribe los campos que el modelo declara realmente, asi que las columnas de alta
    inexistentes se ignoran sin error.

    `updated_by` queda como entero porque el DDL aplicado aun no declara la FK a `usuario`
    (tabla pendiente de ARC-110).
    """

    category_id = models.AutoField(primary_key=True, db_column="category_id")
    category_code = models.CharField(max_length=20, db_column="category_code")
    category_name = models.CharField(max_length=60, db_column="category_name")
    display_order = models.IntegerField(db_column="display_order")
    is_active = models.CharField(max_length=1, choices=INDICADOR_SN, default="Y", db_column="is_active")
    updated_at = models.DateTimeField(null=True, blank=True, db_column="updated_at")
    updated_by = models.IntegerField(null=True, blank=True, db_column="updated_by")

    class Meta:
        managed = False
        db_table = "cat_categoria_incidencia"
        verbose_name = "categoria de incidencia"
        verbose_name_plural = "categorias de incidencia"
        ordering = ("display_order",)

    def __str__(self) -> str:
        return f"Categoria {self.category_code}: {self.category_name}"


class EstadoIncidenciaEntity(SinBorradoFisicoMixin, models.Model):
    """ARC-106 `cat_estado_incidencia`: catalogo cerrado de estados del ciclo de vida."""

    status_code = models.CharField(primary_key=True, max_length=20, db_column="status_code")
    status_name = models.CharField(max_length=40, db_column="status_name")
    sort_order = models.IntegerField(db_column="sort_order")
    is_terminal = models.CharField(max_length=1, choices=INDICADOR_SN, default="N", db_column="is_terminal")
    is_active = models.CharField(max_length=1, choices=INDICADOR_SN, default="Y", db_column="is_active")

    class Meta:
        managed = False
        db_table = "cat_estado_incidencia"
        verbose_name = "estado de incidencia"
        verbose_name_plural = "estados de incidencia"
        ordering = ("sort_order",)

    def __str__(self) -> str:
        return f"Estado {self.status_code}: {self.status_name}"


class TransicionIncidenciaEntity(SinBorradoFisicoMixin, models.Model):
    """
    ARC-107 `cat_transicion_incidencia`: grafo de transiciones permitidas y sus precondiciones.

    Un par origen-destino NO declarado (o declarado con `is_active = 'N'`) es una transicion
    DENEGADA: el grafo se consulta en base de datos, nunca se replica en una maquina de
    estados escrita a mano.
    """

    transition_id = models.AutoField(primary_key=True, db_column="transition_id")
    from_status = models.ForeignKey(
        EstadoIncidenciaEntity,
        on_delete=models.DO_NOTHING,
        db_column="from_status",
        related_name="transiciones_de_origen",
    )
    to_status = models.ForeignKey(
        EstadoIncidenciaEntity,
        on_delete=models.DO_NOTHING,
        db_column="to_status",
        related_name="transiciones_de_destino",
    )
    allowed_role = models.ForeignKey(
        RolEntity,
        on_delete=models.DO_NOTHING,
        db_column="allowed_role",
        related_name="transiciones_autorizadas",
    )
    requires_assignee = models.CharField(max_length=1, choices=INDICADOR_SN, default="N", db_column="requires_assignee")
    requires_comment = models.CharField(max_length=1, choices=INDICADOR_SN, default="N", db_column="requires_comment")
    is_active = models.CharField(max_length=1, choices=INDICADOR_SN, default="Y", db_column="is_active")

    class Meta:
        managed = False
        db_table = "cat_transicion_incidencia"
        verbose_name = "transicion de incidencia"
        verbose_name_plural = "transiciones de incidencia"
        ordering = ("from_status", "to_status")
        unique_together = (("from_status", "to_status"),)

    def __str__(self) -> str:
        return f"Transicion {self.from_status_id} -> {self.to_status_id} (rol {self.allowed_role_id})"


class MotivoDesactivacionEntity(SinBorradoFisicoMixin, models.Model):
    """ARC-108 `cat_motivo_desactivacion`: motivos de desactivacion de una cuenta de usuario."""

    reason_code = models.CharField(primary_key=True, max_length=30, db_column="reason_code")
    reason_name = models.CharField(max_length=120, db_column="reason_name")
    is_active = models.CharField(max_length=1, choices=INDICADOR_SN, default="Y", db_column="is_active")

    class Meta:
        managed = False
        db_table = "cat_motivo_desactivacion"
        verbose_name = "motivo de desactivacion"
        verbose_name_plural = "motivos de desactivacion"
        ordering = ("reason_code",)

    def __str__(self) -> str:
        return f"Motivo {self.reason_code}: {self.reason_name}"


class ConfiguracionSmtpEntity(AtribucionMixin, SinBorradoFisicoMixin, models.Model):
    """
    ARC-109 `configuracion_smtp`: parametros del servidor de correo y del remitente.

    La tabla NO guarda ninguna credencial: `secreto_ref` contiene unicamente una REFERENCIA
    EXTERNA al secreto (su identificador en el gestor de secretos), y el changelog incluye un
    oraculo que verifica que no existe ninguna columna de contrasena en claro (REQ-076 /
    AC-SMTP-11).

    Solo tiene atribucion de modificacion (`updated_at`/`updated_by`), ambas obligatorias; los
    `CAMPO_*` de alta del `AtribucionMixin` se dejan por defecto porque el mixin ignora los
    campos que el modelo no declara. `updated_by` es entero: el DDL aplicado aun no declara la
    FK a `usuario` (tabla pendiente de ARC-110).
    """

    config_id = models.AutoField(primary_key=True, db_column="config_id")
    smtp_host = models.CharField(max_length=255, db_column="smtp_host")
    smtp_port = models.IntegerField(db_column="smtp_port")
    use_tls = models.CharField(max_length=1, choices=INDICADOR_SN, default="N", db_column="use_tls")
    smtp_username = models.CharField(max_length=255, null=True, blank=True, db_column="smtp_username")
    secreto_ref = models.CharField(max_length=512, null=True, blank=True, db_column="secreto_ref")
    sender_address = models.CharField(max_length=254, db_column="sender_address")
    sender_display_name = models.CharField(max_length=100, db_column="sender_display_name")
    facilities_fallback_email = models.CharField(max_length=254, null=True, blank=True, db_column="facilities_fallback_email")
    max_attempts = models.IntegerField(default=3, db_column="max_attempts")
    is_active = models.CharField(max_length=1, choices=INDICADOR_SN, default="N", db_column="is_active")
    updated_at = models.DateTimeField(db_column="updated_at")
    updated_by = models.IntegerField(db_column="updated_by")

    class Meta:
        managed = False
        db_table = "configuracion_smtp"
        verbose_name = "configuracion SMTP"
        verbose_name_plural = "configuraciones SMTP"
        ordering = ("config_id",)

    def __str__(self) -> str:
        return f"Configuracion SMTP {self.config_id}: {self.smtp_host}:{self.smtp_port} (activa: {self.is_active})"


__all__ = [
    "ALCANCE_DATOS",
    "INDICADOR_SN",
    "CategoriaIncidenciaEntity",
    "ConfiguracionSmtpEntity",
    "EstadoIncidenciaEntity",
    "MotivoDesactivacionEntity",
    "OficinaEntity",
    "OperacionEntity",
    "PermisoRolOperacionEntity",
    "RolEntity",
    "SalaEntity",
    "TransicionIncidenciaEntity",
]
