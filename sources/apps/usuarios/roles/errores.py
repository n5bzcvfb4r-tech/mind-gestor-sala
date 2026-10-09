"""
Excepciones de dominio del modulo de roles (ARC-013, REQ-005, REQ-043).

Heredan de `apps.core.errores.ErrorDominio` y publican los dos unicos datos que la capa HTTP
necesita para responder sin reconstruir nada: `codigo` (identificador estable, contrato hacia
fuera) y `http_status`. El manejador unico de excepciones (`apps.core_security.manejadores`) ya
traduce cualquier `ErrorDominio` que exponga ese par al cuerpo canonico `{code, message, details,
traceId}`, de modo que las vistas de roles pueden dejarlas propagar: aqui NO se escribe traduccion
HTTP, ni se construyen respuestas, ni se conoce DRF.

Los codigos siguen la convencion del handbook -UPPER_SNAKE con prefijo de modulo-: `ROL_` cuando
lo que falla es la asignacion de rol en si y `USR_` cuando lo que falla es el sujeto sobre el que
se pretendia operar.

AQUI NO HAY ERROR DE PERMISOS
-----------------------------
No existe una excepcion de denegacion propia del modulo. El 403 lo compone el manejador unico con
su literal uniforme, el mismo que devuelve la matriz de autorizacion, para que ambas denegaciones
sean indistinguibles (REQ-078) y no delaten la existencia del recurso (AC-ROL-06).
"""

from __future__ import annotations

from apps.core.errores import ErrorDominio
from apps.usuarios.roles import mensajes


class RolNoValidoError(ErrorDominio):
    """
    El rol indicado no sirve para operar (AC-ROL-02, AC-ROL-03 de REQ-043).

    Responde 400: el defecto esta en la peticion misma -el rol llega ausente o con un valor que no
    pertenece a `cat_rol` (AC-ROL-02), o se pide un rol inexistente en el cambio (AC-ROL-03)-, y no
    en ningun hecho del sistema. No se crea ni se modifica ninguna asignacion de rol.
    """

    codigo = "ROL_INVALID"
    http_status = 400

    def __init__(self, mensaje: str = mensajes.ROL_NO_VALIDO) -> None:
        super().__init__(mensaje)


class RolYaAsignadoError(ErrorDominio):
    """
    El usuario ya tiene un rol vigente y no admite otra alta (AC-ROL-02).

    Responde 409 y no 400: la peticion es correcta en su forma -el rol existe y es asignable- pero
    choca con un hecho ya existente, que es lo que distingue el conflicto del error de formato. El
    alta no puede dejar dos filas de rol vigente para la misma persona.
    """

    codigo = "ROL_ALREADY_ASSIGNED"
    http_status = 409

    def __init__(self, mensaje: str = mensajes.ROL_YA_ASIGNADO) -> None:
        super().__init__(mensaje)


class MismoRolError(ErrorDominio):
    """
    El rol pedido es el que el usuario ya ostenta (AC-ROL-03 de REQ-043, REQ-005 RN-01).

    Responde 409: choca con el estado vigente, no con la forma de la peticion. RN-01 exige que el
    rol nuevo sea siempre distinto del actual, porque un cambio a si mismo escribiria un movimiento
    de historico que no narra ningun cambio. El indice `ck_usuario_hist_valor_distinto` del DDL lo
    impone ademas en la base, de modo que la regla se sostiene aunque dos cambios compitan.
    """

    codigo = "ROL_SAME_ROLE"
    http_status = 409

    def __init__(self, mensaje: str = mensajes.MISMO_ROL) -> None:
        super().__init__(mensaje)


class AutorretiradaRolAdministracionError(ErrorDominio):
    """
    El administrador intenta retirarse su propio rol de administracion (REQ-043 RN-03).

    Responde 422 y no 409: la peticion es sintacticamente valida y no contradice ningun hecho ya
    escrito; lo que la prohibe es la regla de negocio que impide quedarse sin administracion por
    descuido. El rol del actor permanece inalterado.
    """

    codigo = "ROL_SELF_DEMOTION"
    http_status = 422

    def __init__(self, mensaje: str = mensajes.AUTORRETIRADA_ROL_ADMINISTRACION) -> None:
        super().__init__(mensaje)


class UsuarioNoEncontradoError(ErrorDominio):
    """
    El usuario sobre el que se queria operar no existe.

    Responde 404: no hay sujeto al que asignarle o cambiarle el rol. El mensaje es uniforme y no
    revela nada mas alla de que no se ha encontrado.
    """

    codigo = "USR_NOT_FOUND"
    http_status = 404

    def __init__(self, mensaje: str = mensajes.USUARIO_NO_ENCONTRADO) -> None:
        super().__init__(mensaje)


class UsuarioDeBajaError(ErrorDominio):
    """
    El usuario existe pero esta dado de baja (REQ-005, escenario de error 5).

    Responde 409: la peticion es correcta en su forma y el sujeto existe, pero su estado ya escrito
    impide moverle el rol. Sobre una baja no se asigna ni se cambia rol.
    """

    codigo = "USR_INACTIVE"
    http_status = 409

    def __init__(self, mensaje: str = mensajes.USUARIO_DE_BAJA) -> None:
        super().__init__(mensaje)


__all__ = [
    "AutorretiradaRolAdministracionError",
    "MismoRolError",
    "RolNoValidoError",
    "RolYaAsignadoError",
    "UsuarioDeBajaError",
    "UsuarioNoEncontradoError",
]
