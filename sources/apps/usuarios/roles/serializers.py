"""
Serializadores del cambio de rol y de su historico (EP-011 `PUT /users/{userId}/role`, EP-012
`GET /users/{userId}/role-history`; REQ-005, REQ-008, REQ-043).

CONVENIO DE NOMBRES. El JSON del contrato va en camelCase y el modelo Python en snake_case: los
campos se declaran con el nombre camelCase del contrato y apuntan al atributo real del dataclass
con `source=`, igual que en EP-007 y EP-008. Asi el contrato manda en la frontera HTTP sin
contaminar los nombres del dominio.

LOS ESQUEMAS `RoleChangeRequest`, `UserRoleDetail` Y `RoleHistoryPage` LOS FIJA ESTE MODULO. En el
`openapi.yaml` los tres figuran como GERMEN (`x-mind-placeholder: true`, sin una sola propiedad) y
EL CONTRATO NO SE EDITA: el modelo real es el que se declara aqui, de modo que lo que documenta
drf-spectacular y lo que acepta o devuelve el endpoint son necesariamente lo mismo. La ruta, el
verbo y el 200 se transcriben al pie de la letra y lo unico que este modulo aporta es el cuerpo que
el germen deja sin describir.

QUE SE VALIDA AQUI Y QUE NO
---------------------------
Este modulo rechaza la FORMA de la peticion y nada mas (400): es la unica capa que puede atribuir
el fallo a un CAMPO concreto y devolver el literal exacto de `mensajes`. Las reglas que necesitan
el ESTADO ACTUAL del usuario -que el rol pedido sea el que ya ostenta (409, `MismoRolError`) o que
el actor intente retirarse a si mismo el rol de administracion (422,
`AutorretiradaRolAdministracionError`)- son de NEGOCIO y viven en el servicio, que es quien lee ese
estado dentro de la misma transaccion en la que decide.

EL SERVICIO NO SE IMPORTA A NIVEL DE MODULO. Los dataclasses del dominio solo se traen bajo
`TYPE_CHECKING` -para el tipado- y, en tiempo de ejecucion, DENTRO de `a_datos()` y `a_criterios()`.
Asi este modulo se puede importar (y drf-spectacular puede generar el esquema) sin arrastrar el
caso de uso ni sus dependencias.

NADA DE CREDENCIAL SALE POR AQUI (REQ-063, REQ-076, REQ-079). Ver el docstring de
`UserRoleDetailSerializer`: la garantia no es una omision cuidadosa, es estructural.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import serializers

from apps.usuarios.roles import mensajes


if TYPE_CHECKING:  # pragma: no cover - solo anotaciones: el servicio no se importa en tiempo de ejecucion
    from apps.usuarios.roles.servicio import CriteriosHistoricoRol, DatosCambioRol


# Paginacion del historico de rol (EP-012, REQ-008). El tamanio por defecto es el que se aplica
# cuando el cliente no pide ninguno y el maximo acota lo que un solo cliente puede arrancarle a la
# base en una peticion. Los dos numeros viven aqui una sola vez: el que valida el campo, el que
# documenta el contrato y el que acota la consulta son el mismo y no pueden desalinearse.
TAMANIO_PAGINA_POR_DEFECTO = 25
TAMANIO_PAGINA_MAXIMO = 100


__all__ = [
    "TAMANIO_PAGINA_MAXIMO",
    "TAMANIO_PAGINA_POR_DEFECTO",
    "RoleChangeRequestSerializer",
    "RoleHistoryEntrySerializer",
    "RoleHistoryPageSerializer",
    "RoleHistoryQuerySerializer",
    "UserRoleDetailSerializer",
]


class RoleChangeRequestSerializer(serializers.Serializer):
    """
    Cuerpo de la peticion de cambio de rol (`RoleChangeRequest`, EP-011, REQ-043).

    UN SOLO CAMPO, OBLIGATORIO. El rol de destino es el unico dato que aporta el cliente: el sujeto
    viaja en la ruta (`userId`) y el actor sale SIEMPRE del contexto de sesion (REQ-048, REQ-064),
    nunca del payload. No se declara ningun `changedBy` ni alias suyo ni siquiera como campo
    ignorado: aceptarlo seria permitir que el cliente firme la auditoria con el nombre de otro, y un
    campo declarado acaba leyendose tarde o temprano.

    EL ESQUEMA `RoleChangeRequest` LO FIJA ESTA CLASE. En el `openapi.yaml` es un germen
    (`x-mind-placeholder: true`, sin propiedades) y el contrato no se edita: el modelo real es este,
    de modo que lo que documenta drf-spectacular y lo que acepta el endpoint son lo mismo.

    AQUI SOLO SE RECHAZA LA FORMA (400). Este serializador NO decide si el rol pedido es el que el
    usuario YA tiene -eso es un conflicto con el estado vigente y se responde 409 (REQ-005 RN-01)-
    ni si el actor esta intentando retirarse su propio rol de administracion -regla de negocio que
    se responde 422 (REQ-043 RN-03)-. Las dos necesitan leer el estado actual del usuario y del
    actor, y ese estado solo es fiable dentro de la transaccion del servicio; comprobarlo aqui seria
    decidir sobre una foto anterior. La validacion de forma, en cambio, es la UNICA capa que puede
    decir QUE CAMPO viene mal y por eso vive aqui.

    Los tres literales de rechazo del campo (`required`, `blank`, `null`) apuntan al mismo texto:
    desde el punto de vista de quien rellena el formulario, un rol ausente, uno vacio y uno nulo son
    el mismo defecto -no ha elegido rol- y merecen la misma explicacion.
    """

    roleCode = serializers.CharField(
        source="role_code",
        required=True,
        max_length=32,
        allow_blank=False,
        trim_whitespace=True,
        error_messages={
            "required": mensajes.ROL_NO_VALIDO,
            "blank": mensajes.ROL_NO_VALIDO,
            "null": mensajes.ROL_NO_VALIDO,
            "max_length": mensajes.ROL_NO_VALIDO,
        },
    )

    def validate_roleCode(self, valor: str) -> str:  # el hook de DRF se nombra por el CAMPO del contrato, no por su `source`
        """
        Comprueba que el rol pedido existe y esta ACTIVO en `cat_rol` (AC-ROL-02, AC-ROL-03).

        SE VALIDA CONTRA EL CATALOGO LEIDO DE LA BASE, NUNCA CONTRA UN ENUM DE PYTHON. `cat_rol` es
        un catalogo cerrado que SIEMBRA Liquibase y que se lee de Oracle: una lista de codigos
        copiada en el codigo quedaria desalineada el dia que el catalogo cambie, y pasaria a
        aceptar o rechazar roles que la base no respalda.

        UN ROL DADO DE BAJA LOGICA NO ES UN DESTINO VALIDO. La fila puede seguir existiendo
        (`is_active='N'`, REQ-047) porque sus asignaciones historicas tienen que seguir
        resolviendose, pero ya no se asigna a nadie. Por eso el filtro exige `is_active="Y"` y no
        solo la existencia del codigo.

        EL NOMBRE DEL METODO LO FIJA DRF, QUE RESUELVE EL HOOK POR EL NOMBRE DEL CAMPO
        (`roleCode`) Y NO POR SU `source` (`role_code`). Llamarlo `validate_role_code` lo dejaria
        sin invocar y la comprobacion del catalogo no llegaria a ejecutarse jamas. Es el mismo
        convenio de `UserCreateRequestSerializer.validate_roleCode` (EP-007).

        LA CONSULTA AL CATALOGO VIVE AQUI Y NO EN LOS `choices` DEL CAMPO. Los `choices` se
        resuelven al INSTANCIAR el serializador, y drf-spectacular instancia esta clase para generar
        el esquema de EP-011: cargarlos ahi ataria la generacion del contrato -y cualquier import
        que la dispare- a tener una conexion viva contra Oracle, de modo que `GET /docs/schema/`
        reventaria en cualquier entorno sin BBDD levantada. Validando aqui, la consulta ocurre SOLO
        cuando hay una peticion real que validar, que es justo cuando la base esta disponible.

        Args:
            valor: codigo de rol recibido, ya con los extremos recortados por `trim_whitespace`.

        Returns:
            str: el mismo codigo, ya comprobado contra el catalogo vigente.

        Raises:
            serializers.ValidationError: si el codigo no pertenece al catalogo vigente. DRF lo
                convierte en un error de CAMPO y el manejador unico, en el 400 uniforme.
        """

        # El modelo se importa AQUI DENTRO: este modulo puede resolverse antes de `django.setup()`
        # y un import de modelos a nivel de modulo reventaria el arranque (`AppRegistryNotReady`).
        from apps.core.models import RolEntity

        if not RolEntity.objects.filter(role_code=valor, is_active="Y").exists():
            raise serializers.ValidationError(mensajes.ROL_NO_VALIDO)
        return valor

    def a_datos(self) -> DatosCambioRol:
        """
        Construye la entrada del caso de uso a partir del cuerpo ya validado.

        `validated_data` llega con la clave SNAKE_CASE del dominio -la que declara el campo en su
        `source=`-, asi que el mapeo es directo y no hay una segunda tabla de equivalencias que
        mantener. El campo es obligatorio, de modo que aqui no hay ausencias que resolver: si
        faltase, la validacion ya habria devuelto el 400 con su literal.

        El dataclass se importa DENTRO del metodo y no a nivel de modulo: asi el serializador no
        arrastra el servicio -ni sus dependencias de infraestructura- por el mero hecho de
        importarse, que es lo que ocurre al generar el esquema OpenAPI.

        Returns:
            DatosCambioRol: el dato del cambio, inmutable, listo para el servicio de roles.
        """

        from apps.usuarios.roles.servicio import DatosCambioRol

        return DatosCambioRol(role_code=self.validated_data["role_code"])


class UserRoleDetailSerializer(serializers.Serializer):
    """
    Resultado del cambio de rol (`UserRoleDetail`, respuesta 200 de EP-011).

    Proyecta el dataclass `RolCambiado`. Todos los campos son de SOLO lectura: este recurso REPORTA
    lo que acaba de ocurrir y no admite que el cliente lo escriba.

    ESTA RESPUESTA NO CONTIENE NINGUN MATERIAL DE CREDENCIAL, Y NO PUEDE CONTENERLO. No es que
    `password_hash`, `password_salt` y `password_algorithm` se hayan omitido con cuidado: el
    dataclass que se proyecta no los lleva y `slots=True` cierra la clase a atributos nuevos, de
    modo que no existe atributo del que un `source=` pudiera tirar. La garantia de REQ-063 y
    REQ-076 es ESTRUCTURAL y se cumple POR CONSTRUCCION; publicar uno exigiria aniadirlo primero a
    la proyeccion del dominio, lo que quedaria a la vista en el diff.

    `roleName` admite nulo porque la etiqueta del catalogo puede no resolverse para una fila
    huerfana: `null` es el dato correcto, una cadena vacia seria mentira. `previousRoleCode`, en
    cambio, NO admite nulo aqui: por este endpoint solo se pasa cuando habia un rol vigente que
    cambiar, y el caso sin rol previo es el alta, que no responde con este esquema.

    `roleChangedAt` y `roleChangedBy` los sella el servidor a partir del reloj y del contexto de
    sesion (REQ-048, REQ-064), nunca el cliente. `refreshedSessions` publica cuantas sesiones VIVAS
    del usuario quedan MARCADAS para recargar su rol y sus capacidades en su siguiente peticion
    (`sesion_usuario.permissions_refreshed_at`, ARC-112), para que el administrador sepa que el
    efecto ya se ha propagado y no tenga que deducirlo. EL CAMBIO DE ROL NO CIERRA NI INVALIDA LA
    SESION DEL USUARIO AFECTADO, y por eso AC-PERM-05 de REQ-005 exige que su siguiente peticion
    responda 200 o 403 SEGUN LOS PERMISOS DEL ROL NUEVO, nunca 401. Un 0 no es un fallo: significa
    que el usuario no tenia ninguna sesion abierta.
    """

    userId = serializers.IntegerField(source="user_id", read_only=True)
    fullName = serializers.CharField(source="full_name", read_only=True)
    roleCode = serializers.CharField(source="role_code", read_only=True)
    roleName = serializers.CharField(source="role_name", read_only=True, allow_null=True)
    previousRoleCode = serializers.CharField(source="previous_role_code", read_only=True)
    roleChangedAt = serializers.DateTimeField(source="role_changed_at", read_only=True)
    roleChangedBy = serializers.IntegerField(source="role_changed_by", read_only=True)
    refreshedSessions = serializers.IntegerField(source="sesiones_refrescadas", read_only=True)


class RoleHistoryQuerySerializer(serializers.Serializer):
    """
    Query string del historico de rol (EP-012, REQ-008).

    TODOS LOS FILTROS SON OPCIONALES. Una peticion sin parametros es valida y devuelve la primera
    pagina del historico completo del usuario: ausente significa «no filtres por esto», no «filtra
    por nada». Acotar por defecto el rango de fechas esconderia movimientos antiguos sin que nadie
    lo hubiera pedido.

    `page` y `pageSize` tienen `default`, de modo que `validated_data` siempre trae paginacion y el
    caso de uso no tiene que decidir que hacer con su ausencia. Los limites (`min_value=1`,
    `max_value=TAMANIO_PAGINA_MAXIMO`) los hace cumplir DRF con un error de CAMPO -que dice
    exactamente cual se paso de rango- y el manejador unico lo traduce al 400 uniforme.

    LA COHERENCIA DEL RANGO SE COMPRUEBA EN `validate` Y NO EN UN CAMPO. Es una validacion entre
    DOS campos -`dateFrom` contra `dateTo`- y ningun `validate_<campo>` ve mas que el suyo: solo el
    `validate` de objeto tiene las dos fechas delante a la vez (REQ-008 validacion 2).
    """

    dateFrom = serializers.DateField(
        source="desde",
        required=False,
        error_messages={"invalid": mensajes.FILTRO_NO_VALIDO},
    )
    dateTo = serializers.DateField(
        source="hasta",
        required=False,
        error_messages={"invalid": mensajes.FILTRO_NO_VALIDO},
    )
    page = serializers.IntegerField(
        source="pagina",
        required=False,
        min_value=1,
        default=1,
        error_messages={"min_value": mensajes.FILTRO_NO_VALIDO, "invalid": mensajes.FILTRO_NO_VALIDO},
    )
    pageSize = serializers.IntegerField(
        source="tamanio_pagina",
        required=False,
        min_value=1,
        max_value=TAMANIO_PAGINA_MAXIMO,
        default=TAMANIO_PAGINA_POR_DEFECTO,
        error_messages={
            "min_value": mensajes.FILTRO_NO_VALIDO,
            "max_value": mensajes.FILTRO_NO_VALIDO,
            "invalid": mensajes.FILTRO_NO_VALIDO,
        },
    )

    def validate(self, datos: dict) -> dict:
        """
        Comprueba que el rango de fechas del filtro es coherente (REQ-008 validacion 2).

        Un `dateFrom` posterior a `dateTo` describe un intervalo vacio: la consulta se ejecutaria
        sin error y devolveria cero movimientos, un resultado indistinguible de «este usuario no
        tiene historico». Quien consulta merece saber que el rango estaba cruzado y no quedarse
        pensando que no hay nada que ver. Por eso se rechaza ANTES de tocar la base.

        La comprobacion solo se hace cuando llegan LAS DOS fechas: con una sola no hay rango que
        cruzar y el filtro es una cota abierta perfectamente valida.

        El error se emite con la clave `dateFrom` -el nombre del CONTRATO, no el del dominio-
        porque es la que el cliente ha enviado y la que puede corregir en su formulario.

        Args:
            datos: datos ya validados campo a campo, con las claves SNAKE_CASE del `source=`.

        Returns:
            dict: los mismos datos, sin retocar: este metodo decide, no normaliza.

        Raises:
            serializers.ValidationError: si `desde` es posterior a `hasta`.
        """

        desde = datos.get("desde")
        hasta = datos.get("hasta")
        if desde is not None and hasta is not None and desde > hasta:
            raise serializers.ValidationError({"dateFrom": mensajes.RANGO_FECHAS_NO_VALIDO})
        return datos

    def a_criterios(self) -> CriteriosHistoricoRol:
        """
        Construye los criterios del caso de uso a partir de la query string ya validada.

        `validated_data` llega con las claves SNAKE_CASE del dominio -las que declara cada campo en
        su `source=`-, asi que el mapeo es directo y no hay segunda tabla de equivalencias que
        mantener. Las fechas ausentes quedan en `None`, que para `CriteriosHistoricoRol` significa
        «no acotes por ese extremo»; la paginacion nunca falta porque sus campos declaran `default`.

        El dataclass se importa DENTRO del metodo por la misma razon que en
        `RoleChangeRequestSerializer.a_datos`: que importar este modulo no arrastre el servicio.

        Returns:
            CriteriosHistoricoRol: los criterios inmutables que recorren servicio y repositorio sin
            que ninguna capa los retoque.
        """

        from apps.usuarios.roles.servicio import CriteriosHistoricoRol

        datos = self.validated_data
        return CriteriosHistoricoRol(
            desde=datos.get("desde"),
            hasta=datos.get("hasta"),
            pagina=datos.get("pagina", 1),
            tamanio_pagina=datos.get("tamanio_pagina", TAMANIO_PAGINA_POR_DEFECTO),
        )


class RoleHistoryEntrySerializer(serializers.Serializer):
    """
    Un movimiento del historico de rol de un usuario (EP-012, REQ-008).

    Proyecta el dataclass `EntradaHistoricoRol`. Todos los campos son de SOLO lectura, y aqui eso
    es mas que un detalle de implementacion: el historico es INMUTABLE (REQ-008), de modo que la
    API no ofrece ni la apariencia de poder reescribir un movimiento ya ocurrido.

    `previousRoleCode` admite nulo y es nulo EXACTAMENTE en la entrada de alta, el primer
    movimiento de la persona, cuando no habia rol anterior del que venir (REQ-008 RN-02). Publicar
    ahi una cadena vacia o repetir el rol nuevo seria inventar un estado previo que nunca existio.

    `changedByName` admite nulo porque la etiqueta del actor puede no resolverse -cuentas semilla o
    movimientos escritos por un proceso-; el identificador `changedByUserId`, en cambio, siempre
    esta, que es lo que sostiene la traza.

    `validTo` admite nulo en el movimiento VIGENTE: el intervalo de validez sigue abierto y no hay
    fecha de cierre que publicar. Es la marca que distingue el rol actual de los ya superados.
    """

    historyId = serializers.IntegerField(source="history_id", read_only=True)
    userId = serializers.IntegerField(source="user_id", read_only=True)
    previousRoleCode = serializers.CharField(source="previous_role_code", read_only=True, allow_null=True)
    newRoleCode = serializers.CharField(source="new_role_code", read_only=True)
    changedByUserId = serializers.IntegerField(source="changed_by_user_id", read_only=True)
    changedByName = serializers.CharField(source="changed_by_display_name", read_only=True, allow_null=True)
    changedAt = serializers.DateTimeField(source="changed_at", read_only=True)
    validFrom = serializers.DateTimeField(source="valid_from", read_only=True)
    validTo = serializers.DateTimeField(source="valid_to", read_only=True, allow_null=True)


class RoleHistoryPageSerializer(serializers.Serializer):
    """
    Pagina del historico de rol (`RoleHistoryPage`, respuesta 200 de EP-012).

    Proyecta el dataclass `PaginaHistoricoRol`: los movimientos mas el contexto de paginacion que
    el cliente necesita para pintar el paginador.

    EL ESQUEMA `RoleHistoryPage` LO FIJA ESTA CLASE. En el `openapi.yaml` figura como germen
    (`x-mind-placeholder: true`, sin propiedades) y el contrato NO se edita: el modelo real es
    este, de modo que lo que documenta drf-spectacular y lo que devuelve el endpoint son
    necesariamente lo mismo.

    `totalCount` es el numero de movimientos que cumplen los criterios en la CONSULTA FILTRADA
    COMPLETA, no los de esta pagina; `items` trae como mucho `pageSize`.

    CERO MOVIMIENTOS DEVUELVE 200, NUNCA UN ERROR. Un usuario sin historico -o una pagina pedida
    por encima del total- no es un fallo sino un RESULTADO: la respuesta es correcta, `items` viene
    vacio y `emptyMessage` trae el literal informativo que explica por que no hay nada que mostrar.
    Con movimientos, `emptyMessage` es `null`: el campo existe siempre en el cuerpo para que el
    cliente no tenga que distinguir entre «ausente» y «nulo».
    """

    items = RoleHistoryEntrySerializer(many=True, read_only=True)
    page = serializers.IntegerField(read_only=True)
    pageSize = serializers.IntegerField(source="page_size", read_only=True)
    totalCount = serializers.IntegerField(source="total_count", read_only=True)
    totalPages = serializers.IntegerField(source="total_pages", read_only=True)
    emptyMessage = serializers.CharField(source="empty_message", read_only=True, allow_null=True)
