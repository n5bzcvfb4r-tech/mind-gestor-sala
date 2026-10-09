"""
Excepciones de dominio del alta de usuario (EP-007, REQ-037, REQ-038, REQ-045).

Heredan de `apps.core.errores.ErrorDominio` y publican los dos unicos datos que la capa HTTP
necesita para responder sin reconstruir nada: `codigo` (identificador estable, contrato hacia
fuera) y `http_status`. El manejador unico de excepciones (`apps.core_security.manejadores`) ya
traduce cualquier `ErrorDominio` que exponga ese par, de modo que la vista del alta puede dejarlas
propagar: aqui NO se escribe traduccion HTTP, ni se construyen respuestas, ni se conoce DRF.

POR QUE SOLO HAY DOS ERRORES Y NO SEIS
--------------------------------------
Los fallos de obligatoriedad, formato, longitud y catalogo de rol (REQ-037, validaciones) los
resuelve el serializador de la peticion con los literales de `mensajes`, que es quien sabe a QUE
campo atribuirlos para que el formulario pueda senialar el dato erroneo. Levantarlos tambien aqui
crearia dos caminos para el mismo rechazo y el campo culpable se perderia por uno de ellos. Lo que
si vive en este modulo son los dos desenlaces que NO son un problema de forma de la peticion: el
correo que ya identifica a otra persona y el correo de credencial que no sale.

SECRETOS (REQ-063)
------------------
Ninguna excepcion de este modulo acepta, guarda ni serializa la credencial inicial generada, su
`password_hash` ni fragmento alguno de ellos. `EntregaCredencialInicialFallidaError` transporta
como mucho un codigo de error tecnico de la entrega, que describe el fallo del transporte y nunca
el material de la credencial.
"""

from __future__ import annotations

from apps.core.errores import ErrorDominio
from apps.usuarios.alta import mensajes


class CorreoDuplicadoError(ErrorDominio):
    """
    El correo corporativo del alta ya identifica a otra persona (REQ-037 regla 3, REQ-045, RN-01).

    Responde 409: la peticion es correcta en su forma -el correo tiene formato valido y cabe en la
    columna- pero choca con un hecho ya existente en el sistema, que es precisamente lo que
    distingue un 409 de un 422.

    DOS CAMINOS, UN SOLO ERROR
    --------------------------
    Esta excepcion se lanza por DOS motivos que para el administrador son el mismo hecho:

    1. La comprobacion previa (`RepositorioAltaUsuario.existe_correo`) encuentra ya un usuario cuyo
       correo, en minusculas y sin espacios, coincide con el que se pretende dar de alta.
    2. El INSERT viola el indice unico funcional `ux_usuario_email_ci ON usuario
       (LOWER(TRIM(corporate_email)))`. La comprobacion previa NO es suficiente por si sola: entre
       el `SELECT` que no vio nada y el `INSERT` que escribe caben dos altas simultaneas del mismo
       correo, y sin la restriccion de la base ambas prosperarian. La base es la unica que puede
       arbitrar esa carrera; el chequeo previo existe solo para dar el 409 con un mensaje util en
       el caso normal, en vez de dejar escapar un error de integridad crudo.

    EN NINGUNO DE LOS DOS CASOS SE CREA NADA
    ----------------------------------------
    Cuando se lanza esta excepcion no queda ninguna fila de usuario a medias: en el primer camino
    el INSERT ni siquiera se intento, y en el segundo Oracle lo rechazo entero. Es la garantia del
    criterio de aceptacion de REQ-037 («devuelve 409 y no crea el usuario») y tambien la mitad de
    la invariante de PBT-001: de dos altas cuyo correo normalizado coincide, como mucho una llega a
    existir.
    """

    codigo = "USR_EMAIL_DUPLICATED"
    http_status = 409

    def __init__(self, mensaje: str = mensajes.CORREO_DUPLICADO) -> None:
        super().__init__(mensaje)


class EntregaCredencialInicialFallidaError(ErrorDominio):
    """
    El correo con la credencial inicial no se ha podido entregar (REQ-038 regla 6).

    Responde 502: el fallo no esta en la peticion del administrador sino en un sistema de tercero
    -el servidor de correo-, y distinguirlo de un 500 generico le dice que el problema esta fuera
    de los datos que envio.

    EL USUARIO SI QUEDA CREADO: EL ALTA NO SE REVIERTE
    --------------------------------------------------
    Esta es la diferencia deliberada con el restablecimiento administrativo (EP-017,
    `apps.identidad.reposicion.errores.EntregaCredencialFallidaError`), que comparte codigo y
    estado HTTP pero NO semantica transaccional. Alli el usuario tenia ya una credencial valida y
    fallar la entrega obliga a no confirmar la nueva, porque darla por buena dejaria a la persona
    sin acceso y sin forma de recuperarlo. Aqui no habia credencial previa que preservar: deshacer
    el alta no devolveria a nadie a un estado util, solo borraria un usuario correctamente validado
    y obligaria al administrador a repetir todo el formulario por un fallo del servidor de correo.

    Por eso, cuando se lanza esta excepcion, la fila de `usuario` YA esta escrita y es definitiva.
    El 502 informa unicamente de que el correo con la credencial inicial no se entrego; el camino
    de salida es reemitir la credencial (EP-017), no repetir el alta -que ademas chocaria con el
    409 de correo duplicado-.

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
    "CorreoDuplicadoError",
    "EntregaCredencialInicialFallidaError",
]
