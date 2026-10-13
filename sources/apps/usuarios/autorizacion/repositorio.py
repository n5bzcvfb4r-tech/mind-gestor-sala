"""
Acceso a datos de la AUTORIZACION: rol vigente del usuario y matriz `permiso_rol_operacion` (ARC-013).

LA MATRIZ ES LA FUENTE UNICA DE VERDAD
---------------------------------------
Toda decision de autorizacion del sistema -autorizado, denegado, alcance `OWN` o `ALL`- sale de la
fila rol x operacion que vive en Oracle (ARC-102, REQ-018, AC-G-04). Este modulo es la UNICA puerta
por la que esa fila entra al dominio: quien quiera saber si un rol puede ejecutar una operacion
pregunta aqui, y aqui se pregunta a la base.

LOS CATALOGOS NO SE REPLICAN EN PYTHON
---------------------------------------
`cat_rol`, `cat_operacion` y `permiso_rol_operacion` los siembra el changelog de Liquibase (ARC-016)
y son su unica fuente de verdad. Esta PROHIBIDO replicar sus valores en enums de Python: en este
fichero no hay ni un solo `role_code` ni un solo `operation_code` literal, todos llegan por
parametro y todos se contrastan contra la base. Una segunda copia en codigo se desalinearia en
cuanto se sembrase un rol o una operacion nuevos, y la API acabaria autorizando algo distinto de lo
que dice la matriz.

AQUI NO SE DECIDE, NO SE ESCRIBE Y NO SE ABRE TRANSACCION
-----------------------------------------------------------
Este repositorio es de SOLO LECTURA. No lanza `PermisoDenegadoError` ni `RolNoResolubleError`, no
aplica el deny-by-default y no interpreta el `data_scope`: devuelve lo que hay (o `None`) y es
`ServicioAutorizacion` quien decide. Separarlo asi es lo que permite que la decision tenga un unico
sitio y que las lecturas se puedan auditar por separado de la regla.

EL ALCANCE SE APLICA COMO PREDICADO SQL
----------------------------------------
El `data_scope` que se lee aqui viaja despues dentro del contexto de sesion hasta el repositorio del
caso de uso, que lo traduce a un PREDICADO de la consulta; nunca a un filtro sobre un resultado ya
materializado. Filtrar despues de traer las filas significaria que Oracle ya ha devuelto datos
ajenos al alcance, y ademas rompe la paginacion y los recuentos.

La agregacion de `pares_con_entradas_contradictorias` tambien viaja a Oracle (`GROUP BY` +
`HAVING`): agrupar en memoria obligaria a materializar la matriz entera.
"""

from __future__ import annotations

import dataclasses

from django.db.models import Count

from apps.core.models.catalogos import PermisoRolOperacionEntity, RolEntity
from apps.core.models.transaccional import UsuarioEntity


#: Estado de cuenta operativa del CHECK de `usuario.status`. Es el literal de un enumerado del DDL
#: -no un catalogo con tabla propia-, asi que no hay nada que leer de la base: se declara una sola
#: vez aqui para que no se escriba a mano en cada comparacion.
ESTADO_USUARIO_ACTIVO = "ACTIVO"


@dataclasses.dataclass(frozen=True, slots=True)
class IdentidadAutorizable:
    """
    Lo UNICO del usuario que interviene en una decision de autorizacion.

    Es una proyeccion deliberadamente pobre de la fila de `usuario`: rol vigente, estado y nombre
    para rotular. No arrastra credencial (`password_hash`, `password_salt`) ni correo ni fechas,
    porque lo que no se proyecta no se puede filtrar mal, ni loggear por descuido, ni serializar por
    error (REQ-063, REQ-076).

    `role_code` puede ser `None`: un usuario sin rol es un caso REAL de la base y la decision de que
    hacer con el -denegar, fail-closed- es del servicio, no de esta estructura.

    Es inmutable: la identidad con la que se esta resolviendo una peticion no se reescribe a mitad.
    """

    user_id: int
    role_code: str | None
    is_active: bool
    full_name: str


@dataclasses.dataclass(frozen=True, slots=True)
class FilaPermiso:
    """
    Fila cruda de `permiso_rol_operacion` tal y como esta en la base, SIN interpretar.

    `data_scope` se transporta como la cadena leida y NO como `AlcanceDatos`: si la fila trajera un
    alcance que el dominio no sabe interpretar, convertirla aqui reventaria en el repositorio y
    ocultaria el dato incoherente. Quien valida el alcance contra el enumerado -y deniega si no
    encaja- es `ServicioAutorizacion`, que es la capa que puede decidir.
    """

    role_code: str
    operation_code: str
    data_scope: str


class RepositorioAutorizacion:
    """
    Lecturas de la autorizacion contra Oracle: identidad del usuario y matriz rol x operacion.

    Cinco lecturas y ninguna escritura. Ninguna de ellas lanza excepciones de dominio ni aplica el
    deny-by-default: la ausencia se devuelve como `None` o como coleccion vacia, y es el servicio
    quien la convierte en denegacion.
    """

    def identidad_de(self, user_id: int) -> IdentidadAutorizable | None:
        """
        Lee de `usuario` el rol vigente y el estado de la persona indicada.

        Se RELEE siempre de la base (REQ-018): el rol efectivo no se toma del que quedo congelado en
        `sesion_usuario` al emitir la sesion ni de ningun dato enviado por el cliente. Eso es lo que
        hace que un cambio de rol surta efecto en la SIGUIENTE peticion del usuario y que, tras una
        degradacion, no quede ninguna ventana de privilegio residual.

        `values(...)` proyecta CUATRO columnas y no instancia la entidad: no toca la credencial ni
        el correo. Para la clave ajena `role_code`, `values("role_code")` devuelve el CODIGO (el
        valor de la columna, equivalente a `role_code_id`), que es justo lo que necesita la decision;
        pedir la entidad del rol anadiria un JOIN para un dato que no se usa.

        Args:
            user_id: identificador del usuario de la sesion en curso.

        Returns:
            La identidad proyectada, o `None` si el identificador no corresponde a ninguna fila.
        """

        fila = UsuarioEntity.objects.filter(pk=user_id).values("user_id", "role_code", "status", "full_name").first()
        if fila is None:
            return None

        return IdentidadAutorizable(
            user_id=fila["user_id"],
            role_code=fila["role_code"],
            is_active=fila["status"] == ESTADO_USUARIO_ACTIVO,
            full_name=fila["full_name"],
        )

    def rol_pertenece_al_catalogo(self, role_code: str) -> bool:
        """
        Indica si el codigo de rol existe en `cat_rol`.

        La comprobacion es explicita aunque Oracle declare la clave ajena: es la validacion
        FAIL-CLOSED que exige REQ-018 -un `role_code` fuera de `cat_rol` no concede ningun permiso- y
        cubre el caso de una fila escrita por fuera del ORM.

        NO se filtra por `is_active`: el brief no declara que un rol dado de baja logica deniegue, y
        aqui no se inventan reglas de negocio.

        `.exists()` se resuelve en SQL y no materializa ninguna fila.

        Args:
            role_code: codigo de rol vigente del usuario.

        Returns:
            `True` si el codigo pertenece al catalogo.
        """

        return RolEntity.objects.filter(pk=role_code).exists()

    def permiso_de(self, role_code: str, operation_code: str) -> FilaPermiso | None:
        """
        Devuelve la fila de la matriz para el par (rol, operacion), o `None` si no existe.

        La AUSENCIA de fila se devuelve tal cual, sin traducirla a nada: es el servicio quien la
        convierte en denegacion (deny by default). Aqui no hay ninguna rama que invente un permiso
        ni un alcance por defecto cuando la consulta no devuelve nada.

        El par se busca con un PREDICADO SQL sobre las dos columnas de la clave (`role_code_id`,
        `operation_code_id`), nunca recorriendo la matriz en memoria, y `.first()` trae una sola
        fila.

        Args:
            role_code: codigo del rol vigente.
            operation_code: codigo de la operacion que se intenta ejecutar.

        Returns:
            La fila leida, o `None` si el par no esta en la matriz.
        """

        permiso = PermisoRolOperacionEntity.objects.filter(role_code_id=role_code, operation_code_id=operation_code).first()
        if permiso is None:
            return None

        return FilaPermiso(role_code=role_code, operation_code=operation_code, data_scope=permiso.data_scope)

    def permisos_de_rol(self, role_code: str) -> dict[str, str]:
        """
        Devuelve `{operation_code: data_scope}` con TODAS las filas que la matriz tiene para el rol.

        Es la lectura cruda, sin filtrar por alcance interpretable: el descarte fail-closed de los
        `data_scope` desconocidos lo hace el servicio, para que el repositorio no se convierta en un
        sitio mas donde se decide.

        `values_list` proyecta DOS columnas y no instancia ninguna entidad.

        Args:
            role_code: codigo del rol cuya capacidad se proyecta.

        Returns:
            Diccionario de operacion a alcance; vacio si el rol no tiene ninguna fila, que no es un
            error.
        """

        filas = PermisoRolOperacionEntity.objects.filter(role_code_id=role_code).values_list("operation_code_id", "data_scope")
        return {operation_code: data_scope for operation_code, data_scope in filas}

    def pares_con_entradas_contradictorias(self) -> tuple[tuple[str, str, int], ...]:
        """
        Devuelve los pares (rol, operacion) que tienen MAS DE UNA fila en la matriz.

        ES EL DIAGNOSTICO DE AC-PERM-01: la matriz debe dar una respuesta y solo una para cada par,
        de modo que un par duplicado significa que la decision depende de cual de las dos filas se
        lea primero. En Oracle lo impide la clave primaria compuesta `(role_code, operation_code)`;
        esta lectura existe para ACREDITARLO y para detectar una siembra incoherente, no para
        suplirla.

        LA AGREGACION VA EN SQL. `values(...).annotate(Count(...)).filter(total__gt=1)` se traduce a
        `GROUP BY ... HAVING COUNT(*) > 1` y viaja entero a Oracle: agrupar en Python obligaria a
        materializar la matriz completa, que crece con cada rol y cada operacion sembrados.

        El `order_by` explicito NO es cosmetico: `PermisoRolOperacionEntity` declara `ordering` en su
        `Meta` y Django arrastraria esas columnas al `GROUP BY`, alterando la agrupacion.

        Returns:
            Tupla de `(role_code, operation_code, numero_de_filas)`, vacia cuando la matriz es
            coherente (el caso normal).
        """

        agrupadas = (
            PermisoRolOperacionEntity.objects.values("role_code", "operation_code")
            .annotate(total=Count("permiso_id"))
            .filter(total__gt=1)
            .order_by("role_code", "operation_code")
        )
        return tuple((fila["role_code"], fila["operation_code"], fila["total"]) for fila in agrupadas)


__all__ = [
    "ESTADO_USUARIO_ACTIVO",
    "FilaPermiso",
    "IdentidadAutorizable",
    "RepositorioAutorizacion",
]
