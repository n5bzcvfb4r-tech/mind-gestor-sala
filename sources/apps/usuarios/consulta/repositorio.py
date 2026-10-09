"""
Acceso a datos del CENSO DE USUARIOS (EP-008, REQ-006, REQ-039, tabla `usuario`).

POR QUE TODO SE RESUELVE EN LA CONSULTA
---------------------------------------
Filtrado, busqueda, orden, recuento y troceo viajan a Oracle en UNA sola sentencia. El anti-patron
que este modulo evita a proposito es el de «cargar todo y filtrar en Python»: traer el censo entero
a memoria para quedarse con 25 filas crece de forma lineal con el numero de usuarios, hace que el
`total_count` publicado dependa de lo que quepa en el proceso y, sobre todo, obliga a materializar
columnas -entre ellas las de credencial- que no se necesitan para nada (REQ-024, AC-BAN-09). Lo que
la base no envia no se puede filtrar mal, ni loggear por descuido, ni serializar por error.

EL TROCEO ES SOBRE EL QUERYSET, NUNCA SOBRE UNA LISTA
------------------------------------------------------
`queryset[inicio:inicio + tamanio]` lo traduce el backend de Oracle a `OFFSET .. FETCH NEXT`. No
hay -ni puede haber- SQL textual con `LIMIT`/`OFFSET`: esa sintaxis no existe en Oracle y la
sentencia fallaria. Trocear una lista ya materializada seria sintacticamente valido y
funcionalmente desastroso: leeria el censo completo en cada pagina.

AUSENCIA DE FILTRO NO SIGNIFICA «TODOS» (AC-USR-05)
----------------------------------------------------
El estado es el UNICO filtro con valor por defecto. Sin `status` explicito el listado devuelve solo
los usuarios ACTIVOS, porque el censo operativo del administrador son las personas que hoy pueden
entrar al sistema; las cuentas dadas de baja logica (REQ-047) siguen en la tabla para siempre y
mezclarlas por omision haria crecer el listado con filas historicas que nadie ha pedido. Quien
quiera verlas debe pedirlas: `status=INACTIVO` SUSTITUYE al valor por defecto, no se suma a el.

ESTE REPOSITORIO NO VALIDA ENTRADA
----------------------------------
No lanza errores de usuario ni comprueba catalogos para filtrar: eso es del serializador de la
vista, que es la unica capa que puede devolver un 400 con mensaje. Lo unico que hace aqui es
ACOTAR los valores de paginacion a un rango sano, para que una llamada interna descuidada no
termine pidiendole a Oracle un `FETCH NEXT -5 ROWS`.

SECRETOS (REQ-063, REQ-076)
---------------------------
NINGUN metodo de este repositorio filtra, ordena ni devuelve `password_hash`, `password_salt` ni
`password_algorithm` como dato publicable. La PROYECCION que sale por HTTP la decide el servicio,
que construye un dataclass cerrado sin ningun atributo de credencial.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db import models
from django.db.models import CharField, F, Func, Q, Value
from django.db.models.functions import Lower

from apps.core.repositorios import RepositorioBase


if TYPE_CHECKING:  # pragma: no cover - solo tipado: evita importar modelos antes de django.setup()
    from apps.core.models import UsuarioEntity


#: Estados de cuenta del esquema T.5 (CHECK de la columna `usuario.status`). Dominio CERRADO: es un
#: enumerado del DDL, no un catalogo con tabla propia, asi que no hay nada que leer de la base.
ESTADO_ACTIVO = "ACTIVO"
ESTADO_INACTIVO = "INACTIVO"
ESTADOS_USUARIO: tuple[str, ...] = (ESTADO_ACTIVO, ESTADO_INACTIVO)

#: REQ-039: paginas de 25 usuarios cuando el cliente no pide otra cosa.
TAMANIO_PAGINA_POR_DEFECTO = 25

#: Tope duro del tamanio de pagina. No es cosmetico: sin el, `pageSize=100000` convertiria una
#: consulta paginada en una descarga del censo entero, con el coste de memoria y de red que eso
#: tiene en el proceso y en Oracle.
TAMANIO_PAGINA_MAXIMO = 100

#: Tope del termino de busqueda. Coincide con el ancho de `corporate_email` (150) y supera al de
#: `full_name` (120): un texto mas largo que la propia columna no puede coincidir con nada.
LONGITUD_MAXIMA_BUSQUEDA = 150


def normalizar_busqueda(texto: str | None) -> str:
    """
    Pliega el termino de busqueda a su forma comparable: recortado, en minusculas y SIN diacriticos.

    POR QUE ESTA FUNCION EXISTE. La busqueda libre debe ser insensible a mayusculas Y a acentos
    (REQ-006): quien escribe «jose» tiene que encontrar a «José». La mitad del trabajo la hace
    Oracle sobre la COLUMNA (ver `RepositorioCensoUsuarios.buscar`); esta funcion hace la otra
    mitad, sobre el TERMINO BUSCADO. Las dos partes de la comparacion tienen que estar plegadas de
    la MISMA manera: plegar solo la columna dejaria «José» buscando contra una «Jose» almacenada y
    no encontraria nada, que es precisamente el fallo que se quiere evitar.

    EL RESULTADO DEBE COINCIDIR CON EL DE LA EXPRESION SQL. Lo que aqui se calcula en Python es el
    equivalente de `CONVERT(LOWER(columna), 'US7ASCII')` en Oracle: descomposicion canonica,
    descarte de las marcas combinantes y vuelta a componer. Si alguna vez cambia una de las dos
    mitades, la otra tiene que cambiar con ella o la busqueda empezara a fallar en silencio -sin
    excepcion, sin traza, solo con resultados de menos-.

    COMO PLIEGA LOS ACENTOS. `NFD` separa cada caracter acentuado en su letra base mas una marca
    combinante (`é` -> `e` + U+0301); se descartan los caracteres de categoria `Mn` (Mark,
    Nonspacing), que son exactamente esas marcas; `NFC` vuelve a componer lo que quede, de modo que
    el resultado es una cadena normalizada y no un hibrido a medio descomponer.

    Args:
        texto: termino tal cual lo escribio quien consulta, o `None` si no hay busqueda.

    Returns:
        El termino plegado, o cadena vacia si no habia texto o solo habia espacios. La cadena vacia
        es la senial de «no filtres por texto»: filtrar por ella haria coincidir a todo el censo.
    """

    if not texto:
        return ""

    descompuesto = unicodedata.normalize("NFD", texto.strip().lower())
    sin_marcas = "".join(caracter for caracter in descompuesto if unicodedata.category(caracter) != "Mn")
    return unicodedata.normalize("NFC", sin_marcas)


def _sin_diacriticos(columna: str) -> Func:
    """
    Expresion SQL que pliega una columna de texto a minusculas y sin acentos, DENTRO de Oracle.

    `CONVERT(..., 'US7ASCII')` es la construccion NATIVA de Oracle para plegar los diacriticos: al
    transcodificar a un juego de 7 bits, las letras acentuadas pierden la tilde en vez de fallar
    (`José` -> `Jose`). Envuelta sobre `LOWER`, deja la columna en la misma forma canonica que
    `normalizar_busqueda` produce en Python para el termino buscado, de modo que las dos partes de
    la comparacion son comparables.

    LO IMPORTANTE ES DONDE SE EVALUA. Al ser una anotacion del ORM, el plegado y la comparacion
    viajan a la CONSULTA y nunca a memoria (REQ-024, AC-BAN-09): Oracle devuelve solo las filas que
    coinciden. La alternativa -traer el censo y comparar en Python- obligaria a materializar todas
    las filas y todas sus columnas, incluidas las de credencial, para descartar casi todas.
    """

    return Func(Lower(F(columna)), Value("US7ASCII"), function="CONVERT", output_field=CharField())


@dataclass(frozen=True, slots=True)
class CriteriosCenso:
    """
    Criterios de la consulta del censo (EP-008, REQ-006, REQ-039).

    Es inmutable (`frozen`) para que el mismo objeto pueda recorrer serializador, servicio y
    repositorio sin que ninguna capa lo retoque por el camino: los criterios que se aplican son
    exactamente los que se validaron.

    `None` significa «no filtres por esto» y nunca «valor nulo», con UNA excepcion deliberada:
    `status` ausente activa el filtro por defecto de usuarios ACTIVOS (AC-USR-05). La diferencia
    esta explicada en la nota del modulo.

    `pagina` y `tamanio_pagina` SI tienen valor por defecto porque siempre hay una pagina que
    devolver: una consulta sin paginacion explicita es la primera pagina, no «todas las filas».
    """

    role_code: str | None = None
    status: str | None = None
    texto: str | None = None
    pagina: int = 1
    tamanio_pagina: int = TAMANIO_PAGINA_POR_DEFECTO


class RepositorioCensoUsuarios(RepositorioBase):
    """
    Consulta paginada del censo sobre `UsuarioEntity` (EP-008, REQ-006, REQ-039).

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

    def buscar(self, criterios: CriteriosCenso) -> tuple[list[UsuarioEntity], int]:
        """
        Devuelve la pagina solicitada del censo y el total de coincidencias: `(filas, total)`.

        El `total` es el `COUNT(*)` del queryset YA FILTRADO y ANTES de paginar, porque lo que el
        consumidor necesita para pintar el paginador es cuantos usuarios cumplen los criterios, no
        cuantos caben en la pagina. Contar despues del troceo daria siempre `<= tamanio_pagina` y el
        numero de paginas seria siempre 1.

        ESTADO. Sin `status` explicito se filtra por ACTIVO: ausencia de filtro significa «solo
        ACTIVO» (AC-USR-05) y NO «todos». Un `status` explicito SUSTITUYE a ese valor por defecto.

        ROL. Se filtra por `role_code_id`, la columna literal, sin visitar `cat_rol`: la existencia
        del codigo en el catalogo ya la comprobo el serializador, y volver a unir contra la tabla de
        roles solo para filtrar anadiria un JOIN que no aporta nada al predicado.

        BUSQUEDA. Ver `normalizar_busqueda` y `_sin_diacriticos`: el termino y las dos columnas se
        pliegan de la misma forma y la comparacion la resuelve Oracle.

        Args:
            criterios: filtros, texto de busqueda, pagina y tamanio pedidos, ya validados.

        Returns:
            Tupla con las filas de la pagina (lista vacia si la pagina excede el total, que NO es un
            error) y el total de coincidencias de la consulta filtrada completa.
        """

        # `select_related("role_code")` resuelve el rol en el mismo SELECT: sin el, proyectar el
        # listado dispararia una consulta por fila (N+1) para leer el nombre del rol de cada una.
        queryset = self.modelo.objects.select_related("role_code")

        # Ausencia de filtro == solo ACTIVO (AC-USR-05), no «todos»: ver el docstring del modulo.
        queryset = queryset.filter(status=criterios.status or ESTADO_ACTIVO)

        if criterios.role_code:
            # El campo del modelo es un ForeignKey llamado `role_code`; el filtro por el CODIGO
            # literal es `role_code_id`, que compara contra la columna sin visitar la tabla `cat_rol`.
            queryset = queryset.filter(role_code_id=criterios.role_code)

        patron = normalizar_busqueda(criterios.texto)
        if patron:
            # El plegado de las columnas y el del termino son el MISMO: `CONVERT(LOWER(col),
            # 'US7ASCII')` en Oracle y `normalizar_busqueda` en Python. El filtro viaja a la
            # CONSULTA y nunca a memoria (REQ-024, AC-BAN-09).
            queryset = queryset.annotate(
                _nombre_ai=_sin_diacriticos("full_name"),
                _correo_ai=_sin_diacriticos("corporate_email"),
            ).filter(Q(_nombre_ai__contains=patron) | Q(_correo_ai__contains=patron))

        # COUNT(*) sobre el conjunto filtrado COMPLETO, antes de cualquier troceo.
        total = queryset.count()

        # REQ-039: los usuarios se listan del alta mas reciente a la mas antigua. El orden lo
        # resuelve Oracle sobre el conjunto COMPLETO; un `sorted()` en Python solo podria ordenar la
        # pagina ya traida, con lo que el criterio seria falso en cuanto hubiera mas de una pagina.
        queryset = queryset.order_by("-created_at", "-user_id")
        # El desempate por `user_id` descendente es OBLIGATORIO: sin un criterio estable, dos altas
        # con el mismo `created_at` bailan entre paginas y la paginacion repite u omite usuarios.

        # Acotado defensivo: este repositorio no valida entrada (eso es del serializador), pero
        # tampoco le traslada a Oracle un desplazamiento negativo ni un tamanio absurdo.
        tamanio = min(max(1, criterios.tamanio_pagina), TAMANIO_PAGINA_MAXIMO)
        inicio = (max(1, criterios.pagina) - 1) * tamanio

        # El troceo va SOBRE EL QUERYSET para que Oracle lo traduzca a `OFFSET .. FETCH NEXT`. Una
        # pagina por encima del total devuelve lista vacia, que NO es un error (AC-USR-05).
        return list(queryset[inicio : inicio + tamanio]), total

    def existe_rol_vigente(self, role_code: str) -> bool:
        """
        Indica si el codigo de rol pertenece al catalogo `cat_rol` y sigue vigente.

        EL CATALOGO SE LEE DE LA BASE, NUNCA SE CODIFICA COMO ENUM. `cat_rol` es la fuente unica de
        verdad de los roles del sistema: una lista copiada en Python quedaria desalineada el dia que
        Liquibase siembre un rol nuevo, y el filtro rechazaria un valor perfectamente legitimo.

        Las filas dadas de baja logica (`is_active` distinto de `'Y'`, REQ-047) se excluyen: un rol
        retirado no es un filtro valido aunque sus usuarios historicos sigan existiendo.

        Args:
            role_code: codigo de rol tal cual llego en la query string.

        Returns:
            `True` si existe y esta activo; `False` en cualquier otro caso.
        """

        # El modelo se importa AQUI DENTRO: este modulo puede resolverse antes de `django.setup()` y
        # un import de modelos a nivel de modulo reventaria el arranque (`AppRegistryNotReady`).
        from apps.core.models import RolEntity

        return RolEntity.objects.filter(role_code=role_code, is_active="Y").exists()


__all__ = [
    "ESTADOS_USUARIO",
    "ESTADO_ACTIVO",
    "ESTADO_INACTIVO",
    "LONGITUD_MAXIMA_BUSQUEDA",
    "TAMANIO_PAGINA_MAXIMO",
    "TAMANIO_PAGINA_POR_DEFECTO",
    "CriteriosCenso",
    "RepositorioCensoUsuarios",
    "normalizar_busqueda",
]
