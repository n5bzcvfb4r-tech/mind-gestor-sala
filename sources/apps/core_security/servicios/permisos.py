"""
Fachada de ARC-012 sobre la evaluacion de la matriz que implementa ARC-013 (`apps.usuarios.autorizacion`).

QUIEN HACE QUE
--------------
Este modulo YA NO lee `permiso_rol_operacion` ni interpreta el `data_scope`: lo PROVEE
`apps.usuarios.autorizacion` (ARC-013), que es el UNICO evaluador de la matriz, y aqui solo
queda la FACHADA DELGADA que consume ARC-012 (la guardia de `apps.core_security.permisos`, la
vista de permisos efectivos y el enriquecimiento del contexto de sesion con el alcance).

* `apps.usuarios.autorizacion.ServicioAutorizacion.autorizar` decide el par (rol, operacion)
  contra la fila que vive en Oracle y devuelve el alcance que concede.
* `apps.usuarios.autorizacion.ServicioAutorizacion.operaciones_permitidas` proyecta las
  capacidades del rol para componer la interfaz.

Lo que SIGUE SIENDO de ARC-012 y no se redeclara en el dominio es la FORMA en que esta capa
expone la decision: `PermisoResuelto` (el permiso aplicado que viaja a la guardia),
`PermisosEfectivos` (la proyeccion que serializa la vista) y el enriquecimiento inmutable del
`ContextoSesion` con el alcance de la operacion en curso.

LA MATRIZ ES LA FUENTE UNICA DE VERDAD de la autorizacion del sistema (REQ-018, AC-G-04): el
resultado de cada decision -autorizado, 403, alcance `OWN` o `ALL`- coincide al 100 % con la
fila rol x operacion de Oracle, porque quien la lee es el dominio y nadie mas.

DENY BY DEFAULT (ARC-102). La AUSENCIA de fila es la denegacion, y la aplica el dominio: en
este modulo no existe ninguna rama que conceda permiso sin que `autorizar` haya devuelto una
regla, ni valor por defecto que supla esa llamada.

CATALOGOS LEIDOS DE LA BASE. `cat_rol`, `cat_operacion` y `permiso_rol_operacion` los siembra
el changelog de Liquibase. Esta PROHIBIDO replicar sus valores en enums de Python: aqui no hay
ni una sola constante con un `role_code` ni un `operation_code`; todo sale de la decision del
dominio.

PROHIBIDO EL `if/elif` DE ROLES. La decision no se escribe como cadenas de condicionales por
rol repartidas por los servicios ni dentro del cuerpo de los endpoints: se declara una vez por
`operation_code` mediante la guardia de `apps.core_security.permisos`, que delega aqui, y esta
fachada delega a su vez en el unico evaluador.

SIN EFECTO LATERAL EN LAS DENEGACIONES (AC-G-04). El camino de denegacion no escribe en base,
no muta el contexto de sesion y no emite eventos: el dominio solo lanza y esta fachada solo
propaga. El contexto es inmutable y el alcance se concede construyendo un objeto NUEVO, nunca
reescribiendo el recibido.
"""

import dataclasses
import logging

from apps.core.contexto import AlcanceDatos, ContextoSesion
from apps.core_security.errores import PermisoDenegadoError
from apps.usuarios.autorizacion.servicio import ALCANCES_VALIDOS, ServicioAutorizacion


# Las decisiones de autorizacion no se registran aqui (las traza el manejador junto al
# `traceId`); el logger existe para avisar de MATRIZ HETEROGENEA al proyectar capacidades, que
# es defecto de datos y no de peticion, y que de otro modo pasaria inadvertido.
logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class PermisoResuelto:
    """
    Fila de `permiso_rol_operacion` que ha autorizado la operacion, con el alcance que concede.

    Se devuelve el permiso APLICADO y no un simple booleano a proposito: asi se puede trazar
    QUE REGLA CONCRETA ha decidido la autorizacion (rol, operacion y alcance), que es lo que
    permite auditar que la decision coincide con la matriz y no con una suposicion del codigo.

    Es inmutable: una decision de autorizacion ya tomada no se reescribe.
    """

    role_code: str
    operation_code: str
    data_scope: str


@dataclasses.dataclass(frozen=True, slots=True)
class PermisosEfectivos:
    """
    Proyeccion de solo lectura de lo que la matriz autoriza HOY al rol de la sesion en curso.

    No es una decision de autorizacion y no sustituye a `PermisoResuelto`: aquel acredita que
    una operacion CONCRETA fue autorizada por una fila concreta, mientras que este solo resume
    capacidades para que la interfaz sepa que ensenar. Se separan los dos tipos justamente para
    que ningun camino de decision pueda alimentarse por error de una lista de capacidades.

    Es inmutable porque es una FOTO del momento en que se leyo la matriz: si la matriz cambia,
    se vuelve a proyectar, nunca se parchea la foto anterior.
    """

    role_code: str
    allowed_operations: tuple[str, ...]
    data_scope: str


class ServicioPermisos:
    """
    Fachada de ARC-012 sobre el evaluador de la matriz rol x operacion (ARC-013).

    Dos familias de operaciones que NO se mezclan. Las VINCULANTES, unicas que deciden una
    peticion: `resolver` (decision autorizada o denegada) y `alcance_de` (contexto de sesion
    enriquecido con el alcance de ESA operacion). Las INFORMATIVAS, para componer interfaz y
    jamas para decidir: `tiene_permiso` (consulta booleana) y `efectivos` (proyeccion de las
    capacidades del rol). Ninguna informativa lanza, y ninguna autoriza nada por si misma.

    Ninguna de las cuatro reimplementa la regla: todas se apoyan en `ServicioAutorizacion`.
    """

    def __init__(self) -> None:
        # El servicio de dominio se resuelve UNA vez por fachada y no en cada llamada: es un
        # objeto sin estado de peticion, de modo que construirlo por decision solo anadiria
        # trabajo al camino caliente de la autorizacion.
        self._autorizacion = ServicioAutorizacion()

    def resolver(self, role_code: str, operation_code: str) -> PermisoResuelto:
        """
        Resuelve el permiso del par (rol, operacion) o deniega, delegando en el dominio.

        La regla la aplica INTEGRAMENTE `ServicioAutorizacion.autorizar()`; aqui solo se le da
        a su resultado la forma que esperan la guardia y las vistas de ARC-012.

        DENY BY DEFAULT: la ausencia de fila en `permiso_rol_operacion` ES la denegacion. No
        hay ninguna rama que conceda permiso sin fila, ni valor por defecto que supla la
        consulta del dominio.

        FAIL-CLOSED ante datos incoherentes: si la fila existe pero su `data_scope` no esta en
        el enumerado del dominio (`OWN`/`ALL`), se deniega igual. Jamas se degrada un alcance
        desconocido a `ALL` ni se asume `OWN`: un dato que no se sabe interpretar no autoriza.

        El `operation_code` y el `role_code` viajan en la excepcion como TRAZA INTERNA; el
        cuerpo de la respuesta 403 lo compone el manejador global con el mensaje uniforme
        `mensajes.SIN_PERMISOS`, que no revela cual de los motivos se ha dado.
        """

        permiso = self._autorizacion.autorizar(role_code, operation_code)
        return PermisoResuelto(
            role_code=permiso.role_code,
            operation_code=permiso.operation_code,
            data_scope=permiso.data_scope,
        )

    def alcance_de(self, contexto: ContextoSesion, operation_code: str) -> ContextoSesion:
        """
        Devuelve un contexto de sesion NUEVO con el alcance de datos de ESA operacion.

        `ContextoSesion` es inmutable (`frozen=True`), asi que el alcance no se "anade" al
        contexto recibido: se construye otro con `dataclasses.replace(...)`. El contexto
        original queda intacto, de modo que una denegacion posterior no puede haber ensanchado
        el alcance de nadie.

        El alcance viaja DENTRO del contexto hasta el repositorio, que lo aplica como PREDICADO
        SQL de la consulta; NUNCA como filtro sobre un resultado ya materializado. Filtrar
        despues de traer las filas significaria que la base ya ha devuelto datos ajenos al
        alcance, y ademas rompe la paginacion y los recuentos.

        Si el par (rol, operacion) no tiene fila, propaga `PermisoDenegadoError` de `resolver`:
        no devuelve un contexto degradado.
        """

        permiso = self.resolver(contexto.role_code, operation_code)
        return dataclasses.replace(contexto, data_scope=permiso.data_scope)

    def tiene_permiso(self, role_code: str, operation_code: str) -> bool:
        """
        Indica si el par (rol, operacion) tiene fila en la matriz, sin lanzar.

        Es para COMPONER menus, botones y capacidades efectivas de la interfaz. NO sustituye a
        `resolver` en el camino de decision: la autorizacion real de cada peticion la decide
        siempre `resolver`/`alcance_de` lanzando `PermisoDenegadoError`, porque una consulta
        booleana invita a escribir el `if` de permiso en el cuerpo del endpoint, que es
        justamente el anti-patron que este modulo existe para evitar.
        """

        try:
            self.resolver(role_code, operation_code)
        except PermisoDenegadoError:
            return False
        return True

    def efectivos(self, role_code: str) -> PermisosEfectivos:
        """
        Proyecta las capacidades que la matriz concede al rol indicado, sin decidir nada.

        PARA QUE SIRVE Y PARA QUE NO. Alimenta el recurso de solo lectura de permisos efectivos
        (EP-004, REQ-020) para que la SPA oculte o deshabilite los controles que el backend va a
        denegar. Es USABILIDAD, no seguridad: NO sustituye a `resolver`/`alcance_de`, que siguen
        siendo la UNICA decision vinculante de cada peticion. Que el cliente reciba una
        operacion en `allowed_operations` no la autoriza, y que no la reciba no es lo que la
        deniega; si la matriz cambia entre esta lectura y la peticion siguiente, manda la matriz.

        SOLO EL ROL QUE SE PIDE (AC-ROL-04). Se proyecta el rol de la sesion en curso y ninguno
        mas: este servicio no tiene ninguna forma de devolver los permisos de otros usuarios ni
        la matriz completa del sistema, porque la propia matriz es informacion sensible -revela
        que operaciones existen y quien las puede ejecutar- y publicarla entera daria al cliente
        un mapa del sistema que no necesita para pintar su interfaz.

        SESGO A LA DENEGACION (fail-closed). El `data_scope` del rol es el MENOR privilegio
        compatible con lo leido: sin entradas en la matriz, `OWN`; con entradas heterogeneas
        (unas `OWN` y otras `ALL`), tambien `OWN`. Publicar `ALL` "por si acaso" haria que la
        interfaz ofreciera acciones y volumenes de datos que el backend va a denegar despues, que
        es peor experiencia que no ofrecerlos y, sobre todo, filtra expectativas de privilegio.
        El caso heterogeneo se registra como `warning` porque es un sintoma de matriz mal
        sembrada: el alcance por rol deja de ser representable en un unico valor.

        NO LANZA NUNCA: es una LECTURA de capacidades, no una decision de autorizacion. Un rol
        sin ningun permiso devuelve la proyeccion vacia, no un 403.
        """

        matriz = operaciones_permitidas(role_code)
        alcances = set(matriz.values())

        if len(alcances) == 1:
            # Se normaliza por el enumerado, nunca por la cadena cruda de la fila: el valor
            # publicado procede siempre de `AlcanceDatos` (`OWN`/`ALL`).
            data_scope = AlcanceDatos(alcances.pop()).value
        else:
            data_scope = AlcanceDatos.OWN.value
            if alcances:
                logger.warning(
                    "Matriz de permisos heterogenea para el rol %s: alcances %s; se proyecta el mas restrictivo (%s)",
                    role_code,
                    sorted(alcances),
                    data_scope,
                )

        return PermisosEfectivos(role_code=role_code, allowed_operations=tuple(sorted(matriz)), data_scope=data_scope)


def operaciones_permitidas(role_code: str) -> dict[str, str]:
    """
    Devuelve `{operation_code: data_scope}` con todo lo que la matriz autoriza al rol indicado.

    DELEGA en `ServicioAutorizacion.operaciones_permitidas()`: la proyeccion se LEE de la base
    de datos (`permiso_rol_operacion`), nunca de un enum de Python, porque los codigos de
    operacion y de rol pertenecen a los catalogos que siembra Liquibase y replicarlos en codigo
    garantizaria que un dia la API autoriza algo distinto de lo que dice la matriz.

    Es la lectura que necesita el recurso de permisos efectivos (EP-004 `EffectivePermissions`).
    Aqui es solo una proyeccion de la matriz: no decide nada, y las filas con un `data_scope`
    fuera del enumerado las OMITE el dominio (fail-closed), de modo que nunca se publica como
    capacidad algo que `resolver` denegaria.
    """

    return ServicioAutorizacion().operaciones_permitidas(role_code)


__all__ = [
    # `ALCANCES_VALIDOS` se REEXPORTA del dominio (no se recalcula aqui) porque otros modulos y
    # pruebas de ARC-012 lo leen desde esta fachada: el enumerado valido es uno solo.
    "ALCANCES_VALIDOS",
    "PermisoResuelto",
    "PermisosEfectivos",
    "ServicioPermisos",
    "operaciones_permitidas",
]
