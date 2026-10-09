"""
Excepciones de dominio de la custodia de credenciales (REQ-054, REQ-069).

Heredan de `apps.core.errores.ErrorDominio` y anaden los dos datos que la capa HTTP necesita
para responder sin reconstruir nada: `codigo` (identificador estable) y `http_status`. El
manejador unico de excepciones (`apps.core_security.manejadores`) ya traduce cualquier
`ErrorDominio` que exponga ese par, asi que las vistas de los flujos de contrasenia pueden
dejarlas propagar sin repetir la traduccion.

DOS CLASES DE FALLO, DOS CONTRATOS DISTINTOS
--------------------------------------------
El rechazo por politica es un fallo FUNCIONAL del usuario: responde 422 y SI explica, en la
lista de reglas incumplidas, que tiene que corregir. El fallo de custodia es un fallo TECNICO
nuestro: responde 500 y NO explica nada, porque el usuario no puede hacer nada con el detalle
y ese detalle describiria el interior del servicio.

SECRETOS (REQ-063, REQ-076)
---------------------------
Ninguna excepcion de este modulo acepta, guarda ni serializa la contrasenia en claro, su hash
ni fragmento alguno de la anterior. Los textos salen siempre de `mensajes.py` y hablan de la
REGLA, nunca del valor.
"""

from apps.core.errores import ErrorDominio
from apps.identidad.credenciales import mensajes
from apps.identidad.credenciales.politica import ReglaIncumplida


class PoliticaContraseniaError(ErrorDominio):
    """
    La contrasenia propuesta no cumple la politica unica de contrasenias (REQ-069).

    Responde 422 con una CABECERA GENERICA (`mensajes.POLITICA_NO_CUMPLIDA`) y la LISTA de
    reglas incumplidas en `detalles`, ya en el formato canonico `{"field", "message"}` que
    espera `apps.core_security.respuestas.cuerpo_error`. Se transporta la lista entera y no
    el primer fallo: AC-PWD-03 exige que el usuario vea de una vez todo lo que debe corregir.

    EL MENSAJE NO REVELA EL SECRETO. Ni la cabecera ni ninguna de las reglas mencionan la
    contrasenia propuesta, la anterior, su hash, su longitud real ni un fragmento suyo:
    describen la REGLA incumplida y nada mas. Un rechazo por reutilizacion dice que coincide
    con una de las ultimas usadas, jamas con cual ni que aspecto tenia.

    La misma excepcion sirve a los tres flujos que establecen contrasenia (EP-005, EP-006 y
    EP-017), de modo que una contrasenia rechazada por uno lo es por todos, con identico
    codigo, estado y vocabulario.
    """

    codigo = "AUTH_PASSWORD_POLICY"
    http_status = 422

    def __init__(self, reglas: list[ReglaIncumplida], mensaje: str = mensajes.POLITICA_NO_CUMPLIDA) -> None:
        self.reglas: list[ReglaIncumplida] = list(reglas)
        self.detalles: list[dict[str, str]] = [regla.como_detalle() for regla in self.reglas]
        super().__init__(mensaje)


class CustodiaCredencialError(ErrorDominio):
    """
    La custodia de la credencial ha fallado por un motivo tecnico (REQ-054).

    Cubre el fallo del cifrado/hashing y cualquier otra rotura de la custodia que impida
    completar la operacion. Responde 500 con el texto LITERAL de REQ-054, «No ha sido posible
    completar la operacion», SIN detalle tecnico: el `detalle` que recibe el constructor es
    traza interna, se registra en el log junto al `traceId` y NUNCA se serializa en la
    respuesta. Contarle al cliente que ha fallado el hasher no le ayuda a resolver nada y si
    describe el interior del servicio.

    El `detalle` tampoco puede contener la contrasenia en claro ni su hash (REQ-063, REQ-076):
    quien construya la excepcion aporta el motivo tecnico, no el material.
    """

    codigo = "SYS_UNEXPECTED"
    http_status = 500

    def __init__(self, detalle: str = "", mensaje: str = mensajes.CUSTODIA_NO_COMPLETADA) -> None:
        self.detalle: str = detalle
        super().__init__(mensaje)


class ContraseniaSinHashearError(ErrorDominio):
    """
    Se ha intentado persistir material de credencial que NO procede del servicio de hashing.

    Es la validacion 1 de REQ-054 convertida en guardia activa: la persistencia no confia en
    que quien la llama haya hasheado antes, lo COMPRUEBA, y aborta si el material no lleva la
    marca del hasher configurado. Una contrasenia guardada en claro no se detecta leyendo la
    tabla despues; se evita no escribiendola nunca.

    Hacia fuera es indistinguible del resto de fallos de custodia: 500 con el texto literal de
    REQ-054. El motivo real (`mensajes.CONTRASENIA_SIN_HASHEAR`) queda en `self.detalle`, para
    el log tecnico, porque delatar al cliente que el material llego sin hashear describe el
    estado interno de la custodia sin aportarle nada.

    JAMAS recibe ni guarda el material rechazado: el valor sospechoso no se copia a la
    excepcion, no se registra y no viaja a ninguna traza (REQ-063, REQ-076).
    """

    codigo = "SYS_UNEXPECTED"
    http_status = 500

    def __init__(self, detalle: str = mensajes.CONTRASENIA_SIN_HASHEAR, mensaje: str = mensajes.CUSTODIA_NO_COMPLETADA) -> None:
        self.detalle: str = detalle
        super().__init__(mensaje)
