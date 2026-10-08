"""
Servicio de sesiones: UNICO sitio donde una sesion se valida, se emite y se revoca.

La sesion es OPACA y SERVER-SIDE (ARC-112): el cliente solo maneja un uuid sin significado y
todo el estado (vigencia, actividad y revocacion) vive en la tabla `sesion_usuario` de
Oracle, a la que se accede por el ORM de Django con el driver `python-oracledb`. No hay, ni
puede haber, ningun diccionario ni cache en memoria haciendo de almacen de sesiones: un
reinicio o una segunda replica invalidarian la revocacion inmediata que exige REQ-057.

VIGENCIA (REQ-055, REQ-056). Dos limites independientes, ambos parametrizados por entorno:

* ABSOLUTO: `expires_at`, sellado en la emision, siempre posterior a `issued_at`.
* INACTIVIDAD: `last_activity_at` mas `SESION_INACTIVIDAD_MINUTOS`.

Cuando cualquiera de los dos vence, la sesion se REVOCA en la base (`revoked_at` +
`revocation_reason = "expired"`), no se borra (REQ-047, REQ-070), y queda como evidencia.

RESPUESTA UNIFORME (AC-SES-04). Todos los fallos de validacion lanzan la MISMA
`SesionInvalidaError` con el mismo texto; el `motivo` ("ausente", "no_encontrada",
"revocada", "caducada", "inactividad", "usuario_inactivo") es solo traza interna.

ROL CONGELADO (REQ-011, REQ-018). El `role_code` de `sesion_usuario` es el rol congelado en
la EMISION de la sesion. Se conserva como EVIDENCIA HISTORICA de con que rol se emitio y como
base de comparacion para detectar un cambio de rol, pero YA NO ES LA FUENTE DE LA
AUTORIZACION: el rol efectivo de cada peticion lo relee de la tabla `usuario`
`ResolutorContextoSesion` (apps.core_security.servicios.contexto), al que `validar()` delega.

SECRETOS (REQ-063, REQ-076). Ni la contrasenia en claro, ni `password_hash`, ni el
identificador de sesion se escriben jamas en logs, trazas ni mensajes de error.
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.http import HttpRequest

from apps.core.contexto import ContextoSesion, contexto_de_sesion, utc_now
from apps.core.models.transaccional import SesionUsuarioEntity, UsuarioEntity
from apps.core_security.errores import (
    CredencialesInvalidasError,
    CuentaBloqueadaError,
    SesionInvalidaError,
)


ESTADO_USUARIO_ACTIVO = "ACTIVO"

# Valores del enumerado cerrado `ck_sesion_usuario_reason_enum`, en minusculas y literales.
MOTIVO_REVOCACION_CADUCIDAD = "expired"
MOTIVO_REVOCACION_LOGOUT = "logout"

# Valores por defecto de vigencia si el entorno no los define (el settings los parametriza).
INACTIVIDAD_MINUTOS_POR_DEFECTO = 30
VIGENCIA_ABSOLUTA_HORAS_POR_DEFECTO = 12


class ServicioSesiones:
    """
    Ciclo de vida de la sesion de usuario contra Oracle.

    Cuatro operaciones y ninguna mas: `validar` (cada peticion protegida), `autenticar`
    (inicio de sesion), `emitir` (alta de la sesion) y `revocar` (cierre o revocacion
    administrativa).
    """

    @property
    def inactividad_minutos(self) -> int:
        """Minutos de inactividad tolerados antes de caducar la sesion (parametrizable por entorno)."""

        return int(getattr(settings, "SESION_INACTIVIDAD_MINUTOS", INACTIVIDAD_MINUTOS_POR_DEFECTO))

    @property
    def vigencia_absoluta_horas(self) -> int:
        """Horas de vida maxima de la sesion desde su emision (parametrizable por entorno)."""

        return int(getattr(settings, "SESION_VIGENCIA_ABSOLUTA_HORAS", VIGENCIA_ABSOLUTA_HORAS_POR_DEFECTO))

    # --- Validacion ------------------------------------------------------

    def validar(self, credencial: str | None, *, request: HttpRequest | None = None) -> ContextoSesion:
        """
        Valida la credencial de sesion y devuelve el contexto de la identidad efectiva.

        Lanza SIEMPRE `SesionInvalidaError` ante cualquier fallo, con el mismo mensaje y el
        motivo real solo en la traza interna.

        ROL VIGENTE, NO CONGELADO (REQ-011, REQ-018, AC-PERM-05). El `role_code` del contexto
        devuelto es el VIGENTE EN LA TABLA `usuario`, releido de la base en CADA peticion a
        partir del `session_user_id`, y NO el que quedo congelado en `sesion_usuario` al emitir
        la sesion. Por eso un cambio de rol hecho por el ADMINISTRADOR surte efecto en la
        SIGUIENTE peticion del usuario, sin obligarle a volver a iniciar sesion. Si el rol no se
        puede resolver, el resolutor deniega FAIL-CLOSED con `RolNoResolubleError` (403).

        Cualquier rol, alcance o `user_id` que venga del cliente (cuerpo, query string o
        cabecera) se descarta en SILENCIO: no produce error y no participa en ninguna decision
        (REQ-018). `request` es OPCIONAL y se usa UNICAMENTE para dejar traza a nivel DEBUG de
        lo que se ha ignorado; la identidad jamas se extrae de ahi.

        El `data_scope` del contexto NO se calcula aqui: se deja el valor por defecto del
        dataclass. El alcance efectivo de cada operacion lo resuelve la capa de autorizacion
        contra la matriz `permiso_rol_operacion`, que no es responsabilidad de este servicio.
        """

        # Import local a proposito: `servicios.contexto` importa `ESTADO_USUARIO_ACTIVO` de este
        # modulo, y un import a nivel de modulo en sentido contrario crearia un ciclo.
        from apps.core_security.servicios.contexto import ResolutorContextoSesion

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
            self._sellar_caducidad(sesion, ahora)
            raise SesionInvalidaError(motivo="caducada")

        if sesion.last_activity_at + timedelta(minutes=self.inactividad_minutos) <= ahora:
            self._sellar_caducidad(sesion, ahora)
            raise SesionInvalidaError(motivo="inactividad")

        if sesion.user.status != ESTADO_USUARIO_ACTIVO:
            raise SesionInvalidaError(motivo="usuario_inactivo")

        # REQ-056 (AC-SES-01): la marca de actividad se refresca en CADA peticion aceptada.
        sesion.last_activity_at = ahora
        sesion.save(update_fields=["last_activity_at"])

        resolutor = ResolutorContextoSesion()
        if request is not None:
            return resolutor.resolver_desde_peticion(request, sesion)
        return resolutor.resolver(sesion)

    def _sellar_caducidad(self, sesion: SesionUsuarioEntity, ahora: datetime) -> None:
        """Sella la revocacion por caducidad UNA sola vez; si ya estaba revocada no la reescribe."""

        if sesion.revoked_at is not None:
            return
        sesion.revoked_at = ahora
        sesion.revocation_reason = MOTIVO_REVOCACION_CADUCIDAD
        sesion.save(update_fields=["revoked_at", "revocation_reason"])

    # --- Inicio de sesion ------------------------------------------------

    def autenticar(self, username: str, password: str) -> UsuarioEntity:
        """
        Comprueba las credenciales de inicio de sesion y devuelve el usuario autenticado.

        Acepta el `username` exacto o el correo corporativo (la unicidad del correo es
        insensible a mayusculas y espacios, REQ-045). Usuario inexistente, usuario inactivo y
        contrasenia incorrecta devuelven la MISMA `CredencialesInvalidasError`, de modo que la
        respuesta no revela si la cuenta existe.
        """

        usuario = self._buscar_usuario(username)
        if usuario is None:
            raise CredencialesInvalidasError()

        if usuario.status != ESTADO_USUARIO_ACTIVO:
            raise CredencialesInvalidasError()

        ahora = utc_now()
        if usuario.locked_until is not None and usuario.locked_until > ahora:
            raise CuentaBloqueadaError()

        if not check_password(password, usuario.password_hash):
            # El contador de intentos fallidos y el umbral de bloqueo (REQ-072) se gestionan
            # fuera de este servicio: aqui solo se lee `locked_until`, nunca se escribe.
            raise CredencialesInvalidasError()

        usuario.last_login_at = ahora
        # El usuario es su propio actor de atribucion en el inicio de sesion: todavia no hay
        # contexto publicado y `AtribucionMixin.save()` lo exige.
        contexto = ContextoSesion(
            user_id=usuario.user_id,
            role_code=usuario.role_code_id,
            display_name=usuario.full_name,
        )
        with contexto_de_sesion(contexto):
            usuario.save(update_fields=["last_login_at"])
        return usuario

    def _buscar_usuario(self, username: str) -> UsuarioEntity | None:
        """Localiza al usuario por `username` exacto y, si no aparece, por correo corporativo normalizado."""

        identificador = (username or "").strip()
        if not identificador:
            return None

        usuario = UsuarioEntity.objects.select_related("role_code").filter(username=identificador).first()
        if usuario is not None:
            return usuario

        correo = identificador.lower()
        return UsuarioEntity.objects.select_related("role_code").filter(corporate_email__iexact=correo).first()

    # --- Emision y revocacion --------------------------------------------

    def emitir(self, usuario: UsuarioEntity) -> SesionUsuarioEntity:
        """
        Crea la sesion del usuario y devuelve la fila persistida.

        `session_id` lo genera el default uuid del modelo; `role_code` congela el rol VIGENTE
        en el instante de la emision, leido de la base, y `expires_at` queda siempre posterior
        a `issued_at` (vencimiento ABSOLUTO, REQ-055).

        Ese `role_code` congelado es EVIDENCIA HISTORICA y base de comparacion para detectar un
        cambio de rol; la autorizacion de cada peticion NO lo usa (REQ-018).
        """

        ahora = utc_now()
        return SesionUsuarioEntity.objects.create(
            user=usuario,
            role_code_id=usuario.role_code_id,
            issued_at=ahora,
            expires_at=ahora + timedelta(hours=self.vigencia_absoluta_horas),
            last_activity_at=ahora,
        )

    def revocar(self, session_id: str, motivo: str = MOTIVO_REVOCACION_LOGOUT) -> None:
        """
        Revoca la sesion indicada sellando `revoked_at` y `revocation_reason` (REQ-057).

        La fila NUNCA se borra (REQ-047, REQ-070). `motivo` debe ser uno de los valores en
        minusculas del enumerado del DDL: logout|expired|admin|password_change|account_deactivated.
        Si la sesion no existe o ya estaba revocada, la operacion no hace nada.
        """

        sesion = SesionUsuarioEntity.objects.filter(pk=session_id, revoked_at__isnull=True).first()
        if sesion is None:
            return
        sesion.revoked_at = utc_now()
        sesion.revocation_reason = motivo
        sesion.save(update_fields=["revoked_at", "revocation_reason"])
