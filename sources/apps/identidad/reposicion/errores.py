"""
Excepciones de dominio del restablecimiento administrativo de credencial (REQ-073, REQ-074).

Heredan de `apps.core.errores.ErrorDominio` y publican los dos unicos datos que la capa HTTP
necesita para responder sin reconstruir nada: `codigo` (identificador estable, contrato hacia
fuera) y `http_status`. El manejador unico de excepciones (`apps.core_security.manejadores`) ya
traduce cualquier `ErrorDominio` que exponga ese par, de modo que la vista del restablecimiento
puede dejarlas propagar: aqui NO se escribe traduccion HTTP, ni se construyen respuestas, ni se
conoce DRF.

POR QUE SE REEXPORTAN DOS ERRORES EN VEZ DE DECLARARLOS
-------------------------------------------------------
`UsuarioNoEncontradoError` y `CuentaInactivaError` ya existen en `apps.identidad.bloqueo.errores`
y su dueno semantico es ESE modulo: describen el estado del usuario objetivo de una operacion
administrativa, no un hecho propio del restablecimiento. Declarar aqui un segundo tipo con el
mismo nombre partiria el `except` en dos: un llamador que capturase el de bloqueo dejaria pasar
el de reposicion -y al reves- aunque ambos signifiquen exactamente lo mismo y respondan el mismo
404/409 con el mismo codigo estable. Se reexportan para que este paquete tenga un unico punto de
importacion sin duplicar la jerarquia.

SECRETOS (REQ-063, REQ-076)
---------------------------
Ninguna excepcion de este modulo acepta, guarda ni serializa la credencial temporal generada, su
`password_hash` ni fragmento alguno de ellos. `EntregaCredencialFallidaError` transporta como
mucho un codigo de error tecnico de la entrega, que describe el fallo del transporte y nunca el
material de la credencial.
"""

from __future__ import annotations

from apps.core.errores import ErrorDominio

# Reexportacion deliberada: el dueno semantico de estos dos errores es el modulo de bloqueo, que
# los declara para el desbloqueo administrativo (REQ-075). Un segundo tipo con el mismo nombre
# partiria el `except` en dos y haria que el mismo estado del usuario se capturase -o se escapase-
# segun el modulo del que viniese la instancia.
from apps.identidad.bloqueo.errores import CuentaInactivaError, UsuarioNoEncontradoError
from apps.identidad.reposicion import mensajes


class AutorrestablecimientoNoPermitidoError(ErrorDominio):
    """
    El administrador ha pedido restablecer SU PROPIA credencial (REQ-073, validacion 3).

    Responde 409: la peticion es correcta en su forma pero incompatible con el flujo al que se
    dirige. El restablecimiento administrativo existe para devolver el acceso a OTRO usuario que
    lo ha perdido, y por eso no pide la contrasenia actual; permitir que el actor se lo aplique a
    si mismo convertiria una sesion ya abierta -o robada- en un cambio de credencial sin presentar
    el secreto anterior. El camino propio es EP-005 (cambio de contrasenia propio), que si lo
    exige, y eso es justamente lo que dice el literal.
    """

    codigo = "USR_SELF_RESET_NOT_ALLOWED"
    http_status = 409

    def __init__(self, mensaje: str = mensajes.AUTORRESTABLECIMIENTO_NO_PERMITIDO) -> None:
        super().__init__(mensaje)


class CredencialNoGenerableError(ErrorDominio):
    """
    El generador no ha conseguido una credencial temporal que cumpla la politica (REQ-073 regla 4).

    Responde 500 y no 422: no hay nada que el administrador pueda corregir en su peticion. Es un
    fallo del servicio -longitud pedida por debajo del minimo del requisito, o agotamiento de los
    intentos de generacion- y el 4xx le haria buscar un error propio que no existe.

    El mensaje es deliberadamente opaco (`mensajes.CREDENCIAL_NO_GENERABLE`): no dice cuantos
    intentos se consumieron ni que regla de politica se incumplio, porque esos datos acotarian el
    espacio de busqueda del secreto. Y, por supuesto, la credencial intentada NO viaja en la
    excepcion (REQ-063, REQ-076).
    """

    codigo = "USR_CREDENTIAL_NOT_GENERABLE"
    http_status = 500

    def __init__(self, mensaje: str = mensajes.CREDENCIAL_NO_GENERABLE) -> None:
        super().__init__(mensaje)


class EntregaCredencialFallidaError(ErrorDominio):
    """
    El correo con el acceso temporal no se ha podido entregar (REQ-073 regla 7, AC-RST-03).

    Responde 502: el fallo no esta en la peticion del administrador sino en un sistema de tercero
    -el servidor de correo-, y distinguirlo de un 500 generico le dice que la accion es
    REINTENTABLE tal cual, sin cambiar nada de lo que envio.

    LA CREDENCIAL ANTERIOR SIGUE SIENDO LA VIGENTE
    ----------------------------------------------
    Cuando se lanza esta excepcion, el restablecimiento NO se confirma: la credencial que tenia el
    usuario antes de la operacion continua siendo la valida. Es la garantia de AC-RST-03 y de la
    regla 7 de REQ-073, y la razon de ser del 502 frente a un 200 con aviso: dar por bueno el
    cambio cuando el usuario nunca ha recibido la credencial nueva lo dejaria sin acceso y sin
    forma de recuperarlo. Quien la captura debe deshacer -o no confirmar- la escritura de la
    credencial nueva.

    `mensaje` es OBLIGATORIO y no tiene valor por defecto a proposito: el literal visible del 502
    lo publica el modulo de avisos (`apps.avisos.credenciales.entrega.MENSAJE_502_POR_TIPO`), que
    es quien sabe por que tipo de aviso se ha fallado. Redactarlo aqui crearia una segunda version
    del mismo texto, condenada a divergir de la primera.

    `codigo_error` guarda el codigo tecnico que devolvio la entrega, solo para la traza: describe
    el fallo del transporte y nunca el contenido del correo ni la credencial que llevaba.
    """

    codigo = "AVI_DELIVERY_FAILED"
    http_status = 502

    def __init__(self, mensaje: str, *, codigo_error: str | None = None) -> None:
        self.codigo_error: str | None = codigo_error
        super().__init__(mensaje)


__all__ = [
    "AutorrestablecimientoNoPermitidoError",
    "CredencialNoGenerableError",
    "CuentaInactivaError",
    "EntregaCredencialFallidaError",
    "UsuarioNoEncontradoError",
]
