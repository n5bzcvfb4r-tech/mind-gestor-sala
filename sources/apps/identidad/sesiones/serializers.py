"""
Serializadores del contexto de la sesion vigente (EP-003).

CONVENIO DE NOMBRES. El JSON del contrato va en camelCase y el modelo Python en snake_case:
los campos de salida se declaran con el nombre camelCase del contrato y apuntan al atributo
real del dataclass con `source=`. Asi el contrato manda en la frontera HTTP sin contaminar
los nombres del dominio, igual que en `SessionDetailSerializer` y en
`EffectivePermissionsSerializer`.

EL ESQUEMA `SessionContext` LO FIJA ESTE SERIALIZADOR. En el `openapi.yaml` el esquema esta
todavia como germen (`x-mind-placeholder`), sin propiedades: el modelo real de respuesta es el
que se declara aqui, de modo que la documentacion generada por drf-spectacular y la respuesta
real no puedan divergir.

LO QUE NO SE PUBLICA (REQ-063). La respuesta se limita a la identidad del usuario de la
sesion, su rol VIGENTE y las marcas temporales de la propia sesion. NO se publica
`password_hash`, NI `password_salt`, NI el algoritmo de derivacion, NI ningun otro material
criptografico: nada de la credencial sale por esta frontera.
"""

from dataclasses import dataclass
from datetime import datetime

from rest_framework import serializers


@dataclass(frozen=True, slots=True)
class ContextoSesionVigente:
    """
    Contexto del usuario autenticado que proyecta EP-003 (`SessionContext`).

    Es inmutable a proposito: la vista lo compone una sola vez a partir de la fila de sesion
    releida de la base y del contexto que publico el guardia, y nadie lo reescribe despues.

    `role_code` es el rol VIGENTE del usuario, no el congelado en `sesion_usuario` al emitir
    la sesion (REQ-011, REQ-018). `must_change_password` ya llega como `bool`: la traduccion
    del indicador Oracle 'Y'/'N' se hace al construir este dataclass, no en el serializador.
    """

    session_id: str
    user_id: int
    full_name: str
    corporate_email: str
    role_code: str
    issued_at: datetime
    expires_at: datetime
    last_activity_at: datetime
    must_change_password: bool


class SessionContextSerializer(serializers.Serializer):
    """
    Contexto de sesion que devuelve EP-003 (`SessionContext`).

    Proyecta el dataclass `ContextoSesionVigente`. Los nueve campos son de SOLO lectura y los
    nueve viajan SIEMPRE en la respuesta: este recurso describe el estado de la sesion en
    curso, y un cliente que reciba este cuerpo no tiene que distinguir entre un valor ausente
    y un valor vacio.

    `mustChangePassword` indica si el usuario debe cambiar la contrasenia antes de operar; es
    un INDICADOR de estado, nunca la credencial ni ninguna parte de ella (REQ-063).
    """

    sessionId = serializers.CharField(source="session_id", read_only=True)
    userId = serializers.IntegerField(source="user_id", read_only=True)
    fullName = serializers.CharField(source="full_name", read_only=True)
    corporateEmail = serializers.CharField(source="corporate_email", read_only=True)
    roleCode = serializers.CharField(source="role_code", read_only=True)
    issuedAt = serializers.DateTimeField(source="issued_at", read_only=True)
    expiresAt = serializers.DateTimeField(source="expires_at", read_only=True)
    lastActivityAt = serializers.DateTimeField(source="last_activity_at", read_only=True)
    mustChangePassword = serializers.BooleanField(source="must_change_password", read_only=True)
