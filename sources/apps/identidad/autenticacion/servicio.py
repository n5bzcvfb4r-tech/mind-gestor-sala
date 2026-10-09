"""
Servicio de autenticacion de credenciales endurecido frente a la enumeracion de cuentas.

QUE RESUELVE (REQ-053, AC-AUT-01, AC-AUT-02, AC-ACC-02)
-------------------------------------------------------
Comprobar usuario y contrasenia contra la tabla `usuario` de Oracle y devolver el usuario
autenticado. Es el sustituto ENDURECIDO de la comprobacion de credenciales previa, que
denegaba con el mismo mensaje pero con caminos de codigo de coste muy distinto.

DENEGACION UNIFORME **E INDISTINGUIBLE**
----------------------------------------
Los tres casos de fallo -contrasenia incorrecta, usuario inexistente y usuario con
`status` distinto de ACTIVO (`is_active` falso)- lanzan la MISMA
`CredencialesInvalidasError` (401, `mensajes.CREDENCIALES_INVALIDAS`). Eso ya no basta:
un mensaje identico sigue siendo un oraculo si el TIEMPO de respuesta delata el caso.

Verificar un hash Argon2id cuesta deliberadamente decenas de milisegundos; salir antes de
hacerlo (porque el usuario no existe, o porque esta inactivo) responde en una fraccion de
ese tiempo. Con unas pocas decenas de intentos, esa diferencia permite separar "esta cuenta
no existe" de "existe y la contrasenia falla", que es justo lo que AC-AUT-02 y AC-ACC-02
prohiben. El criterio de aceptacion es cuantitativo: la diferencia de latencia MEDIA entre
los tres casos debe ser INFERIOR A 50 ms sobre 100 intentos.

Por eso aqui NO hay short-circuit en los caminos indistinguibles:

* Cuando el usuario NO existe se verifica igualmente la contrasenia recibida contra un HASH
  SENUELO precalculado con el MISMO algoritmo y los MISMOS parametros de coste que los hashes
  reales (`make_password` usa el primer elemento de `PASSWORD_HASHERS`, Argon2id). Se consume
  asi el mismo trabajo criptografico que en el camino "el usuario existe".
* Cuando el usuario existe pero esta INACTIVO se verifica contra su hash real, exactamente
  igual que si estuviera activo; el estado solo participa en la decision final.
* La decision se ACUMULA en una variable booleana y la excepcion se lanza UNA SOLA VEZ, al
  final, despues de haber hecho siempre el mismo trabajo.

El hash senuelo se calcula UNA vez por proceso y se cachea (`functools.lru_cache`): hacerlo
en cada intento seria correcto en tiempo pero gastaria CPU sin motivo, y hacerlo en el import
encareceria el arranque del servicio.

CUENTA BLOQUEADA, CASO APARTE A PROPOSITO
-----------------------------------------
REQ-053 separa el bloqueo temporal con su propio estado (423, `CuentaBloqueadaError`), asi que
ese SI es distinguible por requisito. Para que el 423 no se convierta en un oraculo de
existencia de cuenta, `locked_until` se comprueba SOLO cuando el usuario existe Y su
credencial ya ha sido verificada como correcta: quien no conoce la contrasenia nunca llega a
ver un 423 y no puede usarlo para saber si la cuenta existe.

SECRETOS (REQ-063, REQ-076)
---------------------------
Ni la contrasenia en claro, ni `password_hash`, ni el correo corporativo se escriben jamas en
logs, trazas ni mensajes de error. Este modulo no registra ninguna traza por ese motivo.
"""

from datetime import datetime
from functools import lru_cache

from django.contrib.auth.hashers import check_password, make_password

from apps.core.contexto import ContextoSesion, contexto_de_sesion, utc_now
from apps.core.models import UsuarioEntity
from apps.core_security.errores import CredencialesInvalidasError, CuentaBloqueadaError

# Valor del enumerado cerrado `ck_usuario_status_enum` que habilita el acceso. Se replica como
# literal local -y no se importa de otro servicio- para no crear una dependencia circular
# cuando la capa de sesion delegue en este servicio.
ESTADO_USUARIO_ACTIVO = "ACTIVO"

# Texto interno del que se deriva el hash senuelo. NO es una credencial: no se persiste, no
# autentica a nadie y ninguna cuenta puede tenerlo, porque el hash que produce solo vive en
# memoria y jamas se compara contra una fila real. Su unica funcion es dar a `check_password`
# algo valido contra lo que trabajar cuando el usuario no existe.
_TEXTO_SENUELO = "credencial-senuelo-sin-uso-real"


@lru_cache(maxsize=1)
def _hash_senuelo() -> str:
    """
    Hash senuelo del proceso, calculado de forma PEREZOSA y una sola vez.

    Lo produce `make_password`, que usa el primer hasher de `PASSWORD_HASHERS` (Argon2id), de
    modo que verificarlo cuesta lo mismo que verificar el hash real de cualquier usuario.
    """

    return make_password(_TEXTO_SENUELO)


class ServicioAutenticacion:
    """
    Comprobacion de credenciales de inicio de sesion contra la tabla `usuario`.

    Unica operacion publica: `autenticar`. No emite sesiones (eso es del servicio de
    sesiones), no toca el contador de intentos fallidos mas alla de ponerlo a cero tras un
    inicio de sesion correcto (REQ-053 RN-04) y no escribe `locked_until`: el umbral de
    bloqueo lo gobierna el flujo de intentos fallidos, fuera de esta unidad.
    """

    def autenticar(self, username: str, password: str) -> UsuarioEntity:
        """
        Valida las credenciales y devuelve el usuario autenticado.

        `username` es el correo corporativo: se le aplica `trim` y la comparacion es
        INSENSIBLE A MAYUSCULAS (REQ-045, AC-AUT-01). A `password` no se le aplica ninguna
        normalizacion -ni recorte de espacios ni cambio de caja-: los espacios y las
        mayusculas forman parte de la contrasenia.

        Deniega con `CredencialesInvalidasError` (401) de forma INDISTINGUIBLE -en mensaje y
        en tiempo- ante contrasenia incorrecta, usuario inexistente o usuario inactivo, y con
        `CuentaBloqueadaError` (423) si la cuenta esta bloqueada temporalmente.
        """

        usuario = self._buscar_usuario(username)

        # Siempre se verifica UN hash: el real si el usuario existe, el senuelo si no. Nunca
        # se salta este paso, porque saltarlo es exactamente lo que delata el caso por tiempo.
        hash_a_verificar = usuario.password_hash if usuario is not None else _hash_senuelo()
        contrasenia_correcta = check_password(password, hash_a_verificar)

        # Decision ACUMULADA: los tres casos indistinguibles recorren el mismo camino y
        # confluyen en un unico `raise` al final del bloque.
        usuario_activo = usuario is not None and usuario.status == ESTADO_USUARIO_ACTIVO
        acceso_concedido = usuario is not None and usuario_activo and contrasenia_correcta
        if not acceso_concedido:
            raise CredencialesInvalidasError()

        ahora = utc_now()
        # Solo aqui, con la credencial YA verificada, el bloqueo puede distinguirse sin
        # convertir el 423 en un oraculo de existencia de cuenta (REQ-053).
        if usuario.locked_until is not None and usuario.locked_until > ahora:
            raise CuentaBloqueadaError()

        return self._sellar_inicio_de_sesion(usuario, ahora)

    def _buscar_usuario(self, username: str) -> UsuarioEntity | None:
        """
        Localiza al usuario por `username` exacto y, si no aparece, por correo corporativo normalizado.

        La unicidad del correo es insensible a mayusculas y espacios (REQ-045), y asi la
        impone el indice funcional `ux_usuario_email_ci` sobre `LOWER(TRIM(corporate_email))`.
        """

        identificador = (username or "").strip()
        if not identificador:
            return None

        usuario = UsuarioEntity.objects.select_related("role_code").filter(username=identificador).first()
        if usuario is not None:
            return usuario

        correo = identificador.lower()
        return UsuarioEntity.objects.select_related("role_code").filter(corporate_email__iexact=correo).first()

    def _sellar_inicio_de_sesion(self, usuario: UsuarioEntity, ahora: datetime) -> UsuarioEntity:
        """
        Sella el inicio de sesion correcto: marca temporal y contador de fallos a cero (REQ-053 RN-04).

        El usuario es su propio actor de atribucion en el inicio de sesion: todavia no hay
        contexto publicado y `AtribucionMixin.save()` lo exige, asi que la escritura va dentro
        de `contexto_de_sesion(...)`.

        El contador por cuenta es la columna fisica `failed_password_attempts` del DDL de T.5
        (`usuario`), que es el `failed_login_attempts` del enunciado funcional; se escribe el
        nombre fisico real, sin traducir.
        """

        usuario.last_login_at = ahora
        usuario.failed_password_attempts = 0
        contexto = ContextoSesion(
            user_id=usuario.user_id,
            role_code=usuario.role_code_id,
            display_name=usuario.full_name,
        )
        with contexto_de_sesion(contexto):
            usuario.save(update_fields=["last_login_at", "failed_password_attempts"])
        return usuario
