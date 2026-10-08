"""
Modelos de las tablas TRANSACCIONALES del esquema T.5 (ARC-110 a ARC-115).

Aqui viven las seis entidades que soportan la operativa del servicio: el censo de usuarios y
su historico de contrasenias, las sesiones emitidas, la incidencia con su ciclo de vida, su
adjunto y la cola de avisos por correo.

Tres invariantes gobiernan este modulo:

* `managed = False` en TODAS las entidades. El DDL es responsabilidad EXCLUSIVA de Liquibase
  (ARC-016): Django no crea, no altera y no borra ninguna de estas tablas, solo las lee y las
  escribe. Los nombres fisicos de tabla y columna son literalmente los del changelog
  `facilities/changelogs/0.0.1/ddl/`, sin traducir.
* SIN BORRADO FISICO (REQ-047). Las bajas son logicas: el usuario se desactiva, la sesion se
  revoca con `revoked_at` y la incidencia se cierra. Por eso las entidades heredan de
  `SinBorradoFisicoMixin` (y el historico de contrasenias, que es append-only, de
  `RegistroInmutableMixin`), que rechazan el `DELETE` por instancia y por queryset.
* ATRIBUCION DESDE EL CONTEXTO DE SESION (REQ-064). Quien da de alta o modifica un usuario o
  una incidencia NO se lee del payload: lo pone `AtribucionMixin` desde `ContextoSesion`,
  junto con la marca temporal del servidor (`utc_now()`, UTC naive).

COLUMNAS VIRTUALES NO MAPEADAS
------------------------------
El DDL declara varias columnas `GENERATED ALWAYS AS (...) VIRTUAL`, de SOLO LECTURA: Oracle
responde ORA-54013 si una sentencia las menciona en un INSERT o UPDATE. Django incluye en el
INSERT todos los campos que declara el modelo, de modo que mapearlas romperia cualquier alta.
Por eso quedan deliberadamente FUERA del ORM, y en su lugar se mapea la columna fisica de
escritura sobre la que cada una se calcula:

* `usuario.is_active` (derivada de `status`) y `usuario.retention_until` (de `deactivated_at`);
* `sesion_usuario.last_seen_at` y `sesion_usuario.revocation_cause`;
* `incidencia.status_code` (sobre `status`), `incidencia.reported_by_user_id` (sobre
  `reported_by`) y `incidencia.closed_by_user_id` (sobre `closed_by`), mas
  `incidencia.retention_expires_at` (de `created_at`);
* `incidencia_adjunto.uploaded_by_user_id` (sobre `uploaded_by`);
* `aviso_correo.dispatch_id` (sobre `notification_id`) y `aviso_correo.status_code` (sobre
  `status`).
"""

import uuid

from django.db import models

from apps.core.contexto import utc_now
from apps.core.modelos_base import (
    AtribucionMixin,
    RegistroInmutableMixin,
    SinBorradoFisicoMixin,
)
from apps.core.models.catalogos import (
    CategoriaIncidenciaEntity,
    EstadoIncidenciaEntity,
    MotivoDesactivacionEntity,
    RolEntity,
    SalaEntity,
)


# Indicador booleano de Oracle: CHAR(1) con los unicos valores 'Y' (si) y 'N' (no).
INDICADOR_SN: tuple[tuple[str, str], ...] = (("Y", "Si"), ("N", "No"))

# Enumerados CERRADOS por CHECK en el DDL; se replican aqui para validar antes de ir a la base.
ESTADOS_USUARIO: tuple[tuple[str, str], ...] = (
    ("ACTIVO", "Activo"),
    ("INACTIVO", "Inactivo"),
)

ALGORITMOS_PASSWORD: tuple[tuple[str, str], ...] = (
    ("argon2id", "Argon2id"),
    ("bcrypt", "bcrypt"),
)

MOTIVOS_REVOCACION_SESION: tuple[tuple[str, str], ...] = (
    ("logout", "Cierre de sesion del usuario"),
    ("expired", "Sesion caducada"),
    ("admin", "Revocada por un administrador"),
    ("password_change", "Cambio de contrasenia"),
    ("account_deactivated", "Cuenta desactivada"),
)

MIME_TYPES_ADJUNTO: tuple[tuple[str, str], ...] = (
    ("image/jpeg", "Imagen JPEG"),
    ("image/png", "Imagen PNG"),
)

TIPOS_AVISO_CORREO: tuple[tuple[str, str], ...] = (
    ("NEW_INCIDENT_ALERT", "Alta de incidencia"),
    ("STATUS_CHANGE_ALERT", "Cambio de estado de incidencia"),
    ("CREDENTIAL_ISSUED", "Credencial emitida"),
    ("PASSWORD_RESET", "Restablecimiento de contrasenia"),
)

ESTADOS_AVISO_CORREO: tuple[tuple[str, str], ...] = (
    ("PENDIENTE", "Pendiente"),
    ("ENVIANDO", "Enviando"),
    ("ENVIADO", "Enviado"),
    ("FALLIDO", "Fallido"),
    ("DESCARTADO", "Descartado"),
    ("SUPRIMIDO", "Suprimido"),
)

MOTIVOS_SUPRESION_AVISO: tuple[tuple[str, str], ...] = (
    ("USUARIO_DESACTIVADO", "Usuario desactivado"),
    ("SIN_CORREO", "Sin correo de destino"),
    ("DESTINATARIO_NO_RESOLUBLE", "Destinatario no resoluble"),
    ("NO_RECIPIENTS", "Sin destinatarios"),
    ("COMPOSICION_INCOMPLETA", "Composicion incompleta"),
)


def generar_identificador_uuid() -> str:
    """
    Genera el uuid canonico en minusculas de las claves primarias opacas.

    Se genera SIEMPRE en Python y nunca con una funcion de base de datos: la base solo
    verifica el formato (`ck_sesion_usuario_id_uuid`), no lo fabrica, de modo que el
    identificador existe antes del INSERT y no es adivinable ni correlativo.
    """

    return str(uuid.uuid4())


class UsuarioEntity(AtribucionMixin, SinBorradoFisicoMixin, models.Model):
    """
    Censo de personas del sistema (ARC-110, tabla `usuario`).

    Rol vigente, credencial y estado de cuenta. La baja es LOGICA (REQ-047): `status` pasa a
    INACTIVO y se sellan `deactivated_at`, `deactivated_by` y `deactivation_reason_code`; la
    fila nunca se borra.

    SECRETOS (REQ-063): `password_hash` y `password_salt` son material criptografico. NUNCA se
    exponen por la API (ningun serializador los publica) ni se escriben en logs, trazas o
    mensajes de error. La contrasenia en claro no se persiste en ninguna columna.
    """

    user_id = models.AutoField(primary_key=True, db_column="user_id")
    full_name = models.CharField(max_length=120, db_column="full_name")
    # La unicidad es global e insensible a mayusculas y espacios, y la impone el indice
    # funcional ux_usuario_email_ci sobre LOWER(TRIM(corporate_email)) (REQ-045).
    corporate_email = models.CharField(max_length=150, db_column="corporate_email")
    role_code = models.ForeignKey(
        RolEntity,
        on_delete=models.DO_NOTHING,
        db_column="role_code",
        related_name="usuarios",
    )
    status = models.CharField(
        max_length=10,
        choices=ESTADOS_USUARIO,
        default="ACTIVO",
        db_column="status",
    )

    # Credencial (REQ-053, REQ-072). Ver la nota de secretos del docstring.
    username = models.CharField(max_length=100, db_column="username")
    password_hash = models.CharField(max_length=255, db_column="password_hash")
    password_salt = models.CharField(
        max_length=64, null=True, blank=True, db_column="password_salt"
    )
    password_algorithm = models.CharField(
        max_length=30, choices=ALGORITMOS_PASSWORD, db_column="password_algorithm"
    )
    password_updated_at = models.DateTimeField(
        null=True, blank=True, db_column="password_updated_at"
    )
    must_change_password = models.CharField(
        max_length=1,
        choices=INDICADOR_SN,
        default="Y",
        db_column="must_change_password",
    )
    credential_issued_at = models.DateTimeField(
        null=True, blank=True, db_column="credential_issued_at"
    )
    password_expires_at = models.DateTimeField(
        null=True, blank=True, db_column="password_expires_at"
    )
    failed_password_attempts = models.IntegerField(
        default=0, db_column="failed_password_attempts"
    )
    last_failed_attempt_at = models.DateTimeField(
        null=True, blank=True, db_column="last_failed_attempt_at"
    )
    locked_until = models.DateTimeField(null=True, blank=True, db_column="locked_until")
    last_login_at = models.DateTimeField(null=True, blank=True, db_column="last_login_at")

    # Cambio de rol y baja logica.
    role_changed_at = models.DateTimeField(null=True, blank=True, db_column="role_changed_at")
    role_changed_by = models.ForeignKey(
        "self",
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="role_changed_by",
        related_name="cambios_de_rol_ejecutados",
    )
    deactivated_at = models.DateTimeField(null=True, blank=True, db_column="deactivated_at")
    deactivated_by = models.ForeignKey(
        "self",
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="deactivated_by",
        related_name="usuarios_desactivados",
    )
    deactivation_reason_code = models.ForeignKey(
        MotivoDesactivacionEntity,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="deactivation_reason_code",
        related_name="usuarios",
    )
    deactivation_note = models.CharField(
        max_length=500, null=True, blank=True, db_column="deactivation_note"
    )

    # Atribucion (REQ-048, REQ-064). created_by es nulo SOLO en la cuenta semilla.
    created_at = models.DateTimeField(default=utc_now, db_column="created_at")
    created_by = models.ForeignKey(
        "self",
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="created_by",
        related_name="usuarios_creados",
    )
    updated_at = models.DateTimeField(null=True, blank=True, db_column="updated_at")
    updated_by = models.ForeignKey(
        "self",
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="updated_by",
        related_name="usuarios_modificados",
    )

    class Meta:
        managed = False
        db_table = "usuario"
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"
        ordering = ["-created_at", "-user_id"]

    def __str__(self) -> str:
        return f"{self.full_name} <{self.corporate_email}>"


class UsuarioPasswordHistoricoEntity(RegistroInmutableMixin, models.Model):
    """
    Historico de hashes de contrasenia (ARC-111, tabla `usuario_password_historico`).

    Append-only (REQ-069): la entrada se escribe cuando una contrasenia deja de ser vigente y
    no se reescribe nunca. El recorte a las 3 ultimas por usuario lo hace el trigger
    `trg_usuario_pwd_hist_purga` en la base, no la aplicacion.

    `password_hash` es material criptografico: no se expone por la API ni se registra en logs
    (REQ-063, REQ-076). La contrasenia en claro no se guarda jamas.
    """

    password_history_id = models.AutoField(primary_key=True, db_column="password_history_id")
    user = models.ForeignKey(
        UsuarioEntity,
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        related_name="contrasenias_historicas",
    )
    password_hash = models.CharField(max_length=255, db_column="password_hash")
    # Instante en que la contrasenia dejo de ser vigente, no su fecha de alta.
    created_at = models.DateTimeField(default=utc_now, db_column="created_at")

    class Meta:
        managed = False
        db_table = "usuario_password_historico"
        verbose_name = "contrasenia historica de usuario"
        verbose_name_plural = "contrasenias historicas de usuario"
        ordering = ["-created_at", "-password_history_id"]

    def __str__(self) -> str:
        return f"Contrasenia archivada {self.password_history_id} del usuario {self.user_id}"


class SesionUsuarioEntity(SinBorradoFisicoMixin, models.Model):
    """
    Sesiones emitidas tras la autenticacion (ARC-112, tabla `sesion_usuario`).

    La sesion se REVOCA, no se borra (REQ-047, REQ-070): se sella `revoked_at` junto con el
    `revocation_reason` que explica por que, y la fila permanece como evidencia del acceso.

    `session_id` es un uuid opaco generado en Python; no viaja nunca en la URL.
    """

    session_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=generar_identificador_uuid,
        db_column="session_id",
    )
    user = models.ForeignKey(
        UsuarioEntity,
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        related_name="sesiones",
    )
    # Rol vigente en el INSTANTE de la emision; no se recalcula al leer la sesion.
    role_code = models.ForeignKey(
        RolEntity,
        on_delete=models.DO_NOTHING,
        db_column="role_code",
        related_name="sesiones",
    )
    issued_at = models.DateTimeField(default=utc_now, db_column="issued_at")
    expires_at = models.DateTimeField(db_column="expires_at")
    last_activity_at = models.DateTimeField(default=utc_now, db_column="last_activity_at")
    permissions_refreshed_at = models.DateTimeField(
        null=True, blank=True, db_column="permissions_refreshed_at"
    )
    revoked_at = models.DateTimeField(null=True, blank=True, db_column="revoked_at")
    revocation_reason = models.CharField(
        max_length=30,
        choices=MOTIVOS_REVOCACION_SESION,
        null=True,
        blank=True,
        db_column="revocation_reason",
    )

    class Meta:
        managed = False
        db_table = "sesion_usuario"
        verbose_name = "sesion de usuario"
        verbose_name_plural = "sesiones de usuario"
        ordering = ["-issued_at"]

    def __str__(self) -> str:
        estado = "revocada" if self.revoked_at is not None else "vigente"
        return f"Sesion {self.session_id} del usuario {self.user_id} ({estado})"


class IncidenciaEntity(AtribucionMixin, SinBorradoFisicoMixin, models.Model):
    """
    Incidencia de sala con su clasificacion, su estado y su responsable (ARC-113, `incidencia`).

    No hay ruta de borrado fisico (REQ-047): una incidencia se cierra, nunca se elimina, y su
    retencion la gobierna la columna virtual `retention_expires_at` (created_at + 24 meses).

    Las columnas `*_snapshot` congelan la denominacion vigente en el alta y son INMUTABLES:
    el trigger `trg_incidencia_snapshot_ro` rechaza cualquier UPDATE con ORA-20010 (REQ-150).

    Atribucion (REQ-064): el autor del alta es `reported_by`, que se toma del contexto de
    sesion y NUNCA del payload; por eso el modelo redefine `CAMPO_ACTOR_ALTA`. La tabla no
    tiene columna de actor de modificacion (`updated_by` no existe en el DDL), asi que el
    mixin solo refresca `updated_at`; el autor de una transicion lo sella explicitamente el
    servicio de ciclo de vida en `status_changed_by`.
    """

    CAMPO_ACTOR_ALTA = "reported_by"
    CAMPO_FECHA_ALTA = "created_at"
    CAMPO_FECHA_MODIFICACION = "updated_at"

    incident_id = models.AutoField(primary_key=True, db_column="incident_id")
    reference_code = models.CharField(max_length=30, unique=True, db_column="reference_code")
    room = models.ForeignKey(
        SalaEntity,
        on_delete=models.DO_NOTHING,
        db_column="room_id",
        related_name="incidencias",
    )
    category = models.ForeignKey(
        CategoriaIncidenciaEntity,
        on_delete=models.DO_NOTHING,
        db_column="category_id",
        related_name="incidencias",
    )
    room_name_snapshot = models.CharField(max_length=120, db_column="room_name_snapshot")
    office_name_snapshot = models.CharField(max_length=120, db_column="office_name_snapshot")
    category_name_snapshot = models.CharField(max_length=60, db_column="category_name_snapshot")
    description = models.CharField(max_length=500, db_column="description")
    # Estado vigente; la columna fisica es `status` y referencia a cat_estado_incidencia.
    status = models.ForeignKey(
        EstadoIncidenciaEntity,
        on_delete=models.DO_NOTHING,
        default="ABIERTA",
        db_column="status",
        related_name="incidencias",
    )
    reported_by = models.ForeignKey(
        UsuarioEntity,
        on_delete=models.DO_NOTHING,
        db_column="reported_by",
        related_name="incidencias_reportadas",
    )

    # Ciclo de vida: asignacion, liberacion, resolucion y cierre.
    assigned_technician = models.ForeignKey(
        UsuarioEntity,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="assigned_technician_id",
        related_name="incidencias_asignadas",
    )
    assigned_at = models.DateTimeField(null=True, blank=True, db_column="assigned_at")
    released_at = models.DateTimeField(null=True, blank=True, db_column="released_at")
    released_by = models.ForeignKey(
        UsuarioEntity,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="released_by",
        related_name="incidencias_liberadas",
    )
    release_reason = models.CharField(
        max_length=500, null=True, blank=True, db_column="release_reason"
    )
    in_progress_at = models.DateTimeField(null=True, blank=True, db_column="in_progress_at")
    resolved_at = models.DateTimeField(null=True, blank=True, db_column="resolved_at")
    closed_at = models.DateTimeField(null=True, blank=True, db_column="closed_at")
    closed_by = models.ForeignKey(
        UsuarioEntity,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="closed_by",
        related_name="incidencias_cerradas",
    )
    resolution_comment = models.TextField(
        null=True, blank=True, db_column="resolution_comment"
    )
    status_changed_at = models.DateTimeField(default=utc_now, db_column="status_changed_at")
    status_changed_by = models.ForeignKey(
        UsuarioEntity,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="status_changed_by",
        related_name="transiciones_de_incidencia_ejecutadas",
    )

    created_at = models.DateTimeField(default=utc_now, db_column="created_at")
    updated_at = models.DateTimeField(null=True, blank=True, db_column="updated_at")
    # Concurrencia optimista: se incrementa en cada transicion aceptada.
    version = models.IntegerField(default=0, db_column="version")

    class Meta:
        managed = False
        db_table = "incidencia"
        verbose_name = "incidencia"
        verbose_name_plural = "incidencias"
        ordering = ["-created_at", "-incident_id"]

    def __str__(self) -> str:
        return f"{self.reference_code} ({self.status_id})"


class IncidenciaAdjuntoEntity(SinBorradoFisicoMixin, models.Model):
    """
    Metadatos del adjunto de una incidencia (ARC-114, tabla `incidencia_adjunto`).

    Como maximo un adjunto por incidencia (UNIQUE `uk_incidencia_adjunto_incident` en el DDL)
    y sin borrado fisico (REQ-047): la fila sobrevive al cierre de la incidencia porque el
    checksum es la evidencia que se verifica en cada descarga (AC-RET-07).

    El binario NO vive aqui: `storage_key` es la clave opaca del almacen de ficheros.
    """

    attachment_id = models.AutoField(primary_key=True, db_column="attachment_id")
    # La unicidad de incident_id la impone el DDL; no se declara aqui para no duplicarla.
    incident = models.ForeignKey(
        IncidenciaEntity,
        on_delete=models.DO_NOTHING,
        db_column="incident_id",
        related_name="adjuntos",
    )
    file_name = models.CharField(max_length=255, db_column="file_name")
    mime_type = models.CharField(
        max_length=50, choices=MIME_TYPES_ADJUNTO, db_column="mime_type"
    )
    file_size_bytes = models.IntegerField(db_column="file_size_bytes")
    # SHA-256 en hexadecimal: CHAR(64) en el DDL.
    file_checksum = models.CharField(max_length=64, db_column="file_checksum")
    storage_key = models.CharField(max_length=255, unique=True, db_column="storage_key")
    uploaded_by = models.ForeignKey(
        UsuarioEntity,
        on_delete=models.DO_NOTHING,
        db_column="uploaded_by",
        related_name="adjuntos_subidos",
    )
    uploaded_at = models.DateTimeField(default=utc_now, db_column="uploaded_at")
    stored_at = models.DateTimeField(
        default=utc_now, null=True, blank=True, db_column="stored_at"
    )

    class Meta:
        managed = False
        db_table = "incidencia_adjunto"
        verbose_name = "adjunto de incidencia"
        verbose_name_plural = "adjuntos de incidencia"
        ordering = ["-uploaded_at", "-attachment_id"]

    def __str__(self) -> str:
        return f"Adjunto {self.file_name} de la incidencia {self.incident_id}"


class AvisoCorreoEntity(SinBorradoFisicoMixin, models.Model):
    """
    Solicitud de aviso por correo con su contenido congelado (ARC-115, tabla `aviso_correo`).

    Es la COLA de envio (REQ-134, REQ-142): `status` recorre PENDIENTE -> ENVIANDO ->
    ENVIADO | FALLIDO | DESCARTADO | SUPRIMIDO, con `attempt_count` acotado por `max_attempts`.
    Una solicitud nunca se borra (REQ-047): queda como traza del aviso, incluso suprimida.

    `notification_key` da la idempotencia por tipo + incidencia + entrada de historial, de modo
    que un reproceso no duplica el aviso. `notification_id` es un uuid generado en Python.
    """

    notification_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=generar_identificador_uuid,
        db_column="notification_id",
    )
    notification_key = models.CharField(
        max_length=120, unique=True, db_column="notification_key"
    )
    notification_type = models.CharField(
        max_length=40, choices=TIPOS_AVISO_CORREO, db_column="notification_type"
    )
    incident = models.ForeignKey(
        IncidenciaEntity,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="incident_id",
        related_name="avisos_correo",
    )
    # Referencia perezosa: `incidencia_historico` vive en otro modulo de modelos del mismo app.
    history_entry = models.ForeignKey(
        "core.IncidenciaHistoricoEntity",
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="history_entry_id",
        related_name="avisos_correo",
    )
    recipient_user = models.ForeignKey(
        UsuarioEntity,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="recipient_user_id",
        related_name="avisos_recibidos",
    )
    recipient_email = models.CharField(
        max_length=254, null=True, blank=True, db_column="recipient_email"
    )

    # Contenido congelado en la composicion; inmutable una vez compuesto.
    subject = models.CharField(max_length=255, null=True, blank=True, db_column="subject")
    body_text = models.TextField(null=True, blank=True, db_column="body_text")
    body_html = models.TextField(null=True, blank=True, db_column="body_html")

    status = models.CharField(
        max_length=20,
        choices=ESTADOS_AVISO_CORREO,
        default="PENDIENTE",
        db_column="status",
    )
    previous_status_code = models.ForeignKey(
        EstadoIncidenciaEntity,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="previous_status_code",
        related_name="avisos_con_estado_anterior",
    )
    new_status_code = models.ForeignKey(
        EstadoIncidenciaEntity,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="new_status_code",
        related_name="avisos_con_estado_nuevo",
    )
    suppression_reason_code = models.CharField(
        max_length=30,
        choices=MOTIVOS_SUPRESION_AVISO,
        null=True,
        blank=True,
        db_column="suppression_reason_code",
    )
    suppressed_at = models.DateTimeField(null=True, blank=True, db_column="suppressed_at")

    # Reintentos y toma de la solicitud por un trabajador.
    attempt_count = models.IntegerField(default=0, db_column="attempt_count")
    max_attempts = models.IntegerField(default=3, db_column="max_attempts")
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_column="next_attempt_at")
    locked_by = models.CharField(
        max_length=60, null=True, blank=True, db_column="locked_by"
    )
    locked_at = models.DateTimeField(null=True, blank=True, db_column="locked_at")
    last_error_code = models.CharField(
        max_length=50, null=True, blank=True, db_column="last_error_code"
    )
    # Nunca contiene credenciales SMTP ni la contrasenia del buzon emisor (REQ-063).
    last_error_message = models.CharField(
        max_length=500, null=True, blank=True, db_column="last_error_message"
    )

    message_id = models.CharField(
        max_length=255, null=True, blank=True, db_column="message_id"
    )
    sent_at = models.DateTimeField(null=True, blank=True, db_column="sent_at")
    resent_by_user = models.ForeignKey(
        UsuarioEntity,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        db_column="resent_by_user_id",
        related_name="avisos_reenviados",
    )
    resent_at = models.DateTimeField(null=True, blank=True, db_column="resent_at")
    # Orden FIFO de procesamiento de la cola.
    created_at = models.DateTimeField(default=utc_now, db_column="created_at")

    class Meta:
        managed = False
        db_table = "aviso_correo"
        verbose_name = "aviso por correo"
        verbose_name_plural = "avisos por correo"
        ordering = ["-created_at", "-notification_id"]

    def __str__(self) -> str:
        return f"Aviso {self.notification_type} [{self.status}] {self.notification_key}"


__all__ = [
    "ALGORITMOS_PASSWORD",
    "ESTADOS_AVISO_CORREO",
    "ESTADOS_USUARIO",
    "INDICADOR_SN",
    "MIME_TYPES_ADJUNTO",
    "MOTIVOS_REVOCACION_SESION",
    "MOTIVOS_SUPRESION_AVISO",
    "TIPOS_AVISO_CORREO",
    "AvisoCorreoEntity",
    "IncidenciaAdjuntoEntity",
    "IncidenciaEntity",
    "SesionUsuarioEntity",
    "UsuarioEntity",
    "UsuarioPasswordHistoricoEntity",
    "generar_identificador_uuid",
]
