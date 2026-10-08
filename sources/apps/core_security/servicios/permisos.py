"""
Servicio de autorizacion: UNICO evaluador de la matriz `permiso_rol_operacion` (ARC-102).

La matriz es la FUENTE UNICA DE VERDAD de la autorizacion del sistema (REQ-018, AC-G-04): el
resultado de cada decision -autorizado, 403, alcance `OWN` o `ALL`- debe coincidir al 100 %
con la fila rol x operacion que vive en Oracle, sin ninguna desviacion.

DENY BY DEFAULT (ARC-102). La AUSENCIA de fila es la denegacion. No existe en este modulo
ninguna rama que conceda permiso sin haber leido una fila: si la consulta no devuelve nada,
se lanza `PermisoDenegadoError` y se acaba la decision.

CATALOGOS LEIDOS DE LA BASE. `cat_rol`, `cat_operacion` y `permiso_rol_operacion` los siembra
el changelog de Liquibase. Esta PROHIBIDO replicar sus valores en enums de Python: aqui no hay
ni una sola constante con un `role_code` ni un `operation_code`; todo sale de la consulta.

PROHIBIDO EL `if/elif` DE ROLES. La decision no se escribe como cadenas de condicionales por
rol repartidas por los servicios ni dentro del cuerpo de los endpoints: se declara una vez por
`operation_code` mediante la guardia de `apps.core_security.permisos`, que delega aqui.

SIN EFECTO LATERAL EN LAS DENEGACIONES (AC-G-04). El camino de denegacion no escribe en base,
no muta el contexto de sesion y no emite eventos: solo lanza. El contexto es inmutable y el
alcance se concede construyendo un objeto NUEVO, nunca reescribiendo el recibido.
"""

import dataclasses

from apps.core.contexto import AlcanceDatos, ContextoSesion
from apps.core.models.catalogos import PermisoRolOperacionEntity
from apps.core_security.errores import PermisoDenegadoError


# Valores admitidos de `data_scope`, tomados del enumerado del dominio (`AlcanceDatos`), que es
# el espejo del CHECK del DDL. No es un catalogo de negocio replicado: es la validacion
# fail-closed de que la fila leida trae un alcance interpretable.
ALCANCES_VALIDOS: frozenset[str] = frozenset(alcance.value for alcance in AlcanceDatos)


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


class ServicioPermisos:
    """
    Evaluador de la matriz rol x operacion contra Oracle.

    Tres operaciones y ninguna mas: `resolver` (decision autorizada o denegada), `alcance_de`
    (contexto de sesion enriquecido con el alcance de ESA operacion) y `tiene_permiso`
    (consulta booleana para componer capacidades, nunca para decidir).
    """

    def resolver(self, role_code: str, operation_code: str) -> PermisoResuelto:
        """
        Resuelve el permiso del par (rol, operacion) o deniega.

        DENY BY DEFAULT: la ausencia de fila en `permiso_rol_operacion` ES la denegacion. No
        hay ninguna rama que conceda permiso sin fila, ni valor por defecto que supla la
        consulta.

        FAIL-CLOSED ante datos incoherentes: si la fila existe pero su `data_scope` no esta en
        el enumerado del dominio (`OWN`/`ALL`), se deniega igual. Jamas se degrada un alcance
        desconocido a `ALL` ni se asume `OWN`: un dato que no se sabe interpretar no autoriza.

        El `operation_code` y el `role_code` viajan en la excepcion como TRAZA INTERNA; el
        cuerpo de la respuesta 403 lo compone el manejador global con el mensaje uniforme
        `mensajes.SIN_PERMISOS`, que no revela cual de los motivos se ha dado.
        """

        permiso = PermisoRolOperacionEntity.objects.filter(role_code_id=role_code, operation_code_id=operation_code).first()
        if permiso is None:
            raise PermisoDenegadoError(operation_code=operation_code, role_code=role_code)

        if permiso.data_scope not in ALCANCES_VALIDOS:
            raise PermisoDenegadoError(operation_code=operation_code, role_code=role_code)

        return PermisoResuelto(role_code=role_code, operation_code=operation_code, data_scope=permiso.data_scope)

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


def operaciones_permitidas(role_code: str) -> dict[str, str]:
    """
    Devuelve `{operation_code: data_scope}` con todo lo que la matriz autoriza al rol indicado.

    Se LEE de la base de datos (`permiso_rol_operacion`), nunca de un enum de Python: los
    codigos de operacion y de rol pertenecen a los catalogos que siembra Liquibase, y
    replicarlos en codigo garantizaria que un dia la API autoriza algo distinto de lo que dice
    la matriz.

    Es la lectura que necesita el recurso de permisos efectivos (EP-004 `EffectivePermissions`),
    propiedad de otra tarea. Aqui es solo una proyeccion de la matriz: no decide nada, y las
    filas con un `data_scope` fuera del enumerado se OMITEN (fail-closed), de modo que nunca se
    publica como capacidad algo que `resolver` denegaria.
    """

    filas = PermisoRolOperacionEntity.objects.filter(role_code_id=role_code).values_list("operation_code_id", "data_scope")
    return {operation_code: data_scope for operation_code, data_scope in filas if data_scope in ALCANCES_VALIDOS}
