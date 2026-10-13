"""
Fachada de ARC-012 sobre el contexto de autorizacion: el rol SIEMPRE se relee de la base.

QUIEN HACE QUE
--------------
La LECTURA FAIL-CLOSED del rol efectivo YA NO se implementa aqui: la PROVEE
`apps.usuarios.autorizacion` (ARC-013), que es el proveedor unico de esa logica y el unico
evaluador de la matriz. `ServicioAutorizacion.rol_efectivo()` relee la fila de `usuario`,
comprueba estado y pertenencia a `cat_rol` y lanza los mismos errores de siempre.

Lo que SIGUE SIENDO de ARC-012 y NO se redeclara en el dominio es lo que queda en este modulo:
el DESCARTE ESTRUCTURAL de la identidad enviada por el cliente y el REFRESCO DE CAPACIDADES de
la fila de `sesion_usuario`.

REQ-018. En cada peticion autenticada el backend obtiene el `role_code` de la tabla `usuario`
a partir del `session_user_id` de la sesion. Cualquier rol, alcance o identificador de usuario
que el cliente envie en el cuerpo, en la query string o en una cabecera se descarta en
SILENCIO: no produce error ni 400, simplemente no participa en ninguna decision. El descarte
es ESTRUCTURAL, no una limpieza defensiva: `ResolutorContextoSesion.resolver()` solo recibe la
fila de `sesion_usuario`, de modo que le resulta imposible leer el payload aunque alguien
quisiera hacerlo, y lo unico que le pasa al dominio es el `user_id` de esa fila.

FAIL-CLOSED (REQ-018, AC-ROL-02). Si el usuario no tiene rol, si su rol no pertenece a
`cat_rol` o si esta inactivo, se deniega con 403 y el texto uniforme `mensajes.SIN_PERMISOS`;
nunca se degrada a un permiso mas amplio ni se asume un rol por defecto. Quien lo decide -y
quien lanza- es el dominio: aqui solo se propaga.

SIN RELOGIN (REQ-011, AC-PERM-05). Releer el rol en cada peticion es justo lo que hace que un
cambio de rol del ADMINISTRADOR surta efecto en la SIGUIENTE peticion del usuario sin obligarle
a iniciar sesion de nuevo y sin invalidar su sesion. Cuando el rol vigente difiere del que
quedo congelado en `sesion_usuario`, se recarga la fila y se sella `permissions_refreshed_at`.

ATRIBUCION (REQ-064, AC-TRZ-01). La identidad que devuelve este modulo es la UNICA valida para
atribuir autorias (reportante, autor de un cambio de estado): sale del usuario de la sesion,
jamas de un `user_id` enviado por el cliente.

SECRETOS (REQ-063, REQ-076). El `session_id` es una credencial: no se escribe en ningun log ni
traza de este modulo. Para identificar la sesion en las trazas se usa el `session_user_id`.
"""
import logging

from django.http import HttpRequest

from apps.core.contexto import ContextoSesion, utc_now
from apps.core.models.transaccional import SesionUsuarioEntity
from apps.usuarios.autorizacion.servicio import ServicioAutorizacion


logger = logging.getLogger(__name__)


# Nombres con los que un cliente podria intentar colar su propio rol, alcance o identidad en el
# cuerpo o en la query string. REQ-018 los descarta en SILENCIO: no producen error ni 400,
# simplemente no participan en ninguna decision.
CAMPOS_DE_IDENTIDAD_DEL_CLIENTE: frozenset[str] = frozenset(
    {
        "role",
        "rol",
        "role_code",
        "roleCode",
        "data_scope",
        "dataScope",
        "scope",
        "alcance",
        "user_id",
        "userId",
        "usuario_id",
        "usuarioId",
        "session_user_id",
        "sessionUserId",
    }
)

# Las mismas pretensiones viajando como cabecera HTTP (clave ya normalizada por Django en
# `request.META`). Reciben identico trato: se ignoran sin avisar.
CABECERAS_DE_IDENTIDAD_DEL_CLIENTE: frozenset[str] = frozenset(
    {
        "HTTP_X_ROLE",
        "HTTP_X_ROLE_CODE",
        "HTTP_X_USER_ID",
        "HTTP_X_DATA_SCOPE",
        "HTTP_X_SCOPE",
    }
)


def identidad_suministrada_por_el_cliente(request: HttpRequest) -> tuple[str, ...]:
    """
    Devuelve, ordenados alfabeticamente, los nombres de identidad que el cliente ha enviado.

    Es una funcion de SOLO DIAGNOSTICO. Su valor NO se usa jamas para decidir nada: existe para
    dejar UNA linea de traza a nivel DEBUG y para que las pruebas puedan acreditar que el dato
    llego y se ignoro. Inspecciona la query string (`request.GET`), el cuerpo (`request.POST` y,
    si existe y es un diccionario, el `request.data` de DRF) y las cabeceras (`request.META`).

    Es tolerante a fallos a proposito: si leer el cuerpo lanza cualquier excepcion (contenido
    mal formado, flujo ya consumido, parser que rechaza el medio), devuelve lo que haya podido
    inspeccionar y nunca propaga. Un diagnostico jamas puede tumbar una peticion.
    """

    encontrados: set[str] = set()

    try:
        encontrados.update(nombre for nombre in request.GET if nombre in CAMPOS_DE_IDENTIDAD_DEL_CLIENTE)
    except Exception:  # noqa: BLE001 - diagnostico: nunca puede romper la peticion
        pass

    try:
        encontrados.update(nombre for nombre in request.POST if nombre in CAMPOS_DE_IDENTIDAD_DEL_CLIENTE)
    except Exception:  # noqa: BLE001 - idem
        pass

    try:
        cuerpo = getattr(request, "data", None)
        if isinstance(cuerpo, dict):
            encontrados.update(nombre for nombre in cuerpo if nombre in CAMPOS_DE_IDENTIDAD_DEL_CLIENTE)
    except Exception:  # noqa: BLE001 - idem
        pass

    try:
        encontrados.update(nombre for nombre in request.META if nombre in CABECERAS_DE_IDENTIDAD_DEL_CLIENTE)
    except Exception:  # noqa: BLE001 - idem
        pass

    return tuple(sorted(encontrados))


class ResolutorContextoSesion:
    """
    Fuente UNICA del contexto de autorizacion de la peticion (REQ-018).

    El rol efectivo NO se toma del `role_code` que quedo congelado en `sesion_usuario` al emitir
    la sesion, ni de ningun dato enviado por el cliente: se RELEE de la tabla `usuario` en cada
    peticion, a partir del `session_user_id`.

    Releer es deliberado y tiene dos consecuencias que el brief exige:

    * Un cambio de rol hecho por el ADMINISTRADOR surte efecto en la SIGUIENTE peticion del
      usuario, sin obligarle a volver a iniciar sesion y sin invalidar la sesion (REQ-011,
      AC-PERM-05).
    * Tras una DEGRADACION de rol no queda ninguna ventana de privilegio residual: el rol
      antiguo deja de usarse en el acto, porque ya nadie lo lee.

    La resolucion es FAIL-CLOSED: usuario inexistente, usuario inactivo, usuario sin rol o rol
    fuera del catalogo cortan la peticion; en ningun caso se sustituye por un rol mas amplio ni
    por un valor por defecto. Esa lectura la PROVEE `apps.usuarios.autorizacion` (ARC-013); lo
    propio de este resolutor es recibir SOLO la fila de sesion y refrescar sus capacidades.
    """

    def __init__(self) -> None:
        self._autorizacion = ServicioAutorizacion()

    def resolver(self, sesion: SesionUsuarioEntity) -> ContextoSesion:
        """
        Resuelve la identidad efectiva de la peticion releyendo al usuario de la base de datos.

        Recibe UNICAMENTE la fila de sesion: no tiene acceso a la peticion, de modo que no puede
        leer rol, alcance ni `user_id` del cliente ni por error ni por descuido (REQ-018). Lo
        unico que viaja al dominio es el `session_user_id` de esa fila.

        La relectura del rol y sus cuatro comprobaciones fail-closed -existencia, estado, rol
        asignado y pertenencia a `cat_rol`, EN ESE ORDEN- las hace integramente
        `ServicioAutorizacion.rol_efectivo()`, que es el proveedor unico de esa regla.

        Propaga `SesionInvalidaError` (401, motivo `usuario_no_encontrado`) si la sesion apunta
        a un usuario que ya no existe, y `RolNoResolubleError` (403, `mensajes.SIN_PERMISOS`,
        motivos `usuario_inactivo`, `sin_rol` o `rol_desconocido`) si el rol no se puede
        resolver. El orden de comprobacion es parte del contrato y lo fija el dominio: invertirlo
        cambiaria el codigo de estado de casos ya acreditados por las pruebas.
        """

        identidad = self._autorizacion.rol_efectivo(sesion.user_id)

        # `role_code` ya no puede ser `None` aqui: el dominio ha denegado antes si lo era.
        role_code: str = identidad.role_code or ""

        self.refrescar_capacidades(sesion, role_code)

        # `data_scope` se deja en su valor por defecto del dataclass: el alcance efectivo es POR
        # OPERACION y lo resuelve la matriz `permiso_rol_operacion`, no este resolutor.
        return ContextoSesion(
            user_id=identidad.user_id,
            role_code=role_code,
            session_id=sesion.session_id,
            display_name=identidad.full_name,
        )

    def refrescar_capacidades(self, sesion: SesionUsuarioEntity, role_code_vigente: str) -> bool:
        """
        Sincroniza la sesion con el rol vigente y sella `permissions_refreshed_at` (REQ-011).

        Devuelve `True` solo si ha habido recarga. En el caso normal (el rol no ha cambiado) no
        escribe NADA y devuelve `False`: una escritura por peticion seria inaceptable.

        La sesion NO se revoca. REQ-011, regla 3: un cambio de rol no exige un nuevo inicio de
        sesion, de modo que la sesion sigue viva y es la siguiente peticion la que ya se resuelve
        con el rol nuevo (AC-PERM-05).
        """

        if sesion.role_code_id == role_code_vigente:
            return False

        role_code_anterior = sesion.role_code_id
        sesion.role_code_id = role_code_vigente
        sesion.permissions_refreshed_at = utc_now()
        sesion.save(update_fields=["role_code", "permissions_refreshed_at"])

        # El `session_id` es credencial y NUNCA se registra (REQ-063, REQ-076): se traza el
        # usuario de la sesion.
        logger.info(
            "Capacidades de la sesion recargadas tras un cambio de rol.",
            extra={
                "data": {
                    "session_user_id": sesion.user_id,
                    "role_code_anterior": role_code_anterior,
                    "role_code_vigente": role_code_vigente,
                }
            },
        )
        return True

    def resolver_desde_peticion(self, request: HttpRequest, sesion: SesionUsuarioEntity) -> ContextoSesion:
        """
        Resuelve el contexto de una peticion HTTP dejando constancia de lo que se ha ignorado.

        El descarte de la identidad enviada por el cliente es ESTRUCTURAL, no una limpieza: lo
        unico que se le pasa a `resolver()` es la fila de sesion, de modo que le resulta
        IMPOSIBLE leer el payload aunque alguien lo quisiera. La inspeccion previa es puro
        diagnostico y deja, como mucho, UNA linea a nivel DEBUG.
        """

        campos_ignorados = identidad_suministrada_por_el_cliente(request)
        if campos_ignorados:
            logger.debug(
                "Datos de identidad recibidos del cliente: descartados.",
                extra={"data": {"campos": list(campos_ignorados), "path": request.path}},
            )

        return self.resolver(sesion)
