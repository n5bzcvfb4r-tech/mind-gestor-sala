"""
Serializadores del desbloqueo administrativo de cuenta (EP-018, `POST /api/users/{userId}/unlock`).

CONVENIO DE NOMBRES. El JSON del contrato va en camelCase y el modelo Python en snake_case:
los campos de salida se declaran con el nombre camelCase del contrato y apuntan al atributo
real del dataclass con `source=`, igual que en `SessionDetailSerializer` y en
`SessionContextSerializer`. Asi el contrato manda en la frontera HTTP sin contaminar los
nombres del dominio.

LOS NOMBRES FISICOS DEL ESQUEMA T.5 NO SE TRADUCEN. `failed_password_attempts`,
`locked_until` y `last_failed_attempt_at` siguen llamandose asi en el modelo, en la politica y
en toda consulta; lo unico que cambia de nombre es la REPRESENTACION HTTP
(`failedPasswordAttempts`, `lockedUntil`, `lastFailedAttemptAt`), y solo aqui.

LOS ESQUEMAS `AccountUnlockRequest` Y `AccountLockStatus` LOS FIJA ESTE MODULO. En el
`openapi.yaml` los dos estan todavia como germen (`x-mind-placeholder: true`, sin
propiedades): el modelo real es el que se declara aqui, de modo que la documentacion que
genera drf-spectacular y la respuesta real no puedan divergir. El contrato NO se edita.

LO QUE NO SE PUBLICA (REQ-054, REQ-063, REQ-079). Esta respuesta describe el ESTADO DE
BLOQUEO de una cuenta y nada mas. Esta PROHIBIDO exponer por ella `password_hash`,
`password_salt`, el algoritmo de derivacion, `corporate_email` o cualquier otro dato de
credencial o de contacto del titular: el desbloqueo no es una consulta de ficha de usuario, y
convertir su respuesta en una no haria mas que repartir datos personales por una superficie
que no los necesita.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rest_framework import serializers


__all__ = [
    "AccountLockStatusSerializer",
    "AccountUnlockRequestSerializer",
    "ResultadoDesbloqueo",
]


@dataclass(frozen=True, slots=True)
class ResultadoDesbloqueo:
    """
    Resultado del desbloqueo administrativo que proyecta EP-018 (`AccountLockStatus`).

    Es inmutable a proposito: la vista lo compone UNA sola vez con el `EstadoBloqueo` que
    devuelve `ServicioBloqueoCuenta.desbloquear` y con la identidad del administrador que
    publico el guardia de sesion, y nadie lo reescribe despues.

    `unlocked_by` es el `user_id` del ADMINISTRADOR que ejecuta la operacion (REQ-075), tomado
    del contexto de sesion; nunca del cuerpo de la peticion. `ultimo_fallo_en` SOBREVIVE al
    desbloqueo (AC-RST-06): el desbloqueo devuelve el acceso, no borra la evidencia de la racha
    de intentos fallidos.
    """

    user_id: int
    locked: bool
    bloqueada_hasta: datetime | None
    intentos_fallidos: int
    ultimo_fallo_en: datetime | None
    unlocked_at: datetime
    unlocked_by: int


class AccountUnlockRequestSerializer(serializers.Serializer):
    """
    Cuerpo de la peticion de desbloqueo administrativo (`AccountUnlockRequest`): SIN CAMPOS.

    No es un olvido. REQ-075 define como datos de entrada de la operacion exactamente dos, y
    NINGUNO de los dos viaja en el cuerpo:

    * `target_user_id`: la cuenta a desbloquear. Viaja en el PATH (`/users/{userId}/unlock`),
      porque es el recurso sobre el que se actua, no un parametro de la accion.
    * `unlocked_by_user_id`: el administrador que la desbloquea. Se toma SIEMPRE del contexto
      de sesion. Aceptarlo del payload seria permitir que el cliente declare quien actua, es
      decir, SUPLANTAR AL ACTOR y firmar la traza de auditoria con el nombre de otro: el actor
      no se autodeclara (REQ-064).

    El serializador existe igualmente porque el contrato declara `requestBody: required` y
    porque validar la entrada -aunque sea vacia- rechaza la basura: un cuerpo que no sea un
    objeto JSON no llega al caso de uso. Un objeto vacio `{}` es el cuerpo esperado y valido.

    No se declara `unlockedByUserId` ni ningun alias suyo NI SIQUIERA como campo ignorado: un
    campo declarado acaba leyendose tarde o temprano.
    """


class AccountLockStatusSerializer(serializers.Serializer):
    """
    Estado de bloqueo de la cuenta tras el desbloqueo (`AccountLockStatus`, respuesta 201 de EP-018).

    Proyecta el dataclass `ResultadoDesbloqueo`. Los siete campos son de SOLO lectura: este
    recurso REPORTA el estado resultante de la operacion y no admite que el cliente lo escriba.

    Tras un desbloqueo correcto, y por AC-RST-06, la respuesta dice siempre lo mismo:
    `locked` a `false`, `lockedUntil` a `null` y `failedPasswordAttempts` a `0`. En cambio
    `lastFailedAttemptAt` SE CONSERVA con el valor que ya tenia: el desbloqueo levanta la
    restriccion de acceso, no limpia la traza del ultimo intento fallido.

    NADA DE LA CREDENCIAL (REQ-054, REQ-063, REQ-079). Ni `password_hash`, ni `password_salt`,
    ni el algoritmo, ni `corporate_email`: por esta frontera solo sale estado de bloqueo y la
    atribucion de quien lo levanto.
    """

    userId = serializers.IntegerField(source="user_id", read_only=True)
    locked = serializers.BooleanField(read_only=True)
    lockedUntil = serializers.DateTimeField(source="bloqueada_hasta", read_only=True, allow_null=True)
    failedPasswordAttempts = serializers.IntegerField(source="intentos_fallidos", read_only=True)
    lastFailedAttemptAt = serializers.DateTimeField(source="ultimo_fallo_en", read_only=True, allow_null=True)
    unlockedAt = serializers.DateTimeField(source="unlocked_at", read_only=True)
    unlockedBy = serializers.IntegerField(source="unlocked_by", read_only=True)
