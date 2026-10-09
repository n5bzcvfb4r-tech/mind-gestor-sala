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

* Cuando el usuario NO existe se verifica igualmente la contrasenia recibida: se pasa un hash
  AUSENTE (`None`) al servicio de custodia, que lo sustituye por su HASH SENUELO -mismo
  algoritmo y mismos parametros de coste que los hashes reales, Argon2id- y consume asi el
  mismo trabajo criptografico que en el camino "el usuario existe".
* Cuando el usuario existe pero esta INACTIVO se verifica contra su hash real, exactamente
  igual que si estuviera activo; el estado solo participa en la decision final.
* La decision se ACUMULA en una variable booleana y la excepcion se lanza UNA SOLA VEZ, al
  final, despues de haber hecho siempre el mismo trabajo.

LA VERIFICACION LA HACE EL SERVICIO UNICO DE CUSTODIA (REQ-054, REQ-069)
------------------------------------------------------------------------
Este modulo ya NO calcula ni compara hashes por su cuenta: delega en
`apps.identidad.credenciales.ServicioCustodiaCredenciales.verificar`, que es el UNICO punto del
servicio que hashea una contrasenia y el unico que la compara en tiempo constante (REQ-054:
«verificacion en login -> recalculo y comparacion en tiempo constante»). Mantener aqui una
segunda implementacion de la misma comprobacion era justo el defecto que REQ-069 prohibe:
bastaria con endurecer una de las dos -o cambiar de hasher en una sola- para que la misma
credencial se tratase de forma distinta segun la puerta por la que entrase.

EL SENUELO TAMBIEN LO APORTA ESE SERVICIO, y por eso ya no vive en este fichero. `verificar`
acepta un `password_hash` ausente o vacio y, en ese caso, verifica igualmente contra su propio
hash senuelo, calculado una vez por proceso y cacheado. La propiedad de indistinguibilidad
temporal se conserva intacta -el trabajo Argon2id se hace SIEMPRE, exista o no el usuario-,
pero deja de depender de que dos modulos mantengan por separado senuelos de coste equivalente.

LA LLAMADA A ESE VERIFICADOR PASA AHORA POR `ServicioBloqueoCuenta` (ARC-013), al que se le
inyecta LA MISMA instancia de custodia. Asi sigue habiendo un unico verificador de credenciales
-y un unico senuelo- vivo en el proceso, y de paso ninguna verificacion de contrasenia del
login escapa al contador de intentos fallidos.

CONTADOR DE INTENTOS Y BLOQUEO: LOS GOBIERNA `ServicioBloqueoCuenta` (REQ-055, REQ-072)
----------------------------------------------------------------------------------------
Este servicio ya NO lleva por su cuenta el contador de fallos ni evalua `locked_until`: delega
ambas cosas en `apps.identidad.bloqueo.ServicioBloqueoCuenta`, que es el punto UNICO donde una
verificacion de contrasenia suma un intento fallido, fija el bloqueo al alcanzar el umbral
(AC-PWD-09), lo reinicia con un acierto o cuando el bloqueo vence (AC-PWD-10) y escribe la
auditoria correspondiente (`LOGIN_FAILED`, `ACCOUNT_LOCKED`). Tener una sola puerta es lo que
impide que la regla de los cinco intentos diverja entre superficies, y lo que evita que exista
un camino por el que probar contrasenias sin que el contador avance.

CUENTA BLOQUEADA, CASO APARTE A PROPOSITO
-----------------------------------------
REQ-053 separa el bloqueo temporal con su propio estado (423), asi que ese SI es distinguible
por requisito; la respuesta lleva ademas la HORA DE DESBLOQUEO
(`CuentaBloqueadaTemporalmenteError`, AC-PWD-09, AC-AUT-04), porque el usuario legitimo
necesita saber cuando podra volver a entrar. Para que el 423 no se convierta en un oraculo de
existencia de cuenta, `exigir_acceso` se consulta SOLO cuando el usuario existe Y su credencial
ya ha sido verificada como correcta: quien no conoce la contrasenia nunca llega a ver un 423 y
no puede usarlo para saber si la cuenta existe.

SECRETOS (REQ-063, REQ-076)
---------------------------
Ni la contrasenia en claro, ni `password_hash`, ni el correo corporativo se escriben jamas en
logs, trazas ni mensajes de error. Este modulo no registra ninguna traza por ese motivo.
"""

from datetime import datetime

from apps.core.contexto import ContextoSesion, contexto_de_sesion, utc_now
from apps.core.models import UsuarioEntity
from apps.core_security.errores import CredencialesInvalidasError
from apps.identidad.bloqueo import ServicioBloqueoCuenta
from apps.identidad.credenciales import ServicioCustodiaCredenciales

# Valor del enumerado cerrado `ck_usuario_status_enum` que habilita el acceso. Se replica como
# literal local -y no se importa de otro servicio- para no crear una dependencia circular
# cuando la capa de sesion delegue en este servicio.
ESTADO_USUARIO_ACTIVO = "ACTIVO"


class ServicioAutenticacion:
    """
    Comprobacion de credenciales de inicio de sesion contra la tabla `usuario`.

    Unica operacion publica: `autenticar`. No emite sesiones (eso es del servicio de
    sesiones) y tampoco gobierna ya el contador de intentos fallidos ni el umbral de bloqueo:
    de ambos se encarga `ServicioBloqueoCuenta` (ARC-013, REQ-055, REQ-072), en quien este
    servicio DELEGA tanto la verificacion con contador -que suma el fallo, reinicia la racha
    con el acierto y fija `locked_until` al alcanzar el umbral (AC-PWD-09, AC-PWD-10)- como la
    puerta del 423 con hora de desbloqueo (AC-AUT-04).

    La comprobacion de la credencial NO se hace aqui: la aporta
    `ServicioCustodiaCredenciales`, el servicio unico de hashing y verificacion (REQ-054,
    REQ-069). Los dos colaboradores se reciben por constructor con un valor por defecto para
    que el consumidor habitual no tenga que construirlos y una prueba pueda inyectar los suyos;
    al de bloqueo se le pasa LA MISMA instancia de custodia, para que no queden dos
    verificadores de credencial vivos en el proceso.
    """

    def __init__(self, custodia: ServicioCustodiaCredenciales | None = None, bloqueo: ServicioBloqueoCuenta | None = None) -> None:
        self._custodia = custodia if custodia is not None else ServicioCustodiaCredenciales()
        self._bloqueo = bloqueo if bloqueo is not None else ServicioBloqueoCuenta(custodia=self._custodia)

    def autenticar(self, username: str, password: str) -> UsuarioEntity:
        """
        Valida las credenciales y devuelve el usuario autenticado.

        `username` es el correo corporativo: se le aplica `trim` y la comparacion es
        INSENSIBLE A MAYUSCULAS (REQ-045, AC-AUT-01). A `password` no se le aplica ninguna
        normalizacion -ni recorte de espacios ni cambio de caja-: los espacios y las
        mayusculas forman parte de la contrasenia.

        Deniega con `CredencialesInvalidasError` (401) de forma INDISTINGUIBLE -en mensaje y
        en tiempo- ante contrasenia incorrecta, usuario inexistente o usuario inactivo, y con
        `CuentaBloqueadaTemporalmenteError` (423, con la hora de desbloqueo) si la cuenta esta
        bloqueada temporalmente.

        Cada intento, acierte o falle, pasa por `ServicioBloqueoCuenta.verificar_contrasenia`:
        ahi se lleva el contador por cuenta y se impone el bloqueo del umbral (REQ-055,
        AC-PWD-09, AC-PWD-10). Al identificador tecleado se le aplica `trim` UNA sola vez, aqui,
        y el mismo valor recortado alimenta la busqueda y la auditoria.
        """

        identificador = (username or "").strip()
        usuario = self._buscar_usuario(identificador)

        # Siempre se verifica UN hash: el real si el usuario existe y, si no, el senuelo que el
        # servicio de custodia pone cuando recibe un hash ausente. Nunca se salta este paso,
        # porque saltarlo es exactamente lo que delata el caso por tiempo. La llamada va por el
        # servicio de bloqueo para que ningun intento escape al contador; a la traza viaja solo
        # el identificador tecleado, JAMAS la contrasenia (REQ-063, REQ-076).
        contrasenia_correcta = self._bloqueo.verificar_contrasenia(usuario, password, username_attempted=identificador)

        # Decision ACUMULADA: los tres casos indistinguibles recorren el mismo camino y
        # confluyen en un unico `raise` al final del bloque.
        usuario_activo = usuario is not None and usuario.status == ESTADO_USUARIO_ACTIVO
        acceso_concedido = usuario is not None and usuario_activo and contrasenia_correcta
        if not acceso_concedido:
            raise CredencialesInvalidasError()

        ahora = utc_now()
        # Solo aqui, con la credencial YA verificada, el bloqueo puede distinguirse sin
        # convertir el 423 en un oraculo de existencia de cuenta (REQ-053). La vigencia la
        # decide el servicio de bloqueo, que responde con la hora de desbloqueo (AC-AUT-04).
        self._bloqueo.exigir_acceso(usuario, ahora)

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

        El reinicio AUTORITATIVO del contador ya lo hizo `ServicioBloqueoCuenta` al verificar la
        contrasenia, via la politica de bloqueo (AC-PWD-10). Aqui se reafirma a 0 en el MISMO
        `update_fields` que `last_login_at`: la operacion es idempotente -el valor ya es 0- y
        mantiene el sello del login en una sola escritura, en vez de gastar un UPDATE extra
        sobre una de las filas mas disputadas del esquema.
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
