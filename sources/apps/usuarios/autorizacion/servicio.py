"""
Dominio de la AUTORIZACION: rol efectivo por peticion y evaluacion de la matriz (ARC-013, ARC-102).

UN SOLO SITIO DONDE SE DECIDE
------------------------------
Este modulo es el UNICO evaluador de `permiso_rol_operacion` y el unico que resuelve el rol
efectivo. No hay -ni puede haber- cadenas de `if/elif` por rol repartidas por los servicios ni
dentro del cuerpo de los endpoints: la regla se declara una vez por `operation_code` y se evalua
aqui, contra la fila que vive en Oracle (REQ-018, AC-G-04).

DENY BY DEFAULT (REQ-021, REQ-060)
-----------------------------------
La AUSENCIA de fila ES la denegacion. En `autorizar` no existe ninguna rama que conceda permiso sin
haber leido una fila, ni valor por defecto que supla la consulta.

FAIL-CLOSED (REQ-018, AC-ROL-02)
---------------------------------
Lo mismo rige en los datos a medio hacer: usuario inexistente, usuario inactivo, usuario sin rol,
rol fuera de `cat_rol` o `data_scope` que el dominio no sabe interpretar cortan la peticion. JAMAS
se degrada a un rol o a un permiso mas amplio, nunca se asume un rol por defecto y nunca se
convierte un alcance desconocido en `ALL` -ni en `OWN`-: un dato que no se sabe interpretar no
autoriza.

EL ROL SE RELEE EN CADA PETICION (REQ-011, REQ-018, AC-PERM-05)
-----------------------------------------------------------------
`rol_efectivo` lee la tabla `usuario`, no el `role_code` que quedo congelado en la sesion ni nada
enviado por el cliente. Releer es lo que hace que un cambio de rol del ADMINISTRADOR surta efecto en
la SIGUIENTE peticion del usuario, sin obligarle a iniciar sesion de nuevo, y lo que garantiza que
tras una degradacion no quede ninguna ventana de privilegio residual.

SIN EFECTO LATERAL EN LA DENEGACION (AC-G-04)
-----------------------------------------------
El camino de denegacion no escribe en base, no muta el contexto de sesion y no emite eventos: solo
lanza. Tampoco escribe nada el camino autorizado: este dominio es de LECTURA.

RESPUESTA UNIFORME. Los motivos ("usuario_no_encontrado", "usuario_inactivo", "sin_rol",
"rol_desconocido") y el par (rol, operacion) viajan en la excepcion como TRAZA INTERNA; el cuerpo de
la respuesta lo compone el manejador global con el mensaje uniforme, que no revela cual de los
motivos se ha dado.
"""

from __future__ import annotations

import dataclasses
import logging

from apps.core.contexto import AlcanceDatos
from apps.core_security.errores import PermisoDenegadoError, RolNoResolubleError, SesionInvalidaError
from apps.usuarios.autorizacion.repositorio import IdentidadAutorizable, RepositorioAutorizacion


# Las decisiones de autorizacion NORMALES no se registran aqui: las traza el manejador global junto
# al `traceId` de la peticion. Este logger existe para avisar de INCOHERENCIAS DE LA MATRIZ, que son
# defecto de datos y no de peticion, y que de otro modo pasarian inadvertidas.
logger = logging.getLogger(__name__)


#: Valores admitidos de `data_scope`, tomados del enumerado del dominio (`AlcanceDatos`), que es el
#: espejo del CHECK del DDL. No es un catalogo de negocio replicado -esos se leen de la base-: es la
#: validacion fail-closed de que la fila leida trae un alcance interpretable.
ALCANCES_VALIDOS: frozenset[str] = frozenset(alcance.value for alcance in AlcanceDatos)


@dataclasses.dataclass(frozen=True, slots=True)
class PermisoAutorizado:
    """
    Fila de `permiso_rol_operacion` que ha autorizado la operacion, con el alcance que concede.

    Se devuelve la REGLA APLICADA y no un simple booleano a proposito: asi se puede trazar QUE FILA
    CONCRETA ha decidido la autorizacion (rol, operacion y alcance), que es lo que permite auditar
    que la decision coincide con la matriz y no con una suposicion del codigo.

    Es inmutable: una decision de autorizacion ya tomada no se reescribe.
    """

    role_code: str
    operation_code: str
    data_scope: str


class ServicioAutorizacion:
    """
    Resuelve el rol efectivo de la peticion y evalua el par (rol, operacion) contra la matriz.

    Dos operaciones VINCULANTES, que son las que deciden una peticion: `rol_efectivo` (quien es,
    releido de la base) y `autorizar` (si ese rol puede ejecutar esa operacion y con que alcance).
    Y dos INFORMATIVAS, que no deciden nada y no lanzan: `operaciones_permitidas` (proyeccion de
    capacidades para componer la interfaz) y `matriz_coherente` (diagnostico de la siembra).

    El repositorio entra por el constructor para que las pruebas puedan sustituirlo; en produccion
    se resuelve solo.
    """

    def __init__(self, repositorio: RepositorioAutorizacion | None = None) -> None:
        self._repo = repositorio or RepositorioAutorizacion()

    def rol_efectivo(self, user_id: int) -> IdentidadAutorizable:
        """
        Devuelve la identidad con el rol VIGENTE del usuario, releyendolo de la base (REQ-018).

        No recibe -ni puede recibir- rol, alcance ni identidad del cliente: lo unico que entra es el
        `user_id` de la sesion ya autenticada. El descarte de lo que el cliente pudiera enviar es
        ESTRUCTURAL, no una limpieza defensiva.

        EL ORDEN DE LAS COMPROBACIONES ES PARTE DEL CONTRATO. Primero la existencia (si el usuario
        ya no esta, la credencial no autentica a nadie y el fallo es de SESION, 401), despues el
        estado y por ultimo el rol (ambos de PERMISOS, 403). Invertirlo cambiaria el codigo de
        estado de casos ya acreditados por las pruebas del repositorio.

        FAIL-CLOSED: ninguna de las cuatro ramas devuelve una identidad degradada ni asume un rol por
        defecto, y ninguna escribe nada.

        Args:
            user_id: identificador del usuario de la sesion en curso.

        Returns:
            La identidad con su rol vigente, ya comprobado contra `cat_rol`.

        Raises:
            SesionInvalidaError: 401, motivo `usuario_no_encontrado`; la sesion apunta a un usuario
                que ya no existe, asi que deja de autenticar a nadie.
            RolNoResolubleError: 403, motivos `usuario_inactivo`, `sin_rol` o `rol_desconocido`; los
                tres comparten mensaje para no servir de oraculo sobre el estado del usuario.
        """

        identidad = self._repo.identidad_de(user_id)
        if identidad is None:
            # No es un problema de permisos sino de sesion: la credencial apunta a un usuario que ya
            # no esta.
            raise SesionInvalidaError(motivo="usuario_no_encontrado")

        if not identidad.is_active:
            # REQ-018, campo `is_active`: "false -> 403 sin efecto". La peticion se deniega y no
            # produce ningun cambio.
            raise RolNoResolubleError(motivo="usuario_inactivo")

        if not identidad.role_code:
            raise RolNoResolubleError(motivo="sin_rol")

        # Se comprueba la pertenencia al catalogo aunque Oracle declare la clave ajena: es la
        # validacion explicita FAIL-CLOSED de REQ-018 y cubre una fila escrita por fuera del ORM.
        if not self._repo.rol_pertenece_al_catalogo(identidad.role_code):
            raise RolNoResolubleError(motivo="rol_desconocido")

        return identidad

    def autorizar(self, role_code: str, operation_code: str) -> PermisoAutorizado:
        """
        Autoriza el par (rol, operacion) contra la matriz, o deniega.

        DENY BY DEFAULT: la ausencia de fila en `permiso_rol_operacion` ES la denegacion. No hay
        ninguna rama que conceda permiso sin fila.

        FAIL-CLOSED ANTE DATO INCOHERENTE: si la fila existe pero su `data_scope` no esta en el
        enumerado del dominio (`OWN`/`ALL`), se deniega IGUAL. Jamas se degrada un alcance
        desconocido a `ALL` ni se asume `OWN`.

        SIN EFECTO LATERAL: la denegacion no escribe en base, no muta el contexto de sesion y no
        emite eventos; solo lanza.

        Args:
            role_code: codigo del rol vigente, el devuelto por `rol_efectivo`.
            operation_code: codigo de la operacion que se intenta ejecutar.

        Returns:
            El permiso aplicado, con el alcance que concede la fila que ha decidido.

        Raises:
            PermisoDenegadoError: 403; `operation_code` y `role_code` viajan dentro como traza
                interna y nunca forman parte del cuerpo de la respuesta.
        """

        permiso = self._repo.permiso_de(role_code, operation_code)
        if permiso is None:
            raise PermisoDenegadoError(operation_code=operation_code, role_code=role_code)

        if permiso.data_scope not in ALCANCES_VALIDOS:
            raise PermisoDenegadoError(operation_code=operation_code, role_code=role_code)

        return PermisoAutorizado(role_code=role_code, operation_code=operation_code, data_scope=permiso.data_scope)

    def operaciones_permitidas(self, role_code: str) -> dict[str, str]:
        """
        Proyecta `{operation_code: data_scope}` con lo que la matriz autoriza HOY al rol indicado.

        PARA QUE SIRVE Y PARA QUE NO. Alimenta la lectura de permisos efectivos para que la interfaz
        oculte o deshabilite los controles que el backend va a denegar. Es USABILIDAD, no seguridad:
        NO sustituye a `autorizar`, que sigue siendo la UNICA decision vinculante de cada peticion.
        Que una operacion aparezca aqui no la autoriza, y que no aparezca no es lo que la deniega.

        FAIL-CLOSED: las filas con un `data_scope` fuera del enumerado se OMITEN, de modo que nunca
        se publica como capacidad algo que `autorizar` denegaria.

        NO LANZA NUNCA: un rol sin ninguna fila devuelve el diccionario vacio, no un 403.

        Args:
            role_code: codigo del rol cuya capacidad se proyecta.

        Returns:
            Diccionario de operacion a alcance, ya filtrado por alcance interpretable.
        """

        matriz = self._repo.permisos_de_rol(role_code)
        return {operation_code: data_scope for operation_code, data_scope in matriz.items() if data_scope in ALCANCES_VALIDOS}

    def matriz_coherente(self) -> tuple[tuple[str, str, int], ...]:
        """
        Devuelve los pares (rol, operacion) con entradas CONTRADICTORIAS en la matriz (AC-PERM-01).

        Es DIAGNOSTICO: no decide, no lanza y no corrige. Una matriz coherente -el caso normal, que
        Oracle sostiene con la clave primaria compuesta- devuelve la tupla vacia.

        Cuando hay duplicados se deja UNA sola linea de `error`: la decision de autorizacion de ese
        par dependeria de que fila se leyese primero, lo que es un defecto de la siembra que debe
        verse en los logs aunque nadie llame nunca a este metodo esperando un fallo.

        Returns:
            Tupla de `(role_code, operation_code, numero_de_filas)`; vacia si la matriz es coherente.
        """

        pares = self._repo.pares_con_entradas_contradictorias()
        if pares:
            logger.error(
                "Matriz de permisos incoherente: hay pares (rol, operacion) con mas de una fila.",
                extra={"data": {"pares": [list(par) for par in pares]}},
            )
        return pares


__all__ = [
    "ALCANCES_VALIDOS",
    "PermisoAutorizado",
    "ServicioAutorizacion",
]
