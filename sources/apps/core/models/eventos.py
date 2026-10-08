"""
Tablas de traza INMUTABLE (append-only) del esquema T.5: historicos, auditoria y logs de evento.

Cinco tablas que son evidencia, no estado: `usuario_historico` (ARC-116), `auditoria_acceso`
(ARC-117), `incidencia_historico` (ARC-118), `aviso_correo_intento` (ARC-119) y
`resolucion_destinatario_log` (ARC-120).

Invariantes que este modulo hace cumplir desde el ORM:

* **`managed = False`**: el esquema lo crea y lo versiona Liquibase (changelogs `ddl/07`, `09`,
  `13`, `15` y `16` de la 0.0.1). Django NUNCA crea, altera ni borra estas tablas; se limita a
  leerlas y a insertar en ellas con los nombres fisicos EXACTOS del DDL.
* **Append-only, retencion de 2 anios** (REQ-015, REQ-065): las cinco entidades heredan de
  `RegistroInmutableMixin`, que admite el INSERT y rechaza cualquier UPDATE o DELETE posterior.
  Dentro del plazo de retencion no hay modificacion ni borrado fisico de una entrada ya escrita;
  en la base de datos lo respaldan ademas los triggers `trg_*_inmutable` (ORA-20003).
* **Atribucion desde el contexto de sesion, nunca del payload** (REQ-064, AC-TRZ-01): el actor y
  la marca temporal de cada asiento los pone `AtribucionMixin` a partir de `ContextoSesion` y de
  `utc_now()`. Un `user_id` o una fecha que lleguen en la peticion se IGNORAN.

Columnas VIRTUALES: varias de las columnas del inventario T.5 son, en el DDL real, columnas
`GENERATED ALWAYS AS (...) VIRTUAL` calculadas sobre las columnas fisicas de escritura (Oracle
responde ORA-54013 a quien intente escribirlas). Se mapean con `models.GeneratedField(...,
db_persist=False)`, que es de solo lectura y queda fuera del INSERT, de modo que el modelo expone
el nombre publico del inventario sin romper la escritura.
"""

from typing import Any
from uuid import uuid4

from django.db import models
from django.db.models import Case, F, Func, Value, When
from django.db.models.functions import Cast, Coalesce, Concat, Substr

from apps.core.contexto import utc_now
from apps.core.modelos_base import AtribucionMixin, RegistroInmutableMixin
from apps.core.models.catalogos import (
    CategoriaIncidenciaEntity,
    EstadoIncidenciaEntity,
    MotivoDesactivacionEntity,
    RolEntity,
    SalaEntity,
)

# Indicador booleano de Oracle: CHAR(1) con 'Y' o 'N'; el esquema no usa NUMBER(1) ni BOOLEAN.
INDICADOR_SN: tuple[tuple[str, str], ...] = (("Y", "Si"), ("N", "No"))


def nuevo_uuid() -> str:
    """Identificador uuid de 36 caracteres generado en PYTHON, nunca con una funcion de la base de datos."""

    return str(uuid4())


class _RetencionVeinticuatroMeses(Func):
    """Replica de la expresion de la columna virtual `auditoria_acceso.retention_until` del DDL."""

    template = "CAST(%(expressions)s + INTERVAL '24' MONTH AS DATE)"
    output_field = models.DateField()


class _CardinalidadJson(Func):
    """Replica de la expresion de la columna virtual `resolucion_destinatario_log.resolved_ids_cardinalidad`."""

    template = "JSON_VALUE(%(expressions)s, '$.size()' RETURNING NUMBER NULL ON ERROR)"
    output_field = models.IntegerField()


class _JsonSoloNumeros(Func):
    """Replica de la expresion de la columna virtual `resolucion_destinatario_log.resolved_ids_solo_numeros`."""

    template = "CASE WHEN JSON_EXISTS(%(expressions)s, '$[*]?(@.type() <> \"number\")') THEN 0 ELSE 1 END"
    output_field = models.IntegerField()


class UsuarioHistoricoEntity(AtribucionMixin, RegistroInmutableMixin, models.Model):
    """
    Traza inmutable de los cambios de rol y de estado de cuenta de un usuario (ARC-116).

    El DDL desdobla el valor anterior y el nuevo en columnas fisicas por dimension
    (`previous_role_code` / `new_role_code` y `previous_status` / `new_status`) para poder declarar
    claves ajenas reales contra `cat_rol`; `event_type`, `previous_value` y `new_value` son las
    columnas VIRTUALES que publican sobre ellas los nombres del inventario T.5.
    """

    # `AtribucionMixin`: el actor y la fecha del asiento son `changed_by` y `changed_at`. La entrada
    # es append-only, asi que NO existen columnas de modificacion; el mixin ignora las que no se
    # declaran, por lo que no se reapuntan `CAMPO_ACTOR_MODIFICACION` ni `CAMPO_FECHA_MODIFICACION`.
    CAMPO_ACTOR_ALTA = "changed_by"
    CAMPO_FECHA_ALTA = "changed_at"

    history_id = models.AutoField(primary_key=True, db_column="history_id")
    user = models.ForeignKey(
        "core.UsuarioEntity",
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        related_name="historicos_rol_estado",
    )
    change_type = models.CharField(max_length=20, db_column="change_type")
    previous_role_code = models.ForeignKey(
        RolEntity,
        on_delete=models.DO_NOTHING,
        db_column="previous_role_code",
        related_name="historicos_usuario_como_rol_anterior",
        null=True,
        blank=True,
    )
    new_role_code = models.ForeignKey(
        RolEntity,
        on_delete=models.DO_NOTHING,
        db_column="new_role_code",
        related_name="historicos_usuario_como_rol_nuevo",
        null=True,
        blank=True,
    )
    previous_status = models.CharField(max_length=10, db_column="previous_status", null=True, blank=True)
    new_status = models.CharField(max_length=10, db_column="new_status", null=True, blank=True)
    reason_code = models.ForeignKey(
        MotivoDesactivacionEntity,
        on_delete=models.DO_NOTHING,
        db_column="reason_code",
        related_name="historicos_usuario",
        null=True,
        blank=True,
    )
    note = models.CharField(max_length=500, db_column="note", null=True, blank=True)
    # valid_from abre la vigencia del valor nuevo y valid_to cierra la del anterior (nulo en el alta).
    valid_from = models.DateTimeField(db_column="valid_from", default=utc_now)
    valid_to = models.DateTimeField(db_column="valid_to", null=True, blank=True)
    changed_by = models.ForeignKey(
        "core.UsuarioEntity",
        on_delete=models.DO_NOTHING,
        db_column="changed_by",
        related_name="historicos_rol_estado_registrados",
    )
    # El DDL trae DEFAULT SYS_EXTRACT_UTC(SYSTIMESTAMP), pero Oracle solo lo aplica si la columna
    # se omite de la sentencia: Django siempre la nombra, asi que el sello va tambien en Python.
    changed_at = models.DateTimeField(db_column="changed_at", default=utc_now)

    # Columnas VIRTUALES del DDL (solo lectura, fuera del INSERT).
    event_type = models.GeneratedField(
        expression=Cast("change_type", models.CharField(max_length=20)),
        output_field=models.CharField(max_length=20),
        db_persist=False,
        db_column="event_type",
    )
    previous_value = models.GeneratedField(
        expression=Coalesce(
            Cast("previous_role_code", models.CharField(max_length=32)),
            Cast("previous_status", models.CharField(max_length=32)),
            output_field=models.CharField(max_length=32),
        ),
        output_field=models.CharField(max_length=32),
        db_persist=False,
        db_column="previous_value",
    )
    new_value = models.GeneratedField(
        expression=Coalesce(
            Cast("new_role_code", models.CharField(max_length=32)),
            Cast("new_status", models.CharField(max_length=32)),
            output_field=models.CharField(max_length=32),
        ),
        output_field=models.CharField(max_length=32),
        db_persist=False,
        db_column="new_value",
    )

    class Meta:
        managed = False
        db_table = "usuario_historico"
        verbose_name = "historico de usuario"
        verbose_name_plural = "historicos de usuario"
        ordering = ["-changed_at", "-history_id"]

    def __str__(self) -> str:
        return f"Historico {self.history_id} de usuario {self.user_id}: {self.change_type} el {self.changed_at}"


class AuditoriaAccesoEntity(RegistroInmutableMixin, models.Model):
    """
    Registro inmutable de eventos de acceso y de operaciones sensibles sobre credenciales (ARC-117).

    No usa `AtribucionMixin` a proposito: el actor de un evento de acceso puede ser NULO. Un intento
    fallido contra un usuario inexistente no tiene `user_id` que atribuir (solo el
    `username_attempted` tecleado), y el contexto de sesion todavia no existe cuando se registra el
    propio login. La marca temporal, en cambio, es SIEMPRE del servidor: la sella `save()` con
    `utc_now()` y, en la base de datos, el trigger `trg_auditoria_acceso_servidor` la vuelve a fijar
    en cada INSERT, de modo que un cliente no puede antedatar un evento.
    """

    audit_id = models.CharField(primary_key=True, max_length=36, db_column="audit_id", default=nuevo_uuid)
    user = models.ForeignKey(
        "core.UsuarioEntity",
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        related_name="eventos_auditoria_acceso",
        null=True,
        blank=True,
    )
    username_attempted = models.CharField(max_length=150, db_column="username_attempted", null=True, blank=True)
    event_type = models.CharField(max_length=30, db_column="event_type")
    operation = models.CharField(max_length=100, db_column="operation", null=True, blank=True)
    outcome = models.CharField(max_length=20, db_column="outcome")
    occurred_at = models.DateTimeField(db_column="occurred_at")
    ip_address = models.CharField(max_length=45, db_column="ip_address", null=True, blank=True)
    user_agent = models.CharField(max_length=255, db_column="user_agent", null=True, blank=True)
    # session_id guarda el IDENTIFICADOR de la sesion, nunca su credencial. El DDL lo declara SIN
    # clave ajena a proposito (changelog ddl/09), para que la auditoria sobreviva a la sesion.
    session_id = models.CharField(max_length=36, db_column="session_id", null=True, blank=True)

    # Columna VIRTUAL del DDL: occurred_at + 24 meses, el horizonte de retencion de 2 anios.
    retention_until = models.GeneratedField(
        expression=_RetencionVeinticuatroMeses(F("occurred_at")),
        output_field=models.DateField(),
        db_persist=False,
        db_column="retention_until",
    )

    class Meta:
        managed = False
        db_table = "auditoria_acceso"
        verbose_name = "evento de auditoria de acceso"
        verbose_name_plural = "eventos de auditoria de acceso"
        ordering = ["-occurred_at"]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Sella la marca temporal con la hora del servidor si no viene informada; nunca la toma del cliente."""

        if self.occurred_at is None:
            self.occurred_at = utc_now()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        actor = self.user_id if self.user_id is not None else (self.username_attempted or "desconocido")
        return f"Auditoria {self.event_type} ({self.outcome}) de {actor} el {self.occurred_at}"


class IncidenciaHistoricoEntity(AtribucionMixin, RegistroInmutableMixin, models.Model):
    """
    Traza append-only de alta, cambios de estado, asignaciones y reclasificaciones de una incidencia (ARC-118).

    Igual que `usuario_historico`, el DDL desdobla en columnas fisicas el valor anterior y el nuevo
    de cada dimension (estado, sala, categoria y tecnico) para declarar claves ajenas reales;
    `value_before` y `value_after` son las columnas VIRTUALES que publican los nombres del
    inventario T.5. Cada asiento queda encadenado por huella (`previous_entry_hash` / `entry_hash`)
    para que una manipulacion posterior sea detectable.
    """

    # `AtribucionMixin`: actor `actor_user` (columna `actor_user_id`) y fecha `changed_at`. No hay
    # columnas de modificacion porque la entrada es append-only.
    CAMPO_ACTOR_ALTA = "actor_user"
    CAMPO_FECHA_ALTA = "changed_at"

    history_id = models.AutoField(primary_key=True, db_column="history_id")
    incident = models.ForeignKey(
        "core.IncidenciaEntity",
        on_delete=models.DO_NOTHING,
        db_column="incident_id",
        related_name="historicos",
    )
    entry_type = models.CharField(max_length=30, db_column="entry_type")
    from_status = models.ForeignKey(
        EstadoIncidenciaEntity,
        on_delete=models.DO_NOTHING,
        db_column="from_status",
        related_name="historicos_incidencia_como_estado_origen",
        null=True,
        blank=True,
    )
    to_status = models.ForeignKey(
        EstadoIncidenciaEntity,
        on_delete=models.DO_NOTHING,
        db_column="to_status",
        related_name="historicos_incidencia_como_estado_destino",
    )
    previous_room = models.ForeignKey(
        SalaEntity,
        on_delete=models.DO_NOTHING,
        db_column="previous_room_id",
        related_name="historicos_incidencia_como_sala_anterior",
        null=True,
        blank=True,
    )
    new_room = models.ForeignKey(
        SalaEntity,
        on_delete=models.DO_NOTHING,
        db_column="new_room_id",
        related_name="historicos_incidencia_como_sala_nueva",
        null=True,
        blank=True,
    )
    previous_category = models.ForeignKey(
        CategoriaIncidenciaEntity,
        on_delete=models.DO_NOTHING,
        db_column="previous_category_id",
        related_name="historicos_incidencia_como_categoria_anterior",
        null=True,
        blank=True,
    )
    new_category = models.ForeignKey(
        CategoriaIncidenciaEntity,
        on_delete=models.DO_NOTHING,
        db_column="new_category_id",
        related_name="historicos_incidencia_como_categoria_nueva",
        null=True,
        blank=True,
    )
    previous_technician = models.ForeignKey(
        "core.UsuarioEntity",
        on_delete=models.DO_NOTHING,
        db_column="previous_technician_id",
        related_name="historicos_incidencia_como_tecnico_anterior",
        null=True,
        blank=True,
    )
    assigned_technician = models.ForeignKey(
        "core.UsuarioEntity",
        on_delete=models.DO_NOTHING,
        db_column="assigned_technician_id",
        related_name="historicos_incidencia_como_tecnico_asignado",
        null=True,
        blank=True,
    )
    assigned_technician_name = models.CharField(max_length=150, db_column="assigned_technician_name", null=True, blank=True)
    actor_user = models.ForeignKey(
        "core.UsuarioEntity",
        on_delete=models.DO_NOTHING,
        db_column="actor_user_id",
        related_name="historicos_incidencia_registrados",
    )
    # Instantanea del nombre: conserva la atribucion aunque el actor se desactive despues.
    actor_display_name = models.CharField(max_length=150, db_column="actor_display_name")
    # El DDL trae DEFAULT SYS_EXTRACT_UTC(SYSTIMESTAMP), pero Oracle solo lo aplica si la columna
    # se omite de la sentencia: Django siempre la nombra, asi que el sello va tambien en Python.
    changed_at = models.DateTimeField(db_column="changed_at", default=utc_now)
    entry_comment = models.CharField(max_length=500, db_column="entry_comment", null=True, blank=True)
    resolution_comment_ref = models.ForeignKey(
        "core.IncidenciaEntity",
        on_delete=models.DO_NOTHING,
        db_column="resolution_comment_ref",
        related_name="historicos_comentario_resolucion",
        null=True,
        blank=True,
    )
    previous_entry_hash = models.CharField(max_length=64, db_column="previous_entry_hash", null=True, blank=True)
    entry_hash = models.CharField(max_length=64, db_column="entry_hash")

    # Columnas VIRTUALES del DDL (solo lectura, fuera del INSERT).
    value_before = models.GeneratedField(
        expression=Case(
            When(
                entry_type="RECLASIFICACION",
                then=Substr(
                    Concat(
                        Value("room_id="),
                        "previous_room",
                        Value(";category_id="),
                        "previous_category",
                        output_field=models.CharField(max_length=200),
                    ),
                    1,
                    200,
                ),
            ),
            When(
                entry_type="ASIGNACION",
                then=Substr(
                    Concat(
                        Value("assigned_technician_id="),
                        "previous_technician",
                        output_field=models.CharField(max_length=200),
                    ),
                    1,
                    200,
                ),
            ),
            default=Cast("from_status", models.CharField(max_length=200)),
            output_field=models.CharField(max_length=200),
        ),
        output_field=models.CharField(max_length=200),
        db_persist=False,
        db_column="value_before",
    )
    value_after = models.GeneratedField(
        expression=Case(
            When(
                entry_type="RECLASIFICACION",
                then=Substr(
                    Concat(
                        Value("room_id="),
                        "new_room",
                        Value(";category_id="),
                        "new_category",
                        output_field=models.CharField(max_length=200),
                    ),
                    1,
                    200,
                ),
            ),
            When(
                entry_type="ASIGNACION",
                then=Substr(
                    Concat(
                        Value("assigned_technician_id="),
                        "assigned_technician",
                        output_field=models.CharField(max_length=200),
                    ),
                    1,
                    200,
                ),
            ),
            default=Cast("to_status", models.CharField(max_length=200)),
            output_field=models.CharField(max_length=200),
        ),
        output_field=models.CharField(max_length=200),
        db_persist=False,
        db_column="value_after",
    )

    class Meta:
        managed = False
        db_table = "incidencia_historico"
        verbose_name = "historico de incidencia"
        verbose_name_plural = "historicos de incidencia"
        ordering = ["-changed_at", "-history_id"]

    def __str__(self) -> str:
        return f"Historico {self.history_id} de incidencia {self.incident_id}: {self.entry_type} el {self.changed_at}"


class AvisoCorreoIntentoEntity(RegistroInmutableMixin, models.Model):
    """
    Traza inmutable de cada intento de entrega de un aviso por correo y de su resultado (ARC-119).

    No lleva atribucion de usuario: el intento lo ejecuta el proceso de envio, no una persona, de
    modo que `AtribucionMixin` no aplica. La marca temporal la pone el servidor (`utc_now()`).
    """

    attempt_id = models.CharField(primary_key=True, max_length=36, db_column="attempt_id", default=nuevo_uuid)
    notification = models.ForeignKey(
        "core.AvisoCorreoEntity",
        on_delete=models.DO_NOTHING,
        db_column="notification_id",
        related_name="intentos",
    )
    incident = models.ForeignKey(
        "core.IncidenciaEntity",
        on_delete=models.DO_NOTHING,
        db_column="incident_id",
        related_name="intentos_aviso_correo",
        null=True,
        blank=True,
    )
    attempt_number = models.IntegerField(db_column="attempt_number")
    attempted_at = models.DateTimeField(db_column="attempted_at", default=utc_now)
    result_code = models.CharField(max_length=30, db_column="result_code")
    smtp_response_code = models.CharField(max_length=3, db_column="smtp_response_code", null=True, blank=True)
    error_code = models.CharField(max_length=50, db_column="error_code", null=True, blank=True)
    error_message = models.CharField(max_length=500, db_column="error_message", null=True, blank=True)
    # Solo nombre y correo corporativo de los destinatarios vigentes en el intento.
    recipients_snapshot = models.TextField(db_column="recipients_snapshot", null=True, blank=True)
    recipient_count = models.IntegerField(db_column="recipient_count", default=0)
    message_id = models.CharField(max_length=255, db_column="message_id", null=True, blank=True)
    suppression_reason_code = models.CharField(max_length=30, db_column="suppression_reason_code", null=True, blank=True)

    # Columnas VIRTUALES del DDL (solo lectura, fuera del INSERT): alias publicados sobre
    # notification_id y result_code.
    dispatch_id = models.GeneratedField(
        expression=Cast("notification", models.CharField(max_length=36)),
        output_field=models.CharField(max_length=36),
        db_persist=False,
        db_column="dispatch_id",
    )
    result = models.GeneratedField(
        expression=Cast("result_code", models.CharField(max_length=30)),
        output_field=models.CharField(max_length=30),
        db_persist=False,
        db_column="result",
    )

    class Meta:
        managed = False
        db_table = "aviso_correo_intento"
        verbose_name = "intento de envio de aviso por correo"
        verbose_name_plural = "intentos de envio de aviso por correo"
        ordering = ["-attempted_at"]

    def __str__(self) -> str:
        return f"Intento {self.attempt_number} del aviso {self.notification_id}: {self.result_code} el {self.attempted_at}"


class ResolucionDestinatarioLogEntity(RegistroInmutableMixin, models.Model):
    """
    Registro inmutable de cada resolucion de destinatarios del directorio, individual o de colectivo (ARC-120).

    Guarda identificadores, nunca correos en claro. La consulta la puede lanzar un proceso del
    sistema (sin usuario) o un administrador, de ahi que `resolved_by_user_id` sea anulable y que no
    se aplique `AtribucionMixin`.
    """

    resolution_id = models.AutoField(primary_key=True, db_column="resolution_id")
    request_type = models.CharField(max_length=20, db_column="request_type")
    requested_by_module = models.CharField(max_length=50, db_column="requested_by_module")
    subject_user = models.ForeignKey(
        "core.UsuarioEntity",
        on_delete=models.DO_NOTHING,
        db_column="subject_user_id",
        related_name="resoluciones_destinatario_como_sujeto",
        null=True,
        blank=True,
    )
    # CLOB con la lista JSON de identificadores resueltos; el DDL exige `IS JSON (STRICT)`.
    resolved_user_ids = models.TextField(db_column="resolved_user_ids")
    recipient_count = models.IntegerField(db_column="recipient_count")
    is_fallback_used = models.CharField(max_length=1, db_column="is_fallback_used", choices=INDICADOR_SN, default="N")
    outcome = models.CharField(max_length=30, db_column="outcome")
    resolved_at = models.DateTimeField(db_column="resolved_at", default=utc_now)
    resolved_by_user = models.ForeignKey(
        "core.UsuarioEntity",
        on_delete=models.DO_NOTHING,
        db_column="resolved_by_user_id",
        related_name="resoluciones_destinatario_lanzadas",
        null=True,
        blank=True,
    )

    # Columnas VIRTUALES del DDL (solo lectura, fuera del INSERT): sostienen las restricciones de
    # coherencia de `resolved_user_ids` (cardinalidad y lista de numeros).
    resolved_ids_cardinalidad = models.GeneratedField(
        expression=_CardinalidadJson(F("resolved_user_ids")),
        output_field=models.IntegerField(),
        db_persist=False,
        db_column="resolved_ids_cardinalidad",
    )
    resolved_ids_solo_numeros = models.GeneratedField(
        expression=_JsonSoloNumeros(F("resolved_user_ids")),
        output_field=models.IntegerField(),
        db_persist=False,
        db_column="resolved_ids_solo_numeros",
    )

    class Meta:
        managed = False
        db_table = "resolucion_destinatario_log"
        verbose_name = "resolucion de destinatarios"
        verbose_name_plural = "resoluciones de destinatarios"
        ordering = ["-resolved_at", "-resolution_id"]

    def __str__(self) -> str:
        return f"Resolucion {self.resolution_id} {self.request_type} de {self.requested_by_module}: {self.outcome} ({self.recipient_count})"


__all__ = [
    "INDICADOR_SN",
    "AuditoriaAccesoEntity",
    "AvisoCorreoIntentoEntity",
    "IncidenciaHistoricoEntity",
    "ResolucionDestinatarioLogEntity",
    "UsuarioHistoricoEntity",
    "nuevo_uuid",
]
