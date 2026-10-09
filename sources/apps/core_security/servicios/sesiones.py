"""
Fachada de ARC-012 sobre el ciclo de vida de sesion que implementa ARC-013 (`apps.identidad`).

QUIEN HACE QUE
--------------
Este modulo YA NO contiene la logica de credenciales ni la de vigencia de sesion: la PROVEE
`apps.identidad` (ARC-013), que es el proveedor unico de esa logica, y aqui solo queda la
FACHADA DELGADA que consume ARC-012 (middleware, autenticador DRF y vistas de sesion).

* `apps.identidad.autenticacion.ServicioAutenticacion` comprueba las credenciales de inicio
  de sesion de forma uniforme E INDISTINGUIBLE EN TIEMPO (AC-AUT-02, AC-ACC-02), que es el
  endurecimiento que falta en cualquier copia local de esa comprobacion.
* `apps.identidad.sesiones.ServicioCicloVidaSesion` emite, resuelve y revoca la sesion opaca
  server-side de la tabla `sesion_usuario` (ARC-112, REQ-055, REQ-056, REQ-057).

Lo que SIGUE SIENDO de ARC-012 y NO se redeclara en identidad es el middleware de sesion
requerida (`apps.core_security.middleware.SesionRequeridaMiddleware`) y la resolucion del ROL
EFECTIVO de cada peticion (`apps.core_security.servicios.contexto.ResolutorContextoSesion`):
al middleware se le PROVEE el resolutor de sesion de identidad, no se duplica.

RESPUESTA UNIFORME (AC-SES-04). Todos los fallos de validacion siguen lanzando la MISMA
`SesionInvalidaError` con el mismo texto y el mismo `motivo` interno ("ausente",
"no_encontrada", "revocada", "caducada", "inactividad", "usuario_inactivo"), porque quien la
lanza es el resolutor de identidad.

ROL VIGENTE, NO CONGELADO (REQ-011, REQ-018). El `role_code` de `sesion_usuario` es el rol
congelado en la EMISION: se conserva como EVIDENCIA HISTORICA y como base de comparacion para
detectar un cambio de rol, pero NO ES LA FUENTE DE LA AUTORIZACION. El rol efectivo de cada
peticion lo relee de la tabla `usuario` `ResolutorContextoSesion`, al que `validar()` delega.

SECRETOS (REQ-063, REQ-076). Ni la contrasenia en claro, ni `password_hash`, ni el
identificador de sesion se escriben jamas en logs, trazas ni mensajes de error.
"""

from django.http import HttpRequest

from apps.core.contexto import ContextoSesion
from apps.core.models.transaccional import SesionUsuarioEntity, UsuarioEntity
from apps.identidad.autenticacion import ServicioAutenticacion
from apps.identidad.sesiones import ServicioCicloVidaSesion


ESTADO_USUARIO_ACTIVO = "ACTIVO"

# Valores del enumerado cerrado `ck_sesion_usuario_reason_enum`, en minusculas y literales.
MOTIVO_REVOCACION_CADUCIDAD = "expired"
MOTIVO_REVOCACION_LOGOUT = "logout"

# Valores por defecto de vigencia si el entorno no los define (el settings los parametriza).
INACTIVIDAD_MINUTOS_POR_DEFECTO = 30
VIGENCIA_ABSOLUTA_HORAS_POR_DEFECTO = 12


class ServicioSesiones:
    """
    Fachada de ARC-012 sobre los servicios de identidad (ARC-013).

    Conserva las cuatro operaciones de siempre -`validar` (cada peticion protegida),
    `autenticar` (inicio de sesion), `emitir` (alta de la sesion) y `revocar` (cierre o
    revocacion administrativa)- pero ninguna de ellas reimplementa nada: todas delegan en
    `apps.identidad`. Lo unico propio que queda aqui es la resolucion del rol efectivo de
    `validar`, que es responsabilidad de ARC-012.
    """

    def __init__(self) -> None:
        self._autenticacion = ServicioAutenticacion()
        self._ciclo_vida = ServicioCicloVidaSesion()

    @property
    def inactividad_minutos(self) -> int:
        """Minutos de inactividad tolerados antes de caducar la sesion, segun el parametro de identidad."""

        return self._ciclo_vida.inactividad_minutos

    @property
    def vigencia_absoluta_horas(self) -> int:
        """Horas de vida maxima de la sesion desde su emision, segun el parametro de identidad."""

        return self._ciclo_vida.vigencia_absoluta_horas

    # --- Validacion ------------------------------------------------------

    def validar(self, credencial: str | None, *, request: HttpRequest | None = None) -> ContextoSesion:
        """
        Resuelve la sesion en identidad y devuelve el contexto de la identidad efectiva.

        La validacion de la credencial (ausente, inexistente, revocada, caducada, inactiva o
        de usuario inactivo), el sellado de la caducidad y el refresco de `last_activity_at`
        los hace INTEGRAMENTE `ServicioCicloVidaSesion.resolver()`, que lanza SIEMPRE la misma
        `SesionInvalidaError` con el motivo real solo en la traza interna (AC-SES-04).

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

        sesion = self._ciclo_vida.resolver(credencial)

        resolutor = ResolutorContextoSesion()
        if request is not None:
            return resolutor.resolver_desde_peticion(request, sesion)
        return resolutor.resolver(sesion)

    # --- Inicio de sesion ------------------------------------------------

    def autenticar(self, username: str, password: str) -> UsuarioEntity:
        """
        Comprueba las credenciales de inicio de sesion delegando en `ServicioAutenticacion`.

        Acepta el `username` exacto o el correo corporativo (la unicidad del correo es
        insensible a mayusculas y espacios, REQ-045). Usuario inexistente, usuario inactivo y
        contrasenia incorrecta devuelven la MISMA `CredencialesInvalidasError` y, ademas,
        consumen el MISMO trabajo criptografico, de modo que la respuesta no revela si la
        cuenta existe ni por mensaje ni por tiempo (AC-AUT-02, AC-ACC-02). La cuenta bloqueada
        temporalmente sigue siendo caso aparte por requisito (`CuentaBloqueadaError`, REQ-053).
        """

        return self._autenticacion.autenticar(username, password)

    # --- Emision y revocacion --------------------------------------------

    def emitir(self, usuario: UsuarioEntity) -> SesionUsuarioEntity:
        """
        Crea la sesion del usuario delegando en `ServicioCicloVidaSesion.emitir()`.

        `session_id` lo genera el default uuid del modelo; `role_code` congela el rol VIGENTE
        en el instante de la emision, leido de la base, y `expires_at` queda siempre posterior
        a `issued_at` (vencimiento ABSOLUTO, REQ-055).

        Ese `role_code` congelado es EVIDENCIA HISTORICA y base de comparacion para detectar un
        cambio de rol; la autorizacion de cada peticion NO lo usa (REQ-018).
        """

        return self._ciclo_vida.emitir(usuario)

    def revocar(self, session_id: str, motivo: str = MOTIVO_REVOCACION_LOGOUT) -> None:
        """
        Revoca la sesion indicada delegando en `ServicioCicloVidaSesion.revocar()` (REQ-057).

        La fila NUNCA se borra (REQ-047, REQ-070). `motivo` debe ser uno de los valores en
        minusculas del enumerado del DDL: logout|expired|admin|password_change|account_deactivated.
        Si la sesion no existe o ya estaba revocada, la operacion no hace nada.
        """

        self._ciclo_vida.revocar(session_id, motivo)
