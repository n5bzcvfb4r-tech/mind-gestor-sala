"""
Servicio de bloqueo temporal de cuenta: aplica la politica sobre la fila de `usuario`.

QUE RESUELVE (REQ-055, REQ-072, REQ-075)
----------------------------------------
Es la pieza que une tres cosas que viven separadas a proposito: la politica PURA
(`apps.identidad.bloqueo.politica`), que decide COMO evoluciona el estado; el servicio unico
de custodia (`apps.identidad.credenciales`), que es el unico que verifica una contrasenia; y
la fila de `usuario`, donde ese estado se persiste en tres columnas fisicas. Aqui no se vuelve
a implementar ninguna de las dos primeras: solo se orquestan y se escribe el resultado.

PUNTO UNICO DEL CONTADOR DE INTENTOS
------------------------------------
`verificar_contrasenia` es la UNICA puerta por la que pasa una verificacion de contrasenia que
deba contar intentos fallidos: el login de EP-001 y, cuando exista, la comprobacion de
`current_password` del cambio de contrasenia de EP-005. Teniendo una sola puerta, la regla de
los cinco intentos no puede divergir entre superficies, y no hay ningun camino por el que un
atacante pueda probar contrasenias sin que el contador avance.

EL CONTADOR ES POR CUENTA, NUNCA POR IP NI POR NAVEGADOR (REQ-055 RN-02, REQ-072 RN-04)
----------------------------------------------------------------------------------------
La clave del contador es `usuario.user_id` y nada mas. Este servicio NO recibe la peticion, NO
lee `REMOTE_ADDR`, NO lee cabeceras y NO toca cookies: no tiene forma de contar por origen
aunque alguien quisiera. Contar por IP dejaria pasar un ataque distribuido y, al reves,
bloquearia a toda una oficina tras una NAT por los fallos de una sola persona.

INDISTINGUIBILIDAD TEMPORAL (AC-PWD-11, AC-AUT-02)
---------------------------------------------------
La verificacion del hash se hace SIEMPRE, exista o no el usuario, antes de cualquier
bifurcacion. Cuando no hay usuario se delega igualmente en la custodia con `password_hash` a
`None` y esta verifica contra su hash senuelo, de mismo algoritmo y mismo coste. Un `return`
temprano para el caso "usuario inexistente" responderia en una fraccion del tiempo y delataria
que la cuenta no existe: esa diferencia de latencia media debe quedar por debajo de 50 ms
sobre 100 intentos.

EL 423 SE EVALUA DESPUES DE LA CREDENCIAL (REQ-053)
-----------------------------------------------------
`exigir_acceso` se invoca SOLO cuando la contrasenia ya resulto correcta. Si se consultara
antes, el 423 seria un oraculo: cualquiera podria bloquear una cuenta a fuerza de fallos y
usar despues el codigo de estado para confirmar que existe, sin conocer el secreto.

FRONTERA TRANSACCIONAL: NO ESTA AQUI, Y ES DELIBERADO
-------------------------------------------------------
Este servicio NO abre `transaction.atomic()`. La frontera la pone el caso de uso -la vista-,
que es quien sabe que otras escrituras forman parte de la misma unidad de trabajo: el login
actualiza ademas `last_login_at` y crea la sesion, y el desbloqueo administrativo puede
acompanarse de su propio historico. Si cada servicio abriera su transaccion, el commit
parcial de uno dejaria al siguiente trabajando sobre un estado ya confirmado que la vista no
podria deshacer.

LAS ESCRITURAS VAN CON `update_fields` Y SOLO SI ALGO CAMBIO
--------------------------------------------------------------
`_aplicar` devuelve la lista de columnas que cambiaron DE VERDAD y `_guardar` no emite ninguna
consulta si esa lista esta vacia. Un login correcto de una cuenta que no tenia fallos no debe
costar un UPDATE: es el caso mas frecuente del sistema y escribir en el solo produciria
contencion sobre la fila y ruido en la traza del DDL.

SECRETOS (REQ-063, REQ-076)
---------------------------
La contrasenia en claro entra por parametro, se entrega a la custodia y no se guarda en ningun
atributo, no se registra en ninguna traza y no aparece en ningun mensaje. `password_hash` solo
se LEE para pasarlo al verificador; este servicio no lo escribe nunca.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from apps.core.contexto import ContextoSesion, contexto_de_sesion, obtener_contexto, utc_now
from apps.identidad.bloqueo.auditoria import (
    EVENTO_CUENTA_BLOQUEADA,
    EVENTO_CUENTA_DESBLOQUEADA,
    EVENTO_LOGIN_FALLIDO,
    OUTCOME_DENEGADO_401,
    OUTCOME_OK,
    registrar_evento_de_bloqueo,
)
from apps.identidad.bloqueo.errores import (
    CuentaBloqueadaTemporalmenteError,
    CuentaInactivaError,
    CuentaNoBloqueadaError,
)
from apps.identidad.bloqueo.politica import EstadoBloqueo, PoliticaBloqueo
from apps.identidad.credenciales import ServicioCustodiaCredenciales


if TYPE_CHECKING:  # pragma: no cover - solo tipado: evita importar modelos antes de django.setup()
    from apps.core.models import UsuarioEntity


# Valor del enumerado cerrado `ck_usuario_status_enum` que habilita el acceso. Se replica como
# literal local -igual que hace `apps.identidad.autenticacion.servicio`- en lugar de importarlo
# de otro servicio, para no crear una dependencia circular entre paquetes de identidad.
ESTADO_USUARIO_ACTIVO = "ACTIVO"

# Nombres FISICOS de las columnas de `usuario` (esquema T.5) que este servicio escribe. Son los
# unicos tres que toca: nada de `status`, nada de `password_hash`, nada de sesiones.
CAMPO_INTENTOS_FALLIDOS = "failed_password_attempts"
CAMPO_BLOQUEADA_HASTA = "locked_until"
CAMPO_ULTIMO_FALLO = "last_failed_attempt_at"

# Valor de `operation` del desbloqueo administrativo en la traza (REQ-075).
OPERACION_DESBLOQUEO = "USER_UNLOCK"


__all__ = [
    "CAMPO_BLOQUEADA_HASTA",
    "CAMPO_INTENTOS_FALLIDOS",
    "CAMPO_ULTIMO_FALLO",
    "ESTADO_USUARIO_ACTIVO",
    "OPERACION_DESBLOQUEO",
    "ServicioBloqueoCuenta",
]


class ServicioBloqueoCuenta:
    """
    Aplicador del bloqueo temporal por intentos fallidos sobre la fila de `usuario`.

    Tres operaciones publicas y ninguna mas: `exigir_acceso` (puerta del 423),
    `verificar_contrasenia` (punto unico de verificacion con contador) y `desbloquear`
    (atajo administrativo de REQ-075).

    Sus dos colaboradores -la custodia de credenciales y la politica de bloqueo- se reciben por
    constructor con un valor por defecto construido PEREZOSAMENTE, igual que hace
    `ServicioAutenticacion`: el consumidor habitual escribe `ServicioBloqueoCuenta()` sin
    cablear nada, y una prueba puede inyectar dobles -una politica con umbral 2, una custodia
    que no gaste Argon2id- sin tocar `settings` ni el reloj.
    """

    def __init__(self, custodia: ServicioCustodiaCredenciales | None = None, politica: PoliticaBloqueo | None = None) -> None:
        self._custodia = custodia if custodia is not None else ServicioCustodiaCredenciales()
        self._politica = politica if politica is not None else PoliticaBloqueo()

    # --- Operaciones publicas -------------------------------------------------

    def exigir_acceso(self, usuario: UsuarioEntity, ahora: datetime) -> None:
        """
        Deniega con 423 si hay un bloqueo VIGENTE sobre la cuenta (AC-PWD-09, AC-AUT-04).

        Lanza `CuentaBloqueadaTemporalmenteError` con `locked_until`, porque AC-PWD-09 exige
        que la respuesta indique la HORA DE DESBLOQUEO y no un simple "cuenta bloqueada": el
        usuario legitimo necesita saber cuando podra volver a entrar sin tener que llamar al
        administrador.

        SE INVOCA SOLO CON LA CREDENCIAL YA VERIFICADA (REQ-053). El orden importa: si se
        consultara el bloqueo antes de comprobar la contrasenia, el 423 revelaria a cualquier
        anonimo que la cuenta existe -le bastaria provocar el bloqueo con cinco fallos- y se
        convertiria en el oraculo de enumeracion que REQ-053 prohibe. Quien no conoce el
        secreto recibe siempre el 401 generico.

        El instante lo pone quien llama -con `utc_now()`, hora del servidor- para que toda la
        operacion razone sobre la MISMA linea temporal que el resto de decisiones.
        """

        if self._politica.esta_bloqueada(self._estado_de(usuario), ahora):
            raise CuentaBloqueadaTemporalmenteError(usuario.locked_until)

    def verificar_contrasenia(self, usuario: UsuarioEntity | None, password: str, *, username_attempted: str | None = None) -> bool:
        """
        Verifica la contrasenia llevando el contador de intentos por cuenta (REQ-055, AC-PWD-09/10/11).

        Devuelve `True` si la contrasenia es correcta y `False` en cualquier otro caso. NO lanza
        el 423: comprobar el bloqueo es responsabilidad de `exigir_acceso`, que el llamador
        invoca DESPUES de obtener `True` aqui (REQ-053).

        PASOS, EN ESTE ORDEN EXACTO:

        1. Se sella `ahora` con la hora del servidor.
        2. Se verifica SIEMPRE un hash contra el servicio unico de custodia -el real si hay
           usuario, el senuelo si no-. No hay ningun atajo ni `return` antes de esta linea, y
           no puede haberlo: saltarsela es exactamente lo que delata por TIEMPO que la cuenta no
           existe (AC-PWD-11, diferencia de latencia media < 50 ms sobre 100 intentos).
        3. Usuario inexistente: se registra `LOGIN_FAILED` con el identificador tecleado -que es
           la evidencia de una posible enumeracion- y se devuelve `False`. No hay fila que
           actualizar, asi que no se escribe nada en `usuario`.
        4. Acierto: la politica cierra la racha y el contador vuelve a 0, tambien si el bloqueo
           acababa de vencer (AC-PWD-10). Si nada cambio no se emite ningun UPDATE.
        5. Fallo: la politica suma el intento y, al alcanzar el umbral, fija `locked_until`. Se
           registra `LOGIN_FAILED` y, SOLO si la cuenta ha pasado de no bloqueada a bloqueada en
           esta misma llamada, ademas `ACCOUNT_LOCKED` (el evento `UserAccountLocked` de
           REQ-055). La comparacion es antes/despues y no "el estado final esta bloqueado",
           para no reemitir el evento en cada intento posterior durante los quince minutos.

        El contador es POR CUENTA: la clave es `usuario.user_id` y este metodo no recibe ni
        consulta la peticion, su IP ni sus cabeceras (REQ-055 RN-02, REQ-072 RN-04).

        SECRETOS: `password` se entrega a la custodia y no se guarda, no se registra y no viaja
        a la traza; `username_attempted` se audita SOLO, nunca junto al secreto probado.
        """

        ahora = utc_now()

        # SIEMPRE se verifica un hash, exista o no el usuario. Nada puede colarse por delante.
        correcta = self._custodia.verificar(password, usuario.password_hash if usuario is not None else None)

        if usuario is None:
            registrar_evento_de_bloqueo(
                event_type=EVENTO_LOGIN_FALLIDO,
                outcome=OUTCOME_DENEGADO_401,
                username_attempted=username_attempted,
            )
            return False

        estado = self._estado_de(usuario)

        if correcta:
            nuevo = self._politica.registrar_acierto(estado, ahora)
            self._guardar(usuario, self._aplicar(usuario, nuevo))
            return True

        bloqueada_antes = self._politica.esta_bloqueada(estado, ahora)
        nuevo = self._politica.registrar_fallo(estado, ahora)
        self._guardar(usuario, self._aplicar(usuario, nuevo))

        registrar_evento_de_bloqueo(
            event_type=EVENTO_LOGIN_FALLIDO,
            outcome=OUTCOME_DENEGADO_401,
            usuario=usuario,
            username_attempted=username_attempted,
        )

        # El bloqueo se audita UNA sola vez: en la transicion, no en cada intento que rebota
        # contra un bloqueo ya vigente.
        if not bloqueada_antes and self._politica.esta_bloqueada(nuevo, ahora):
            registrar_evento_de_bloqueo(
                event_type=EVENTO_CUENTA_BLOQUEADA,
                outcome=OUTCOME_DENEGADO_401,
                usuario=usuario,
                username_attempted=username_attempted,
            )

        return False

    def desbloquear(self, usuario: UsuarioEntity, *, actor: ContextoSesion) -> EstadoBloqueo:
        """
        Levanta el bloqueo por decision administrativa (REQ-075, AC-RST-06, AC-RST-07).

        El `actor` es el administrador que ejecuta la operacion: la atribucion de la escritura
        debe ser SUYA y no del titular de la cuenta, porque la traza tiene que responder a
        "quien devolvio el acceso". Por eso el llamador publica su contexto de sesion y
        `_guardar` lo respeta en vez de envolver la escritura con el del propio usuario.

        Secuencia y errores:

        1. Cuenta INACTIVA -> `CuentaInactivaError` (409, escenario de error 4 de REQ-075).
           Desbloquear una baja logica no devolveria el acceso, asi que responder 200 mentiria
           sobre el efecto de la operacion; reactivar es otra operacion, con su propio permiso.
        2. Cuenta SIN bloqueo vigente -> `CuentaNoBloqueadaError` (409) y NO SE ESCRIBE NADA, ni
           en `usuario` ni en la traza (AC-RST-07: «no se altera ningun dato de la cuenta»).
           Incluye el caso en que el bloqueo ya habia vencido solo por el paso del tiempo.
        3. Con bloqueo vigente: `locked_until` a `None` y `failed_password_attempts` a 0. Se
           CONSERVA `last_failed_attempt_at` (AC-RST-06): el desbloqueo devuelve el acceso, no
           borra la evidencia de que hubo una racha de intentos fallidos.
        4. Se registra `ACCOUNT_UNLOCKED` con `outcome` correcto y `operation="USER_UNLOCK"`.

        OMISIONES DELIBERADAS Y EXIGIDAS POR REQUISITO. Que no se "arreglen" despues:

        * NO se toca `status` ni ningun indicador de actividad (REQ-055 RN-03, AC-AUT-05): el
          bloqueo temporal y la baja logica son estados INDEPENDIENTES, y una cuenta inactiva
          desbloqueada seguiria sin poder entrar.
        * NO se toca `password_hash` ni ninguna columna de credencial (REQ-075 RN-03): el
          desbloqueo NO es un restablecimiento de contrasenia; la del usuario sigue siendo
          valida y exigirle una nueva convertiria una operacion de soporte en una de seguridad.
        * NO se revocan sesiones: `sesion_usuario` no se consulta ni se escribe, porque AC-RST-06
          dice literalmente que «sus sesiones no son revocadas». Quien tuviera una sesion activa
          la conserva.

        Devuelve el `EstadoBloqueo` resultante, que es el que la vista puede serializar sin
        volver a leer la fila.
        """

        ahora = utc_now()

        if usuario.status != ESTADO_USUARIO_ACTIVO:
            raise CuentaInactivaError()

        estado = self._estado_de(usuario)
        if not self._politica.esta_bloqueada(estado, ahora):
            # Ni UPDATE ni traza: la peticion no ha cambiado nada y decirlo es justo el 409.
            raise CuentaNoBloqueadaError()

        nuevo = self._politica.desbloquear(estado)
        self._guardar(usuario, self._aplicar(usuario, nuevo))

        registrar_evento_de_bloqueo(
            event_type=EVENTO_CUENTA_DESBLOQUEADA,
            outcome=OUTCOME_OK,
            usuario=usuario,
            operation=OPERACION_DESBLOQUEO,
            session_id=actor.session_id,
        )

        return nuevo

    # --- Colaboracion con la fila de `usuario` --------------------------------

    def _estado_de(self, usuario: UsuarioEntity) -> EstadoBloqueo:
        """
        Proyecta las tres columnas fisicas de bloqueo en el `EstadoBloqueo` que entiende la politica.

        `failed_password_attempts` se lee como 0 cuando llega `None`: la columna tiene valor por
        defecto 0, pero una fila heredada o un objeto recien construido en memoria pueden traerlo
        nulo, y la politica razona con enteros. Tratarlo como 0 es lo seguro: ningun intento
        previo registrado equivale a ninguna racha.
        """

        return EstadoBloqueo(
            intentos_fallidos=usuario.failed_password_attempts or 0,
            bloqueada_hasta=usuario.locked_until,
            ultimo_fallo_en=usuario.last_failed_attempt_at,
        )

    def _aplicar(self, usuario: UsuarioEntity, estado: EstadoBloqueo) -> list[str]:
        """
        Vuelca el estado sobre la entidad y devuelve las columnas FISICAS que cambiaron de verdad.

        Se comparan los valores ANTES de asignar para poder construir un `update_fields` minimo.
        La lista vacia -nada cambio- es el caso mas frecuente del sistema: un login correcto de
        una cuenta sin fallos previos. Evitar ahi el UPDATE ahorra contencion sobre la fila de
        `usuario`, que es de las mas leidas del esquema.

        Se usan los nombres FISICOS del DDL de T.5 sin traducir, para que lo que se escribe aqui
        sea literalmente lo que aparece en el plan de ejecucion y en la traza de la base.
        """

        campos: list[str] = []

        # La comparacion es contra el valor CRUDO y no contra el normalizado: una fila que trae
        # `None` debe quedar escrita a 0, porque la columna del DDL es NOT NULL.
        if usuario.failed_password_attempts != estado.intentos_fallidos:
            usuario.failed_password_attempts = estado.intentos_fallidos
            campos.append(CAMPO_INTENTOS_FALLIDOS)

        if usuario.locked_until != estado.bloqueada_hasta:
            usuario.locked_until = estado.bloqueada_hasta
            campos.append(CAMPO_BLOQUEADA_HASTA)

        if usuario.last_failed_attempt_at != estado.ultimo_fallo_en:
            usuario.last_failed_attempt_at = estado.ultimo_fallo_en
            campos.append(CAMPO_ULTIMO_FALLO)

        return campos

    def _guardar(self, usuario: UsuarioEntity, campos: list[str]) -> None:
        """
        Persiste SOLO las columnas indicadas, con la atribucion que corresponda a cada escenario.

        Si `campos` esta vacio no se hace absolutamente nada: ni `save()`, ni consulta, ni
        apertura de conexion.

        LA ATRIBUCION DEPENDE DE QUIEN ACTUA. `UsuarioEntity` usa `AtribucionMixin` y su `save()`
        EXIGE un contexto de sesion publicado:

        * Si YA hay contexto -caso del desbloqueo administrativo, donde la vista publico el del
          administrador- se guarda sin envolver nada, para que la fila quede atribuida al actor
          real de la operacion.
        * Si NO lo hay -caso del login, donde todavia no existe sesion- se envuelve la escritura
          en el contexto del PROPIO usuario, que en ese momento es su unico actor posible,
          exactamente igual que hace `ServicioAutenticacion._sellar_inicio_de_sesion`.

        No se abre transaccion: la frontera la pone el caso de uso (ver docstring del modulo).
        """

        if not campos:
            return

        if obtener_contexto() is not None:
            usuario.save(update_fields=campos)
            return

        contexto = ContextoSesion(
            user_id=usuario.user_id,
            role_code=usuario.role_code_id,
            display_name=usuario.full_name,
        )
        with contexto_de_sesion(contexto):
            usuario.save(update_fields=campos)
