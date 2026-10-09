"""
Serializadores del alta de usuario (EP-007 `POST /users`, REQ-037, REQ-038, REQ-045).

CONVENIO DE NOMBRES. El JSON del contrato va en camelCase y el modelo Python en snake_case: los
campos se declaran con el nombre camelCase del contrato y apuntan al atributo real del dataclass
con `source=`, igual que en EP-017 y EP-018. Asi el contrato manda en la frontera HTTP sin
contaminar los nombres del dominio.

LOS ESQUEMAS `UserCreateRequest` Y `UserDetail` LOS FIJA ESTE MODULO. En el `openapi.yaml` los dos
estan todavia como germen (`x-mind-placeholder: true`, sin una sola propiedad): el modelo real es
el que se declara aqui, de modo que la documentacion que genera drf-spectacular y la respuesta
real no puedan divergir. EL CONTRATO NO SE EDITA, igual que se hizo con EP-017, EP-018 y EP-019:
la ruta, el verbo y el 201 se transcriben al pie de la letra y lo unico que este modulo aporta es
el cuerpo que el germen deja sin describir.

VALIDAR ES DE AQUI; NORMALIZAR, DE `normalizacion`
---------------------------------------------------
Las cuatro validaciones de REQ-037 -obligatoriedad, formato del correo, longitudes y pertenencia
del rol al catalogo- viven en este modulo y no en el servicio, porque es la unica capa que puede
decir QUE CAMPO esta mal y devolver el literal exacto de `mensajes` para el 400. El servicio, por
su parte, no revalida nada: se ocupa de la unicidad (409) y del orden de la operacion.

El orden es siempre NORMALIZAR primero y VALIDAR despues: un nombre que solo tenia espacios se
rechaza por vacio y un correo no se rechaza por una longitud que iba a desaparecer con el recorte.

NADA DE CREDENCIAL SALE POR AQUI (REQ-063, REQ-076, REQ-079). Ver el docstring de
`UserDetailSerializer`: la garantia no es una omision cuidadosa, es estructural.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.usuarios.alta import mensajes
from apps.usuarios.alta.normalizacion import (
    LONGITUD_MAXIMA_CORREO,
    LONGITUD_MAXIMA_NOMBRE,
    LONGITUD_MINIMA_NOMBRE,
    normalizar_correo,
    normalizar_nombre,
)
from apps.usuarios.alta.servicio import DatosAltaUsuario


__all__ = [
    "UserCreateRequestSerializer",
    "UserDetailSerializer",
]


class UserCreateRequestSerializer(serializers.Serializer):
    """
    Cuerpo de la peticion de alta de usuario (`UserCreateRequest`, REQ-037, PRE-03).

    TRES CAMPOS, LOS TRES OBLIGATORIOS. Son exactamente los datos de entrada que enumera REQ-037:
    nombre, correo corporativo y rol. Cada uno lleva su propio literal de obligatoriedad para que
    el formulario senale CUAL falta; un unico «faltan datos» obligaria al administrador a revisar
    los tres.

    LO QUE NO SE DECLARA, Y POR QUE
    --------------------------------
    * No hay campo de CONTRASENIA. El administrador no elige la credencial inicial: la genera el
      sistema y viaja al buzon del usuario (REQ-038 regla 1). Un campo aqui permitiria fijar un
      secreto conocido por quien da de alta.
    * No hay `createdBy` ni alias suyo NI SIQUIERA COMO CAMPO IGNORADO. El actor del alta sale
      SIEMPRE del contexto de sesion (REQ-048, REQ-064); aceptarlo del payload seria permitir que
      el cliente declare quien actua y firme la auditoria con el nombre de otro. Un campo
      declarado acaba leyendose tarde o temprano.
    * No hay `username`. No es un dato del formulario: lo deriva el caso de uso de la parte local
      del correo (ver `ServicioAltaUsuario._username_para`).
    * No hay `status`. La cuenta nace ACTIVA por regla (REQ-037 regla 4) y no por eleccion.

    Los limites numericos se IMPORTAN de `apps.usuarios.alta.normalizacion` en lugar de
    reescribirse: son los anchos reales de las columnas del esquema T.5 y el numero del literal
    visible y el del contrato tienen que ser el mismo.
    """

    fullName = serializers.CharField(
        source="full_name",
        required=True,
        allow_blank=False,
        max_length=LONGITUD_MAXIMA_NOMBRE,
        trim_whitespace=True,
        error_messages={
            "required": mensajes.NOMBRE_REQUERIDO,
            "blank": mensajes.NOMBRE_REQUERIDO,
            "null": mensajes.NOMBRE_REQUERIDO,
            "max_length": mensajes.NOMBRE_LONGITUD_NO_VALIDA,
        },
    )
    corporateEmail = serializers.EmailField(
        source="corporate_email",
        required=True,
        allow_blank=False,
        max_length=LONGITUD_MAXIMA_CORREO,
        error_messages={
            "required": mensajes.CORREO_REQUERIDO,
            "blank": mensajes.CORREO_REQUERIDO,
            "null": mensajes.CORREO_REQUERIDO,
            "invalid": mensajes.CORREO_NO_VALIDO,
            "max_length": mensajes.CORREO_DEMASIADO_LARGO,
        },
    )
    roleCode = serializers.CharField(
        source="role_code",
        required=True,
        allow_blank=False,
        trim_whitespace=True,
        error_messages={
            "required": mensajes.ROL_REQUERIDO,
            "blank": mensajes.ROL_REQUERIDO,
            "null": mensajes.ROL_REQUERIDO,
        },
    )

    def validate_fullName(self, valor: str) -> str:
        """
        Comprueba la longitud del nombre YA NORMALIZADO (REQ-037: entre 2 y 120 caracteres).

        La medida se toma sobre el valor normalizado y no sobre el texto en bruto, y eso cambia el
        resultado en los dos extremos: «A  B» -con doble espacio- mide 4 caracteres en bruto y 3
        normalizado, y un nombre pegado desde una hoja de calculo con espacios de sobra podria
        superar los 120 en bruto aunque el valor que de verdad se va a persistir quepa de sobra.
        Validar la forma que NO se guarda seria rechazar o aceptar por un texto que nunca existio.

        Se devuelve el valor ya normalizado, de modo que `validated_data` lleve exactamente lo que
        se midio. El servicio vuelve a normalizar por su cuenta -`normalizar_nombre` es idempotente
        y aplicarla dos veces da el mismo resultado- porque no puede depender de que su llamante
        sea siempre este serializador.

        Args:
            valor: nombre tal y como llego, ya con los extremos recortados por `trim_whitespace`.

        Returns:
            str: el nombre normalizado, que es el que se persistira.

        Raises:
            serializers.ValidationError: si el nombre normalizado se sale de la horquilla. DRF lo
                convierte en un error de CAMPO y el manejador unico, en el 400 uniforme.
        """

        normalizado = normalizar_nombre(valor)
        if not (LONGITUD_MINIMA_NOMBRE <= len(normalizado) <= LONGITUD_MAXIMA_NOMBRE):
            raise serializers.ValidationError(mensajes.NOMBRE_LONGITUD_NO_VALIDA)
        return normalizado

    def validate_corporateEmail(self, valor: str) -> str:
        """
        Lleva el correo a su FORMA CANONICA y comprueba que cabe en la columna (REQ-045, RN-01).

        El valor que devuelve este metodo es el que viaja al caso de uso, el que se compara contra
        el censo y el que se persiste: son el mismo, que es la invariante de PBT-001. Si aqui se
        dejase pasar el texto en bruto, dos altas que solo difieren en mayusculas chocarian la
        primera vez y dejarian de chocar en cuanto se reescribiese el original.

        La longitud se mide DESPUES de normalizar, igual que en el nombre y por el mismo motivo: el
        exceso de un correo pegado con espacios desaparece con la normalizacion, y rechazarlo seria
        rechazar un dato que si cabe en `usuario.corporate_email`. El `max_length` declarado en el
        campo sigue actuando como primer filtro sobre el texto en bruto; esta comprobacion es la
        que manda sobre el valor real.

        Args:
            valor: correo tal y como llego, ya validado como correo por `EmailField`.

        Returns:
            str: el correo en minusculas y sin espacios.

        Raises:
            serializers.ValidationError: si el correo normalizado supera `LONGITUD_MAXIMA_CORREO`.
        """

        normalizado = normalizar_correo(valor)
        if len(normalizado) > LONGITUD_MAXIMA_CORREO:
            raise serializers.ValidationError(mensajes.CORREO_DEMASIADO_LARGO)
        return normalizado

    def validate_roleCode(self, valor: str) -> str:
        """
        Comprueba que el rol elegido existe y esta ACTIVO en `cat_rol` (REQ-037 validacion 3).

        Se valida contra la base y no contra una lista en codigo: los catalogos del proyecto se
        LEEN, no se codifican. Las filas dadas de baja logica (`is_active` distinto de `'Y'`,
        REQ-047) se excluyen porque un rol retirado no se puede asignar en un alta, aunque sus
        filas historicas sigan existiendo. Por eso el literal del rechazo
        (`mensajes.ROL_NO_VALIDO`) no enumera ningun codigo.

        LA CONSULTA AL CATALOGO VIVE AQUI Y NO EN LOS `choices` DEL CAMPO. Es una diferencia con
        consecuencias, la misma que documenta `CredentialStatusQuerySerializer.validate_roleCode`
        (EP-019): los `choices` se resuelven al INSTANCIAR el serializador, y drf-spectacular
        instancia esta clase para generar el esquema de EP-007. Cargarlos ahi ataria la generacion
        del contrato -y cualquier import que la dispare- a tener una conexion viva contra Oracle,
        de modo que `GET /docs/schema/` reventaria con un error de base de datos en cualquier
        entorno sin BBDD levantada. Validando dentro de `validate_roleCode`, la consulta ocurre
        SOLO cuando hay una peticion real que validar, que es justo cuando la base esta
        disponible. El catalogo sigue siendo la unica fuente de verdad.

        Args:
            valor: codigo de rol recibido en la peticion de alta.

        Returns:
            str: el mismo codigo, ya comprobado contra el catalogo vigente.

        Raises:
            serializers.ValidationError: si el codigo no pertenece al catalogo vigente.
        """

        # El modelo se importa AQUI DENTRO: este modulo puede resolverse antes de `django.setup()`
        # y un import de modelos a nivel de modulo reventaria el arranque (`AppRegistryNotReady`).
        from apps.core.models import RolEntity

        if not RolEntity.objects.filter(role_code=valor, is_active="Y").exists():
            raise serializers.ValidationError(mensajes.ROL_NO_VALIDO)
        return valor

    def a_datos(self) -> DatosAltaUsuario:
        """
        Construye la entrada del caso de uso a partir del cuerpo ya validado.

        `validated_data` llega con las claves SNAKE_CASE del dominio -las que declara cada campo en
        su `source=`-, asi que el mapeo es directo y no hay una segunda tabla de equivalencias que
        mantener. Los tres campos son obligatorios, de modo que aqui no hay ausencias que resolver:
        si faltase alguno, la validacion ya habria devuelto el 400 con su literal.

        Returns:
            DatosAltaUsuario: los tres datos del alta, inmutables, listos para `ServicioAltaUsuario.crear`.
        """

        datos = self.validated_data
        return DatosAltaUsuario(
            full_name=datos["full_name"],
            corporate_email=datos["corporate_email"],
            role_code=datos["role_code"],
        )


class UserDetailSerializer(serializers.Serializer):
    """
    Detalle del usuario recien creado (`UserDetail`, respuesta 201 de EP-007).

    Proyecta el dataclass `UsuarioCreado`. Los once campos son de SOLO lectura: este recurso
    REPORTA lo que acaba de ocurrir y no admite que el cliente lo escriba.

    ESTA RESPUESTA NO CONTIENE LA CREDENCIAL INICIAL, NI SU HASH, NI SU SAL, NI EL ALGORITMO, Y NO
    PUEDE CONTENERLOS. No es que se hayan omitido con cuidado: el dataclass que se proyecta no los
    lleva (`slots=True` cierra la clase a atributos nuevos), de modo que no existe atributo del que
    un `source=` pudiera tirar. La garantia de AC-USR-01 y de REQ-063 es ESTRUCTURAL: publicar
    material de credencial exigiria aniadirlo primero al dominio, lo que quedaria a la vista en el
    diff. La contrasenia inicial existe en un unico sitio: el correo que recibe el usuario en su
    buzon corporativo.

    `mustChangePassword` sale ya como booleano, traducido desde el CHAR(1) de Oracle en la
    proyeccion del dominio: el convenio fisico del esquema no se publica por la API.

    `createdBy` admite nulo porque la columna lo admite -es nula solo en la cuenta semilla, que no
    se crea por este camino-; publicar un cero en su lugar seria inventar un actor. `createdAt` lo
    sella el servidor (REQ-048) y nunca el cliente.

    `passwordExpiresAt` admite nulo por la misma razon que la columna: aunque el alta siempre fija
    caducidad (REQ-038: 48 h), el contrato no puede afirmar que jamas sera nula para un usuario.

    `notificationId` identifica la solicitud de aviso, para la traza y el soporte posterior:
    identifica el ENVIO, nunca su contenido.
    """

    userId = serializers.IntegerField(source="user_id", read_only=True)
    fullName = serializers.CharField(source="full_name", read_only=True)
    corporateEmail = serializers.CharField(source="corporate_email", read_only=True)
    roleCode = serializers.CharField(source="role_code", read_only=True)
    status = serializers.CharField(read_only=True)
    mustChangePassword = serializers.BooleanField(source="must_change_password", read_only=True)
    createdAt = serializers.DateTimeField(source="created_at", read_only=True)
    createdBy = serializers.IntegerField(source="created_by", read_only=True, allow_null=True)
    credentialIssuedAt = serializers.DateTimeField(source="credential_issued_at", read_only=True)
    passwordExpiresAt = serializers.DateTimeField(source="password_expires_at", read_only=True, allow_null=True)
    notificationId = serializers.CharField(source="notification_id", read_only=True)
