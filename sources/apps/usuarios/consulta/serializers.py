"""
Serializadores de la consulta del censo de usuarios (EP-008 `GET /users`, REQ-006, REQ-039).

CONVENIO DE NOMBRES. El JSON del contrato va en camelCase y el modelo Python en snake_case: los
campos se declaran con el nombre camelCase del contrato y apuntan al atributo real del dataclass
con `source=`, igual que en `CredentialStatusPageSerializer` (EP-019). Asi el contrato manda en la
frontera HTTP sin contaminar los nombres del dominio.

LOS NOMBRES FISICOS DEL ESQUEMA T.5 NO SE TRADUCEN. `full_name`, `corporate_email`, `role_code`,
`created_at`, `last_login_at` y `role_changed_at` siguen llamandose asi en el modelo, en el
repositorio y en la proyeccion; lo unico que cambia de nombre es la REPRESENTACION HTTP, y solo
aqui.

EL ESQUEMA `UserListPage` LO FIJA ESTE MODULO. En el `openapi.yaml` esta todavia como germen
(`x-mind-placeholder: true`, sin propiedades): el modelo real es el que se declara aqui, de modo
que la documentacion que genera drf-spectacular y la respuesta real no puedan divergir. El contrato
NO se edita, igual que se hizo con EP-018 y EP-019.

NADA DE CREDENCIAL SALE POR AQUI (REQ-063, REQ-079). Ninguno de los serializadores de salida de
este modulo declara `password_hash`, `password_salt` ni `password_algorithm`, y el dataclass que
proyectan tampoco los transporta: la garantia es ESTRUCTURAL, no una omision que haya que recordar
en cada revision.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.usuarios.consulta import mensajes
from apps.usuarios.consulta.repositorio import (
    ESTADOS_USUARIO,
    LONGITUD_MAXIMA_BUSQUEDA,
    TAMANIO_PAGINA_MAXIMO,
    TAMANIO_PAGINA_POR_DEFECTO,
    CriteriosCenso,
)


__all__ = [
    "UserListPageSerializer",
    "UserListQuerySerializer",
    "UserSummarySerializer",
]


class UserListQuerySerializer(serializers.Serializer):
    """
    Query string del listado del censo (EP-008, REQ-006, REQ-039).

    TODOS LOS FILTROS SON OPCIONALES. Una peticion sin parametros es valida y devuelve la primera
    pagina de los usuarios ACTIVOS ordenados por antiguedad: ausente significa «no filtres por
    esto». La UNICA excepcion es `status`, cuya ausencia activa el filtro por defecto de ACTIVO
    (AC-USR-05); un `status` explicito lo SUSTITUYE, no se suma a el.

    `page` y `pageSize` tienen `default`, de modo que `validated_data` siempre trae paginacion: el
    caso de uso no tiene que decidir que hacer con su ausencia. Los limites (`min_value=1`,
    `max_value=TAMANIO_PAGINA_MAXIMO`) los hace cumplir DRF con un error de CAMPO -que dice
    exactamente cual se paso de rango- y el manejador unico lo traduce al 400 uniforme del servicio.
    El tope de 100 se IMPORTA del repositorio en vez de reescribirse: el numero del texto visible, el
    del contrato y el que acota la consulta son el mismo y no pueden desalinearse.

    `roleCode` SE VALIDA CONTRA EL CATALOGO LEIDO DE LA BASE (REQ-006, AC-ROL-07), nunca contra un
    enum escrito a mano: `cat_rol` es la fuente unica de verdad de los roles del sistema y un
    literal copiado aqui quedaria desalineado el dia que Liquibase siembre un rol nuevo.

    LA CONSULTA AL CATALOGO VIVE EN `validate_roleCode` Y NO EN LOS `choices` DEL CAMPO. Es una
    diferencia con consecuencias: los `choices` se resuelven al INSTANCIAR el serializador, y
    drf-spectacular instancia esta clase para generar el esquema de EP-008 (`parameters=[...]`).
    Cargarlos ahi ataria la generacion del contrato -y cualquier import que la dispare- a tener una
    conexion viva contra Oracle, de modo que `GET /docs/schema/` reventaria con un error de base de
    datos en cualquier entorno sin BBDD levantada. Validando en su lugar dentro de
    `validate_roleCode`, la consulta ocurre SOLO cuando hay una peticion real que validar, que es
    justo cuando la base esta disponible. El catalogo sigue siendo la unica fuente de verdad.

    `status`, en cambio, SI es un dominio cerrado en codigo, y no es una incoherencia: no es un
    catalogo con tabla propia sino el enumerado del CHECK de la columna `usuario.status` en el DDL.
    No hay nada que leer de la base porque no hay fila que leer.
    """

    q = serializers.CharField(
        source="texto",
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        max_length=LONGITUD_MAXIMA_BUSQUEDA,
        error_messages={"max_length": mensajes.BUSQUEDA_DEMASIADO_LARGA},
    )
    roleCode = serializers.CharField(source="role_code", required=False, trim_whitespace=True)
    status = serializers.CharField(required=False, trim_whitespace=True)
    page = serializers.IntegerField(
        source="pagina",
        required=False,
        min_value=1,
        default=1,
        error_messages={"min_value": mensajes.PAGINA_NO_VALIDA, "invalid": mensajes.PAGINA_NO_VALIDA},
    )
    pageSize = serializers.IntegerField(
        source="tamanio_pagina",
        required=False,
        min_value=1,
        max_value=TAMANIO_PAGINA_MAXIMO,
        default=TAMANIO_PAGINA_POR_DEFECTO,
        error_messages={
            "min_value": mensajes.TAMANIO_PAGINA_NO_VALIDO,
            "max_value": mensajes.TAMANIO_PAGINA_NO_VALIDO,
            "invalid": mensajes.TAMANIO_PAGINA_NO_VALIDO,
        },
    )

    def validate_roleCode(self, valor: str) -> str:
        """
        Comprueba que el rol pedido existe y esta ACTIVO en `cat_rol` (REQ-006, AC-ROL-07).

        Se valida contra la base y no contra una lista en codigo: los catalogos del proyecto se
        LEEN, no se codifican. Las filas dadas de baja logica (`is_active` distinto de `'Y'`,
        REQ-047) se excluyen porque un rol retirado no es un filtro valido, aunque sus usuarios
        historicos sigan existiendo.

        Se ejecuta SOLO durante la validacion de una peticion real, nunca al instanciar la clase:
        ver la nota del docstring de clase sobre la generacion del esquema con drf-spectacular.

        Raises:
            serializers.ValidationError: si el codigo no pertenece al catalogo vigente. DRF lo
                convierte en un error de campo y el manejador unico, en el 400 uniforme.
        """

        # El modelo se importa AQUI DENTRO: este modulo puede resolverse antes de `django.setup()`
        # y un import de modelos a nivel de modulo reventaria el arranque (`AppRegistryNotReady`).
        from apps.core.models import RolEntity

        if not RolEntity.objects.filter(role_code=valor, is_active="Y").exists():
            raise serializers.ValidationError(mensajes.FILTRO_NO_VALIDO)
        return valor

    def validate_status(self, valor: str) -> str:
        """
        Comprueba que el estado pedido pertenece al dominio cerrado ACTIVO/INACTIVO.

        No se consulta ninguna tabla porque no existe: `status` es un enumerado del CHECK de la
        columna `usuario.status`, no un catalogo. Rechazar aqui un valor fuera de dominio evita que
        la consulta se ejecute con un predicado que no puede casar con ninguna fila y devuelva un
        listado vacio indistinguible de «no hay nadie»; quien consulta merece saber que el filtro
        estaba mal escrito, no quedarse pensando que el censo esta vacio.

        Raises:
            serializers.ValidationError: si el estado no es `ACTIVO` ni `INACTIVO`.
        """

        if valor not in ESTADOS_USUARIO:
            raise serializers.ValidationError(mensajes.FILTRO_NO_VALIDO)
        return valor

    def a_criterios(self) -> CriteriosCenso:
        """
        Construye los criterios del caso de uso a partir de la query string ya validada.

        `validated_data` llega con las claves SNAKE_CASE del dominio -las que declara cada campo en
        su `source=`-, asi que el mapeo es directo y no hay segunda tabla de equivalencias que
        mantener. Los filtros ausentes quedan en `None`, que para `CriteriosCenso` significa «no
        filtres por esto»; `status` en `None` es ademas la senial de aplicar el ACTIVO por defecto.

        Returns:
            CriteriosCenso: los criterios inmutables que recorren servicio y repositorio sin que
            ninguna capa los retoque.
        """

        datos = self.validated_data
        return CriteriosCenso(
            role_code=datos.get("role_code"),
            status=datos.get("status"),
            texto=datos.get("texto"),
            pagina=datos.get("pagina", 1),
            tamanio_pagina=datos.get("tamanio_pagina", TAMANIO_PAGINA_POR_DEFECTO),
        )


class UserSummarySerializer(serializers.Serializer):
    """
    Ficha resumida de UN usuario dentro del listado del censo (EP-008, REQ-006).

    Proyecta el dataclass `UsuarioDelCenso`. Todos los campos son de SOLO lectura: este recurso es
    una consulta y no admite escritura.

    NO SE DECLARA NINGUN CAMPO DE CREDENCIAL. `password_hash`, `password_salt` y
    `password_algorithm` no estan aqui Y TAMPOCO ESTAN EN EL DATACLASS QUE SE PROYECTA, de modo que
    la regla de negocio de REQ-006 -ninguna respuesta del endpoint contiene material de credencial-
    se cumple POR CONSTRUCCION y no por acordarse de omitirlos en cada cambio. Publicar uno exigiria
    anadirlo primero a la proyeccion del dominio, lo que quedaria a la vista en el diff.

    `roleName`, `lastLoginAt` y `roleChangedAt` admiten nulo: la etiqueta del rol puede no resolverse
    para una fila huerfana, un usuario recien dado de alta puede no haber entrado nunca y puede no
    habersele cambiado jamas el rol. `null` es el dato correcto; una cadena vacia o una fecha
    inventada serian mentira.
    """

    userId = serializers.IntegerField(source="user_id", read_only=True)
    fullName = serializers.CharField(source="full_name", read_only=True)
    corporateEmail = serializers.CharField(source="corporate_email", read_only=True)
    roleCode = serializers.CharField(source="role_code", read_only=True)
    roleName = serializers.CharField(source="role_name", read_only=True, allow_null=True)
    status = serializers.CharField(read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    lastLoginAt = serializers.DateTimeField(source="last_login_at", read_only=True, allow_null=True)
    roleChangedAt = serializers.DateTimeField(source="role_changed_at", read_only=True, allow_null=True)


class UserListPageSerializer(serializers.Serializer):
    """
    Pagina del censo de usuarios (`UserListPage`, respuesta 200 de EP-008).

    Proyecta el dataclass `PaginaCenso`: las filas mas el contexto de paginacion que el cliente
    necesita para pintar el paginador.

    EL ESQUEMA `UserListPage` LO FIJA ESTA CLASE. En el `openapi.yaml` figura como germen
    (`x-mind-placeholder: true`, sin propiedades) y el contrato NO se edita: el modelo real es este,
    de modo que lo que documenta drf-spectacular y lo que devuelve el endpoint son necesariamente lo
    mismo.

    `totalCount` es el numero de usuarios que cumplen los criterios en la CONSULTA FILTRADA
    COMPLETA, no los de esta pagina; `items` trae como mucho `pageSize` (REQ-039).

    CERO COINCIDENCIAS DEVUELVE 200, NUNCA UN ERROR (AC-USR-05). Cuando nadie cumple los filtros
    -o cuando la pagina pedida esta por encima del total- la respuesta es correcta, `items` viene
    vacio y `emptyMessage` trae el literal informativo que explica por que no hay nada que mostrar.
    Con filas, `emptyMessage` es `null`: el campo existe siempre en el cuerpo para que el cliente no
    tenga que distinguir entre «ausente» y «nulo».
    """

    items = UserSummarySerializer(many=True, read_only=True)
    page = serializers.IntegerField(read_only=True)
    pageSize = serializers.IntegerField(source="page_size", read_only=True)
    totalCount = serializers.IntegerField(source="total_count", read_only=True)
    totalPages = serializers.IntegerField(source="total_pages", read_only=True)
    emptyMessage = serializers.CharField(source="empty_message", read_only=True, allow_null=True)
