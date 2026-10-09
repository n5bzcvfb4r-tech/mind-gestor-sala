"""
Acceso a datos del ESTADO DE CREDENCIAL de los usuarios (EP-019, REQ-074, tabla `usuario`).

POR QUE TODO SE RESUELVE EN LA CONSULTA
---------------------------------------
Filtrado, busqueda, orden, recuento y troceo viajan a Oracle en UNA sola sentencia. El anti-patron
que este modulo evita a proposito es el de «cargar todo y filtrar en Python»: traer el censo entero
a memoria para quedarse con 25 filas crece de forma lineal con el numero de usuarios, hace que el
`total_count` publicado dependa de lo que quepa en el proceso y, sobre todo, obliga a materializar
columnas -entre ellas las de credencial- que no se necesitan para nada. Lo que la base no envia no
se puede filtrar mal, ni loggear por descuido, ni serializar por error.

EL TROCEO ES SOBRE EL QUERYSET, NUNCA SOBRE UNA LISTA
------------------------------------------------------
`queryset[inicio:inicio + TAMANIO_PAGINA]` lo traduce el backend de Oracle a `OFFSET .. FETCH NEXT`.
No hay -ni puede haber- SQL textual con `LIMIT`/`OFFSET`: esa sintaxis no existe en Oracle y la
sentencia fallaria. Trocear una lista ya materializada seria sintacticamente valido y funcionalmente
desastroso: leeria el censo completo en cada pagina.

EL INSTANTE LO PONE QUIEN LLAMA
-------------------------------
`buscar` recibe `ahora` por parametro y no lo calcula. El servicio lo sella una sola vez con
`utc_now()` y lo usa tanto para el filtro `locked_until > ahora` como para derivar el campo `locked`
de cada fila publicada. Con dos relojes distintos -uno aqui y otro en la proyeccion- una fila podria
entrar en el filtro «bloqueado» y salir con `locked = false` por unos microsegundos de diferencia.

SECRETOS (REQ-063, REQ-076)
---------------------------
NINGUN metodo de este repositorio selecciona ni devuelve `password_hash`, `password_salt` ni
`password_algorithm` como dato publicable: no se filtra por ellos, no se ordenan por ellos y no
aparecen en ninguna traza. La PROYECCION que sale por HTTP la decide el serializer de la vista, y el
test de AC-RST-04 exige 0 ocurrencias de `password_hash` en la respuesta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from django.db import models
from django.db.models import Case, IntegerField, Q, Value, When

from apps.core.repositorios import RepositorioBase


if TYPE_CHECKING:  # pragma: no cover - solo tipado: evita importar modelos antes de django.setup()
    from apps.core.models import UsuarioEntity


#: REQ-074 regla 2: el listado devuelve como maximo 25 usuarios por pagina.
TAMANIO_PAGINA = 25

#: REQ-074 regla 4 / validacion 4: una busqueda libre tiene al menos 3 caracteres.
LONGITUD_MINIMA_BUSQUEDA = 3

ESTADO_BLOQUEADO = "bloqueado"
ESTADO_NO_BLOQUEADO = "no bloqueado"
ESTADOS_BLOQUEO: tuple[str, ...] = (ESTADO_BLOQUEADO, ESTADO_NO_BLOQUEADO)

# `must_change_password` es CHAR(1) 'Y'/'N' en el esquema T.5, no un booleano: el ORM compara contra
# estos literales y la conversion a `bool` se hace una sola vez, en la frontera de la proyeccion.
INDICADOR_SI = "Y"
INDICADOR_NO = "N"


@dataclass(frozen=True, slots=True)
class CriteriosEstadoCredencial:
    """
    Criterios de busqueda del listado de estado de credencial (REQ-074 regla 4).

    Es inmutable (`frozen`) para que el mismo objeto pueda recorrer servicio y repositorio sin que
    ninguna capa lo retoque por el camino: los criterios que se auditan son exactamente los que se
    aplicaron. Todos los filtros son OPCIONALES y `None` significa «no filtres por esto», nunca «no
    hay valor»: asi el caso sin filtros es un queryset sin predicados, no una consulta con
    comparaciones contra nulo.
    """

    role_code: str | None = None
    estado_bloqueo: str | None = None
    must_change_password: bool | None = None
    texto: str | None = None
    pagina: int = 1


class RepositorioEstadoCredencial(RepositorioBase):
    """
    Consulta paginada del estado de credencial sobre `UsuarioEntity` (EP-019, REQ-074).

    Hereda de `RepositorioBase` y por tanto NO publica ninguna operacion de borrado (REQ-047); de
    hecho es de SOLO LECTURA: no escribe nada en `usuario`.

    El modelo se resuelve de forma PEREZOSA en el constructor porque importar modelos a nivel de
    modulo revienta con `AppRegistryNotReady` antes de `django.setup()`.
    """

    def __init__(self, modelo: type[models.Model] | None = None) -> None:
        if modelo is None:
            from apps.core.models import UsuarioEntity

            modelo = UsuarioEntity
        super().__init__(modelo)

    def buscar(self, criterios: CriteriosEstadoCredencial, *, ahora: datetime) -> tuple[list[UsuarioEntity], int]:
        """
        Devuelve la pagina solicitada y el total de coincidencias: `(filas, total)`.

        El `total` es el `COUNT(*)` del queryset YA FILTRADO y ANTES de paginar, porque lo que el
        consumidor necesita para pintar el paginador es cuantos usuarios cumplen los criterios, no
        cuantos caben en la pagina. Contar despues del troceo daria siempre `<= 25` y el numero de
        paginas seria siempre 1.

        CRITERIO DE BLOQUEO (REQ-074). «Bloqueada» es `locked_until` EN EL FUTURO respecto a `ahora`,
        no simplemente informado: una cuenta cuyo bloqueo vencio ayer conserva la marca en la columna
        y esta operativa. Filtrar por `locked_until__isnull=False` la daria por bloqueada y el
        administrador perseguiria un problema inexistente.

        BUSQUEDA LIBRE. Se recorta con `strip()` y, si mide menos de `LONGITUD_MINIMA_BUSQUEDA`, se
        IGNORA en vez de rechazarse: este repositorio no valida entrada ni lanza errores de usuario
        -eso es del serializer de la vista, que es quien puede devolver un 400 con mensaje-. Un
        repositorio que lanzara errores de formato obligaria a cada llamador a capturarlos.

        Args:
            criterios: filtros, texto de busqueda y pagina pedida.
            ahora: instante sellado por el servicio; decide que bloqueos estan vigentes.

        Returns:
            Tupla con las filas de la pagina (lista vacia si la pagina excede el total, que NO es un
            error segun REQ-074) y el total de coincidencias.
        """

        # `select_related("role_code")` resuelve el rol en el mismo SELECT: sin el, proyectar el
        # listado dispararia una consulta por fila (N+1) para leer el codigo de rol.
        queryset = self.modelo.objects.select_related("role_code")

        if criterios.role_code:
            # El campo del modelo es un ForeignKey llamado `role_code`; el filtro por el CODIGO
            # literal es `role_code_id`, que compara contra la columna sin visitar la tabla `rol`.
            queryset = queryset.filter(role_code_id=criterios.role_code)

        if criterios.estado_bloqueo == ESTADO_BLOQUEADO:
            queryset = queryset.filter(locked_until__gt=ahora)
        elif criterios.estado_bloqueo == ESTADO_NO_BLOQUEADO:
            queryset = queryset.filter(Q(locked_until__isnull=True) | Q(locked_until__lte=ahora))

        if criterios.must_change_password is True:
            queryset = queryset.filter(must_change_password=INDICADOR_SI)
        elif criterios.must_change_password is False:
            queryset = queryset.filter(must_change_password=INDICADOR_NO)

        texto = (criterios.texto or "").strip()
        if len(texto) >= LONGITUD_MINIMA_BUSQUEDA:
            queryset = queryset.filter(Q(full_name__icontains=texto) | Q(corporate_email__icontains=texto))

        # COUNT(*) sobre el conjunto filtrado completo, antes de cualquier troceo.
        total = queryset.count()

        # REQ-074 regla 3: las cuentas bloqueadas y las pendientes de cambio preceden al resto. El
        # orden se expresa con CASE/WHEN para que lo resuelva Oracle sobre el conjunto COMPLETO; un
        # `sorted()` en Python solo podria ordenar la pagina ya traida, con lo que el criterio de
        # prioridad seria falso en cuanto hubiera mas de 25 coincidencias.
        queryset = queryset.annotate(
            _prioridad_bloqueo=Case(When(locked_until__gt=ahora, then=Value(0)), default=Value(1), output_field=IntegerField()),
            _prioridad_cambio=Case(
                When(must_change_password=INDICADOR_SI, then=Value(0)), default=Value(1), output_field=IntegerField()
            ),
        ).order_by("_prioridad_bloqueo", "_prioridad_cambio", "full_name", "user_id")
        # El desempate final por `user_id` es OBLIGATORIO: sin un criterio estable, dos filas con el
        # mismo nombre bailan entre paginas y la paginacion repite u omite usuarios.

        pagina = max(1, criterios.pagina)
        inicio = (pagina - 1) * TAMANIO_PAGINA

        # El troceo va SOBRE EL QUERYSET para que Oracle lo traduzca a `OFFSET .. FETCH NEXT`.
        return list(queryset[inicio : inicio + TAMANIO_PAGINA]), total


__all__ = [
    "ESTADOS_BLOQUEO",
    "ESTADO_BLOQUEADO",
    "ESTADO_NO_BLOQUEADO",
    "INDICADOR_NO",
    "INDICADOR_SI",
    "LONGITUD_MINIMA_BUSQUEDA",
    "TAMANIO_PAGINA",
    "CriteriosEstadoCredencial",
    "RepositorioEstadoCredencial",
]
