"""
Ciclo de vida de la sesion opaca server-side (ARC-013, ARC-112, tabla `sesion_usuario`).

La sesion es OPACA y vive ENTERAMENTE EN SERVIDOR: el cliente solo maneja un uuid sin
significado y todo el estado (vigencia, actividad y revocacion) se persiste en la tabla
`sesion_usuario` de Oracle a traves del ORM. No hay, ni puede haber, ningun diccionario ni
cache en memoria haciendo de almacen de sesiones: un reinicio o una segunda replica
invalidarian la revocacion inmediata que exige REQ-057.

VIGENCIA (REQ-055, REQ-056). Dos limites independientes, ambos parametrizados por entorno y
con valor por defecto, nunca fijados en el fuente:

* ABSOLUTO: `expires_at`, sellado en la emision y siempre posterior a `issued_at`.
* INACTIVIDAD: `last_activity_at` mas `SESION_INACTIVIDAD_MINUTOS`.

Cuando cualquiera de los dos vence, la sesion se REVOCA en la base (`revoked_at` +
`revocation_reason = "expired"`); la fila NUNCA se borra (REQ-047, REQ-070) y queda como
evidencia del acceso.

RESPUESTA UNIFORME (AC-SES-02, AC-SES-04). Todos los fallos de resolucion lanzan la MISMA
`SesionInvalidaError` (401) con el mismo texto; el `motivo` ("ausente", "no_encontrada",
"revocada", "caducada", "inactividad", "usuario_inactivo") es UNICAMENTE traza interna y no
se serializa jamas en la respuesta, para no revelar el estado real de la sesion.

Este modulo PROVEE el resolutor de sesion que consume el middleware de sesion requerida
(`apps.core_security.middleware.SesionRequeridaMiddleware`); no lo redeclara ni lo
reimplementa.

SECRETOS (REQ-063, REQ-076). El identificador de sesion no se escribe jamas en logs, trazas
ni mensajes de error.
"""

from datetime import datetime, timedelta

from django.conf import settings

from apps.core.contexto import utc_now
from apps.core.models import SesionUsuarioEntity, UsuarioEntity
from apps.core_security.errores import SesionInvalidaError


#: Estado de `usuario.status` que habilita a operar; cualquier otro invalida la sesion.
ESTADO_USUARIO_ACTIVO = "ACTIVO"

# Valores del enumerado cerrado `ck_sesion_usuario_reason_enum`, en minusculas y literales.
MOTIVO_REVOCACION_LOGOUT = "logout"
MOTIVO_REVOCACION_CADUCIDAD = "expired"

# Valores por defecto de vigencia si el entorno no los define (el settings los parametriza).
INACTIVIDAD_MINUTOS_POR_DEFECTO = 30
VIGENCIA_ABSOLUTA_HORAS_POR_DEFECTO = 12


class ServicioCicloVidaSesion:
    """
    Emision, resolucion y revocacion de la sesion de usuario contra Oracle.

    Cuatro operaciones publicas y ninguna mas: `emitir` (alta de la sesion tras autenticar),
    `resolver` (cada peticion protegida), `cerrar` (logout idempotente) y `revocar` (cierre
    con cualquier motivo del enumerado del DDL).
    """

    # --- Parametros de vigencia ------------------------------------------

    @property
    def inactividad_minutos(self) -> int:
        """Minutos de inactividad tolerados antes de caducar la sesion (parametrizable por entorno)."""

        return int(getattr(settings, "SESION_INACTIVIDAD_MINUTOS", INACTIVIDAD_MINUTOS_POR_DEFECTO))

    @property
    def vigencia_absoluta_horas(self) -> int:
        """Horas de vida maxima de la sesion desde su emision (parametrizable por entorno)."""

        return int(getattr(settings, "SESION_VIGENCIA_ABSOLUTA_HORAS", VIGENCIA_ABSOLUTA_HORAS_POR_DEFECTO))

    # --- Emision ----------------------------------------------------------

    def emitir(self, usuario: UsuarioEntity) -> SesionUsuarioEntity:
        """
        Crea la fila de `sesion_usuario` del usuario indicado y devuelve la sesion persistida.

        `session_id` lo genera el default uuid del modelo; `role_code` congela el rol VIGENTE
        en el instante de la emision, leido de la BASE y nunca del cliente (REQ-018); y
        `expires_at` queda siempre posterior a `issued_at` (vencimiento ABSOLUTO, REQ-055).
        """

        ahora = utc_now()
        return SesionUsuarioEntity.objects.create(
            user=usuario,
            role_code_id=usuario.role_code_id,
            issued_at=ahora,
            expires_at=ahora + timedelta(hours=self.vigencia_absoluta_horas),
            last_activity_at=ahora,
        )

    # --- Resolucion -------------------------------------------------------

    def resolver(self, credencial: str | None) -> SesionUsuarioEntity:
        """
        Devuelve la fila de `sesion_usuario` si y solo si la sesion es UTILIZABLE.

        Las seis comprobaciones (credencial ausente, sesion inexistente, revocada, caducada por
        vencimiento absoluto, caducada por inactividad y usuario inactivo) lanzan la MISMA
        `SesionInvalidaError` (401) y solo cambian el `motivo`, que es traza interna y nunca
        viaja en la respuesta (AC-SES-02, AC-SES-04).

        Si la sesion supera todas las comprobaciones se refresca `last_activity_at` en la base:
        la marca de actividad se actualiza en CADA peticion aceptada (REQ-056, AC-SES-01).
        """

        if not credencial:
            raise SesionInvalidaError(motivo="ausente")

        sesion = SesionUsuarioEntity.objects.select_related("user", "role_code").filter(pk=credencial).first()
        if sesion is None:
            # Cubre tambien la credencial MANIPULADA: el identificador es un uuid opaco, y si
            # no esta en la tabla simplemente no es una sesion.
            raise SesionInvalidaError(motivo="no_encontrada")

        if sesion.revoked_at is not None:
            raise SesionInvalidaError(motivo="revocada")

        ahora = utc_now()

        if sesion.expires_at <= ahora:
            # Vencimiento ABSOLUTO (REQ-055, AC-SES-04): se sella la caducidad y se deniega.
            self._sellar_caducidad(sesion, ahora)
            raise SesionInvalidaError(motivo="caducada")

        if sesion.last_activity_at + timedelta(minutes=self.inactividad_minutos) <= ahora:
            # Expiracion por INACTIVIDAD (REQ-056, AC-SES-02).
            self._sellar_caducidad(sesion, ahora)
            raise SesionInvalidaError(motivo="inactividad")

        if sesion.user.status != ESTADO_USUARIO_ACTIVO:
            raise SesionInvalidaError(motivo="usuario_inactivo")

        sesion.last_activity_at = ahora
        sesion.save(update_fields=["last_activity_at"])
        return sesion

    def _sellar_caducidad(self, sesion: SesionUsuarioEntity, ahora: datetime) -> None:
        """
        Sella la revocacion por caducidad UNA sola vez sobre la sesion ya cargada.

        Si `revoked_at` ya estaba informado no se reescribe, para no pisar la marca original.
        La fila NUNCA se borra (REQ-047, REQ-070).
        """

        if sesion.revoked_at is not None:
            return
        sesion.revoked_at = ahora
        sesion.revocation_reason = MOTIVO_REVOCACION_CADUCIDAD
        sesion.save(update_fields=["revoked_at", "revocation_reason"])

    # --- Cierre y revocacion ----------------------------------------------

    def cerrar(self, session_id: str) -> None:
        """
        Cierra la sesion indicada sellando el motivo `logout` en SERVIDOR (REQ-057, AC-SES-03).

        Es IDEMPOTENTE: si la sesion no existe o ya estaba revocada no hace nada y no lanza, de
        modo que repetir el cierre produce exactamente el mismo resultado observable y no pisa
        la marca de revocacion original.
        """

        self.revocar(session_id, MOTIVO_REVOCACION_LOGOUT)

    def revocar(self, session_id: str, motivo: str) -> None:
        """
        Revoca la sesion indicada sellando `revoked_at` y `revocation_reason` (REQ-057).

        `motivo` debe ser uno de los valores en minusculas del enumerado del DDL
        `ck_sesion_usuario_reason_enum`: logout|expired|admin|password_change|account_deactivated.

        Solo actua sobre sesiones SIN revocar, asi que la operacion es idempotente; la fila
        NUNCA se borra (REQ-047, REQ-070).
        """

        sesion = SesionUsuarioEntity.objects.filter(pk=session_id, revoked_at__isnull=True).first()
        if sesion is None:
            return
        sesion.revoked_at = utc_now()
        sesion.revocation_reason = motivo
        sesion.save(update_fields=["revoked_at", "revocation_reason"])
