"""
Excepciones de dominio del bloqueo temporal de cuenta (REQ-055, REQ-072, REQ-075).

Heredan de `apps.core.errores.ErrorDominio` -directamente o a traves de
`apps.core_security.errores.CuentaBloqueadaError`- y publican los dos datos que la capa HTTP
necesita para responder sin reconstruir nada: `codigo` (identificador estable) y
`http_status`. El manejador unico de excepciones (`apps.core_security.manejadores`) ya
traduce cualquier `ErrorDominio` que exponga ese par, de modo que las vistas de login y de
desbloqueo administrativo pueden dejarlas propagar sin repetir la traduccion.

DOS SUPERFICIES, DOS CRITERIOS DE INFORMACION
---------------------------------------------
El 423 de autenticacion (REQ-072) lo recibe un anonimo: solo cuenta que hay un bloqueo
vigente y hasta cuando, porque es el unico estado que el propio solicitante ya ha provocado;
nada en el revela si la cuenta existe o si la contrasenia era correcta (RN-05). Los errores
del desbloqueo administrativo (REQ-075) los recibe un actor ya autenticado y autorizado, y
ahi si se distingue «no existe» (404) de «no esta bloqueada» (409) de «esta inactiva» (409),
porque es lo que necesita para decidir que hacer y no filtra nada hacia fuera.

SECRETOS (REQ-063, REQ-076)
---------------------------
Ninguna excepcion de este modulo acepta, guarda ni serializa la contrasenia intentada, su
`password_hash` ni fragmento alguno. El unico dato que transporta `CuentaBloqueadaTemporalmente`
es el instante de desbloqueo, que es estado de la cuenta y no material de credencial.
"""

from __future__ import annotations

from datetime import datetime

from apps.core.errores import ErrorDominio
from apps.core_security.errores import CuentaBloqueadaError
from apps.identidad.bloqueo import mensajes


class CuentaBloqueadaTemporalmenteError(CuentaBloqueadaError):
    """
    Hay un bloqueo VIGENTE sobre la cuenta y el intento no concede acceso (REQ-072, AC-PWD-09).

    Especializa `apps.core_security.errores.CuentaBloqueadaError` para poder decir HASTA CUANDO
    dura el bloqueo: AC-PWD-09 exige que el 423 indique la hora de desbloqueo, y componer ese
    texto en la vista obligaria a cada llamador a formatear `locked_until` por su cuenta. Se da
    la hora absoluta en `HH:MM` y no los minutos restantes porque un contador relativo deja de
    ser cierto en cuanto el usuario tarda en leerlo.

    NO ANADE UN ORACULO NUEVO. Hereda -sin redefinirlos- el `codigo` «AUTH_ACCOUNT_LOCKED» y el
    `http_status` 423 de la clase base: hacia fuera es EXACTAMENTE la misma respuesta que el 423
    generico, con el mismo codigo estable y el mismo estado, y lo unico que cambia es que el
    texto incluye la hora de expiracion. Un cliente que ya reacciona al 423 generico no necesita
    distinguir esta subclase, y un atacante no obtiene de ella ninguna senial que no tuviera ya:
    el bloqueo es un estado que el mismo ha provocado.

    `bloqueada_hasta` se conserva como atributo para la traza tecnica y para los consumidores que
    quieran exponerlo estructurado; el instante es SIEMPRE un `datetime` naive en UTC, sellado por
    el servidor con `apps.core.contexto.utc_now()` y nunca tomado del cliente.
    """

    def __init__(self, bloqueada_hasta: datetime) -> None:
        self.bloqueada_hasta: datetime = bloqueada_hasta
        super().__init__(mensajes.CUENTA_BLOQUEADA_HASTA.format(hora=bloqueada_hasta.strftime("%H:%M")))


class CuentaNoBloqueadaError(ErrorDominio):
    """
    Se ha pedido desbloquear una cuenta que no tiene bloqueo vigente (REQ-075, AC-RST-07).

    Responde 409 y no 200: tratar la peticion como exito silencioso le haria creer al
    administrador que ha resuelto un bloqueo que nunca existio, y ocultaria el caso real en el
    que el bloqueo ya habia vencido solo -por el paso del tiempo, REQ-055 RN-04- antes de que
    actuase. El conflicto es informacion util para quien ya esta autenticado y autorizado.
    """

    codigo = "USR_ACCOUNT_NOT_LOCKED"
    http_status = 409

    def __init__(self, mensaje: str = mensajes.CUENTA_NO_BLOQUEADA) -> None:
        super().__init__(mensaje)


class UsuarioNoEncontradoError(ErrorDominio):
    """
    El identificador recibido no corresponde a ningun usuario (REQ-075, escenario de error).

    Responde 404. A diferencia del flujo de autenticacion, aqui SI se distingue del resto de
    errores: el solicitante es un administrador autenticado cuyo alcance ya ha validado la capa
    de permisos, de modo que confirmarle que el identificador no existe no permite enumerar
    cuentas a nadie que no pudiera listarlas de todas formas.
    """

    codigo = "USR_NOT_FOUND"
    http_status = 404

    def __init__(self, mensaje: str = mensajes.USUARIO_NO_ENCONTRADO) -> None:
        super().__init__(mensaje)


class CuentaInactivaError(ErrorDominio):
    """
    La cuenta existe pero esta inactiva, y una cuenta inactiva no admite desbloqueo (REQ-075).

    Responde 409 por la misma razon que `CuentaNoBloqueadaError`: la peticion es coherente en su
    forma pero incompatible con el estado actual del recurso. Desbloquear una baja logica
    (REQ-047) no devolveria el acceso -el usuario seguiria sin poder entrar-, asi que dar un 200
    seria mentir sobre el efecto de la operacion. Reactivar la cuenta es otra operacion distinta,
    con su propio permiso y su propia traza de auditoria.
    """

    codigo = "USR_ACCOUNT_INACTIVE"
    http_status = 409

    def __init__(self, mensaje: str = mensajes.CUENTA_INACTIVA) -> None:
        super().__init__(mensaje)
