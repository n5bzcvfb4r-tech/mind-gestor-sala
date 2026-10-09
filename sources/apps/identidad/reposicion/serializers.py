"""
Serializadores del restablecimiento administrativo de credencial (EP-017) y del listado de
estado de credencial (EP-019).

CONVENIO DE NOMBRES. El JSON del contrato va en camelCase y el modelo Python en snake_case: los
campos se declaran con el nombre camelCase del contrato y apuntan al atributo real del dataclass
con `source=`, igual que en `AccountLockStatusSerializer` (EP-018). Asi el contrato manda en la
frontera HTTP sin contaminar los nombres del dominio.

LOS NOMBRES FISICOS DEL ESQUEMA T.5 NO SE TRADUCEN. `must_change_password`,
`password_updated_at`, `locked_until` y `last_login_at` siguen llamandose asi en el modelo, en el
repositorio y en la proyeccion; lo unico que cambia de nombre es la REPRESENTACION HTTP, y solo
aqui.

LOS ESQUEMAS `PasswordResetRequest`, `PasswordResetResult` Y `CredentialStatusPage` LOS FIJA ESTE
MODULO. En el `openapi.yaml` los tres estan todavia como germen (`x-mind-placeholder: true`, sin
propiedades): el modelo real es el que se declara aqui, de modo que la documentacion que genera
drf-spectacular y la respuesta real no puedan divergir. El contrato NO se edita, igual que se
hizo con EP-018.

NADA DE CREDENCIAL SALE POR AQUI (REQ-063, REQ-076, REQ-079). Ninguno de los serializadores de
salida de este modulo declara `password_hash`, `password_salt` ni `password_algorithm`, y los
dataclasses que proyectan tampoco los transportan: la garantia de AC-RST-01 y AC-RST-04 es
ESTRUCTURAL, no una omision que haya que recordar en cada revision.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.identidad.reposicion import mensajes
from apps.identidad.reposicion.repositorio import ESTADOS_BLOQUEO, LONGITUD_MINIMA_BUSQUEDA, CriteriosEstadoCredencial
from apps.identidad.reposicion.servicio import LONGITUD_MAXIMA_MOTIVO


__all__ = [
    "CredentialStatusItemSerializer",
    "CredentialStatusPageSerializer",
    "CredentialStatusQuerySerializer",
    "PasswordResetRequestSerializer",
    "PasswordResetResultSerializer",
]


class PasswordResetRequestSerializer(serializers.Serializer):
    """
    Cuerpo de la peticion de restablecimiento administrativo (`PasswordResetRequest`).

    UN SOLO CAMPO, Y OPCIONAL. `resetReason` es la justificacion libre del restablecimiento
    (REQ-073 regla 6). De los tres datos de entrada que enumera REQ-073, los otros DOS NO VIAJAN
    EN EL CUERPO:

    * `target_user_id`: el usuario al que se le repone la credencial. Viaja en el PATH
      (`/users/{userId}/password-reset`), porque es el recurso sobre el que se actua, no un
      parametro de la accion.
    * `reset_by_user_id`: el administrador que la ejecuta. Se toma SIEMPRE del contexto de
      sesion. Aceptarlo del payload seria permitir que el cliente declare quien actua, es decir,
      SUPLANTAR AL ACTOR y firmar la traza de auditoria con el nombre de otro: el actor no se
      autodeclara (REQ-064).

    No se declara `resetByUserId` ni ningun alias suyo NI SIQUIERA COMO CAMPO IGNORADO: un campo
    declarado acaba leyendose tarde o temprano.

    EL TOPE DEL MOTIVO LO HACE CUMPLIR ESTE SERIALIZADOR, no el servicio (REQ-073 regla 6 /
    validacion 4). Un motivo de mas de `LONGITUD_MAXIMA_MOTIVO` caracteres lo rechaza DRF con un
    error de CAMPO -que dice exactamente cual se paso de largo- y el manejador unico lo traduce
    al 400 uniforme del servicio. El limite se IMPORTA del caso de uso en vez de reescribirse:
    el numero del texto visible y el del contrato son el mismo y no pueden desalinearse.
    """

    resetReason = serializers.CharField(
        source="reset_reason",
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=LONGITUD_MAXIMA_MOTIVO,
        trim_whitespace=True,
        error_messages={"max_length": mensajes.MOTIVO_DEMASIADO_LARGO},
    )


class PasswordResetResultSerializer(serializers.Serializer):
    """
    Desenlace del restablecimiento (`PasswordResetResult`, respuesta 201 de EP-017).

    Proyecta el dataclass `ResultadoRestablecimiento`. Los siete campos son de SOLO lectura:
    este recurso REPORTA lo que acaba de ocurrir y no admite que el cliente lo escriba.

    ESTA RESPUESTA NO CONTIENE LA CONTRASENIA TEMPORAL, NI SU HASH, NI NINGUN MATERIAL DE
    CREDENCIAL, Y NO PUEDE CONTENERLOS. No es que se hayan omitido con cuidado: el dataclass que
    se proyecta no los lleva (`slots=True` cierra la clase a atributos nuevos), de modo que no
    existe atributo del que un `source=` pudiera tirar. REQ-073 regla 4 es literal -«nunca
    persistida en claro ni devuelta por la API»- y AC-RST-01 exige que EL ADMINISTRADOR NO VEA EN
    NINGUN MOMENTO la contrasenia que se ha emitido. La credencial temporal existe en un unico
    sitio: el correo que recibe el usuario destino en su buzon corporativo.

    `notificationId` es el identificador de la solicitud de aviso, para la traza y el soporte
    posterior: identifica el envio, NUNCA su contenido.

    `revokedSessions` publica cuantas sesiones vivas quedaron revocadas (AC-PWD-06). Puede ser
    `0` y no es un error: simplemente el usuario no tenia ninguna sesion abierta.
    """

    userId = serializers.IntegerField(source="user_id", read_only=True)
    mustChangePassword = serializers.BooleanField(source="must_change_password", read_only=True)
    passwordExpiresAt = serializers.DateTimeField(source="password_expires_at", read_only=True)
    resetAt = serializers.DateTimeField(source="reset_at", read_only=True)
    resetByUserId = serializers.IntegerField(source="reset_by_user_id", read_only=True)
    revokedSessions = serializers.IntegerField(source="sesiones_revocadas", read_only=True)
    notificationId = serializers.CharField(source="notification_id", read_only=True)


class CredentialStatusQuerySerializer(serializers.Serializer):
    """
    Query string del listado de estado de credencial (EP-019, REQ-074 validaciones).

    TODOS LOS FILTROS SON OPCIONALES. Una peticion sin parametros es valida y devuelve la primera
    pagina del censo sin predicados: ausente significa «no filtres por esto», y no «valor nulo».

    `search` exige `LONGITUD_MINIMA_BUSQUEDA` caracteres (REQ-074 regla 4). El escenario 1 de
    REQ-074 pide que una busqueda demasiado corta se rechace con el literal publicado, asi que el
    mensaje del error de campo se personaliza con `mensajes.BUSQUEDA_DEMASIADO_CORTA` en vez de
    dejar el generico de DRF. El repositorio, por su parte, IGNORA un texto corto en lugar de
    fallar: ahi no hay a quien responderle, y esta frontera es la unica capa que puede devolver
    un 400 con mensaje.

    `roleCode` SE VALIDA CONTRA EL CATALOGO LEIDO DE LA BASE (REQ-074 regla 1), nunca contra un
    enum escrito a mano: `cat_rol` es la fuente unica de verdad de los roles del sistema y un
    literal copiado aqui quedaria desalineado el dia que Liquibase siembre un rol nuevo.

    LA CONSULTA AL CATALOGO VIVE EN `validate_roleCode` Y NO EN LOS `choices` DEL CAMPO. Es una
    diferencia con consecuencias: los `choices` se resuelven al INSTANCIAR el serializador, y
    drf-spectacular instancia esta clase para generar el esquema de EP-019 (`parameters=[...]`).
    Cargarlos ahi ataria la generacion del contrato -y cualquier import que la dispare- a tener
    una conexion viva contra Oracle, de modo que `GET /docs/schema/` reventaria con un error de
    base de datos en cualquier entorno sin BBDD levantada. Validando en su lugar dentro de
    `validate_roleCode`, la consulta ocurre SOLO cuando hay una peticion real que validar, que es
    justo cuando la base esta disponible. El catalogo sigue siendo la unica fuente de verdad.

    `page` tiene `default=1`, de modo que `validated_data` siempre trae pagina: el caso de uso no
    tiene que decidir que hacer con su ausencia.
    """

    roleCode = serializers.CharField(source="role_code", required=False, trim_whitespace=True)
    lockStatus = serializers.ChoiceField(source="estado_bloqueo", required=False, choices=ESTADOS_BLOQUEO)
    mustChangePassword = serializers.BooleanField(source="must_change_password", required=False)
    search = serializers.CharField(
        source="texto",
        required=False,
        min_length=LONGITUD_MINIMA_BUSQUEDA,
        trim_whitespace=True,
        error_messages={"min_length": mensajes.BUSQUEDA_DEMASIADO_CORTA},
    )
    page = serializers.IntegerField(source="pagina", required=False, min_value=1, default=1)

    def validate_roleCode(self, valor: str) -> str:
        """
        Comprueba que el rol pedido existe y esta ACTIVO en `cat_rol` (REQ-074 validacion 1).

        Se valida contra la base y no contra una lista en codigo: los catalogos del proyecto se
        LEEN, no se codifican. Las filas dadas de baja logica (`is_active` distinto de `'Y'`,
        REQ-047) se excluyen porque un rol retirado no es un filtro valido, aunque sus filas
        historicas sigan existiendo.

        Se ejecuta SOLO durante la validacion de una peticion real, nunca al instanciar la clase:
        ver la nota del docstring de clase sobre la generacion del esquema.

        Raises:
            serializers.ValidationError: si el codigo no pertenece al catalogo vigente. DRF lo
                convierte en un error de campo y el manejador unico, en el 400 uniforme.
        """

        # El modelo se importa AQUI DENTRO: este modulo puede resolverse antes de `django.setup()`
        # y un import de modelos a nivel de modulo reventaria el arranque (`AppRegistryNotReady`).
        from apps.core.models import RolEntity

        if not RolEntity.objects.filter(role_code=valor, is_active="Y").exists():
            raise serializers.ValidationError(mensajes.ROL_NO_VALIDO)
        return valor

    def a_criterios(self) -> CriteriosEstadoCredencial:
        """
        Construye los criterios del caso de uso a partir de la query string ya validada.

        `validated_data` llega con las claves SNAKE_CASE del dominio -las que declara cada campo
        en su `source=`-, asi que el mapeo es directo y no hay segunda tabla de equivalencias que
        mantener. Los filtros ausentes quedan en `None`, que para `CriteriosEstadoCredencial`
        significa «no filtres por esto».

        Returns:
            CriteriosEstadoCredencial: los criterios inmutables que recorren servicio y
            repositorio sin que ninguna capa los retoque.
        """

        datos = self.validated_data
        return CriteriosEstadoCredencial(
            role_code=datos.get("role_code"),
            estado_bloqueo=datos.get("estado_bloqueo"),
            must_change_password=datos.get("must_change_password"),
            texto=datos.get("texto"),
            pagina=datos.get("pagina", 1),
        )


class CredentialStatusItemSerializer(serializers.Serializer):
    """
    Estado de credencial de UN usuario dentro del listado (REQ-074, campos de datos).

    Proyecta el dataclass `EstadoCredencialUsuario`. Son los diez campos de datos de REQ-074 y
    ninguno mas, todos de SOLO lectura: este recurso es una consulta y no admite escritura.

    NO SE DECLARA NINGUN CAMPO DE CREDENCIAL. `password_hash`, `password_salt` y
    `password_algorithm` no estan aqui Y TAMPOCO ESTAN EN EL DATACLASS QUE SE PROYECTA, de modo
    que AC-RST-04 -«ninguna respuesta del endpoint contiene `password_hash` ni ningun material de
    credencial»- se cumple POR CONSTRUCCION y no por acordarse de omitirlos en cada cambio.
    Publicar uno exigiria anadirlo primero a la proyeccion del dominio, lo que quedaria a la
    vista en el diff.

    `passwordUpdatedAt`, `lockedUntil` y `lastLoginAt` admiten nulo: una cuenta recien creada
    puede no haber cambiado nunca su contrasenia, no haber estado bloqueada nunca y no haber
    entrado todavia. `null` es el dato correcto; un cero o una fecha inventada serian mentira.

    `locked` ya viene resuelto como `bool` desde la proyeccion, comparando `locked_until` contra
    el MISMO instante que filtro la consulta: aqui no se recalcula nada, para que la respuesta no
    pueda contradecirse a si misma.
    """

    userId = serializers.IntegerField(source="user_id", read_only=True)
    fullName = serializers.CharField(source="full_name", read_only=True)
    corporateEmail = serializers.CharField(source="corporate_email", read_only=True)
    roleCode = serializers.CharField(source="role_code", read_only=True)
    status = serializers.CharField(read_only=True)
    passwordUpdatedAt = serializers.DateTimeField(source="password_updated_at", read_only=True, allow_null=True)
    mustChangePassword = serializers.BooleanField(source="must_change_password", read_only=True)
    locked = serializers.BooleanField(read_only=True)
    lockedUntil = serializers.DateTimeField(source="locked_until", read_only=True, allow_null=True)
    lastLoginAt = serializers.DateTimeField(source="last_login_at", read_only=True, allow_null=True)


class CredentialStatusPageSerializer(serializers.Serializer):
    """
    Pagina del listado de estado de credencial (`CredentialStatusPage`, respuesta 200 de EP-019).

    Proyecta el dataclass `PaginaEstadoCredencial`: las filas mas el contexto de paginacion que
    el cliente necesita para pintar el paginador.

    `totalCount` es el numero de usuarios que cumplen los criterios en la CONSULTA FILTRADA
    COMPLETA, no los de esta pagina; `items` trae como mucho `pageSize` (REQ-074 regla 2). Una
    pagina por encima del total devuelve `items` vacio con 200, que no es un error: la consulta
    era valida y simplemente no quedaban filas.
    """

    items = CredentialStatusItemSerializer(many=True, read_only=True)
    page = serializers.IntegerField(read_only=True)
    pageSize = serializers.IntegerField(source="page_size", read_only=True)
    totalCount = serializers.IntegerField(source="total_count", read_only=True)
    totalPages = serializers.IntegerField(source="total_pages", read_only=True)
