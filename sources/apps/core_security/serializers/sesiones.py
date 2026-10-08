"""
Serializadores del inicio de sesion (EP-001).

CONVENIO DE NOMBRES. El JSON del contrato va en camelCase y el modelo Python en snake_case:
los campos de salida se declaran con el nombre camelCase del contrato y apuntan al atributo
real de la fila con `source=`. Asi el contrato manda en la frontera HTTP sin contaminar los
nombres del dominio.

SECRETOS (REQ-063). `username` y `password` son `write_only`: la contrasenia entra, se
comprueba y no vuelve a salir por ninguna respuesta ni se refleja en el eco de la peticion.
La salida (`SessionDetailSerializer`) es de SOLO lectura y no publica `password_hash`,
`password_salt` ni ningun otro material criptografico del usuario.
"""

from rest_framework import serializers

from apps.core.models.transaccional import SesionUsuarioEntity


# Indicador booleano de Oracle: 'Y' es verdadero, cualquier otro valor es falso.
INDICADOR_VERDADERO = "Y"


class LoginRequestSerializer(serializers.Serializer):
    """
    Cuerpo de la peticion de inicio de sesion (`LoginRequest`).

    La credencial es PROPIA de la aplicacion (no hay SSO): `username` admite tanto el nombre
    de usuario como el correo corporativo, y el servicio de sesiones resuelve cual es.
    """

    username = serializers.CharField(max_length=150, trim_whitespace=True, write_only=True)
    password = serializers.CharField(
        max_length=128,
        trim_whitespace=False,
        write_only=True,
        style={"input_type": "password"},
    )


class SessionDetailSerializer(serializers.Serializer):
    """
    Sesion emitida que devuelve EP-001 (`SessionDetail`).

    `sessionId` es la credencial OPACA de la sesion: el cliente la envia despues en la
    cabecera `Authorization: Bearer <sessionId>`. `roleCode` es el rol VIGENTE congelado en
    el instante de la emision, no el rol recalculado al leer.

    `fullName` y `mustChangePassword` no son columnas de `sesion_usuario`: se leen del usuario
    asociado a la sesion, por eso se resuelven con metodos y no con `source`.
    """

    sessionId = serializers.CharField(source="session_id", read_only=True)
    userId = serializers.IntegerField(source="user_id", read_only=True)
    roleCode = serializers.CharField(source="role_code_id", read_only=True)
    issuedAt = serializers.DateTimeField(source="issued_at", read_only=True)
    expiresAt = serializers.DateTimeField(source="expires_at", read_only=True)
    lastActivityAt = serializers.DateTimeField(source="last_activity_at", read_only=True)
    fullName = serializers.SerializerMethodField(method_name="obtener_full_name")
    mustChangePassword = serializers.SerializerMethodField(method_name="obtener_must_change_password")

    def obtener_full_name(self, sesion: SesionUsuarioEntity) -> str:
        """Nombre completo del titular de la sesion, leido del censo de usuarios."""

        return sesion.user.full_name

    def obtener_must_change_password(self, sesion: SesionUsuarioEntity) -> bool:
        """Traduce el indicador 'Y'/'N' de la columna `usuario.must_change_password` a booleano."""

        return sesion.user.must_change_password == INDICADOR_VERDADERO
