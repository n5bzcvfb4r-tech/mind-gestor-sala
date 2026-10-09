"""
Politica UNICA de contrasenia del servicio (REQ-069).

Este modulo es dominio puro: no toca la base de datos, no conoce el ORM y no importa Django.
Describe QUE exige la politica y devuelve QUE reglas incumple una contrasenia propuesta, para
que el servicio de credenciales -el unico que la consume- no tenga que reimplementar nada.

POR QUE UNA SOLA POLITICA, EN UN SOLO SITIO
-------------------------------------------
Tres flujos establecen contrasenia: el cambio propio (EP-005), el primer acceso (EP-006) y el
restablecimiento por administrador (EP-017). REQ-069 exige que una contrasenia rechazada en
uno lo sea en los tres. Esa garantia no se consigue repitiendo las mismas comprobaciones en
tres sitios con cuidado: se consigue teniendo UN solo evaluador. Si manana la politica cambia,
cambia aqui y los tres flujos cambian con ella; si estuviera duplicada, bastaria con olvidar
una copia para que el mismo secreto fuese valido por una puerta e invalido por otra.

LAS REGLAS SON LEY, Y SON ESTAS
-------------------------------
Minimo de caracteres, presencia de mayuscula/minuscula/digito, no contener el identificador
del usuario (username o parte local del correo corporativo) y no coincidir con las ultimas
contrasenias registradas. NO hay longitud maxima, NO hay caducidad y NO hay lista de
contrasenias comunes: anadir validaciones «de sentido comun» endureceria el contrato por la
puerta de atras y rechazaria contrasenias que el requisito acepta.

EL HISTORIAL SE DECLARA AQUI PERO NO SE EVALUA AQUI
---------------------------------------------------
La regla de no reutilizacion necesita leer `usuario_password_historico`, y este modulo es
dominio puro sin acceso a datos: la comprueba el servicio. Aun asi su CODIGO y su MENSAJE
viven en este mismo catalogo, porque la lista de reglas incumplidas que viaja en el 422 es
UNA SOLA: el cliente recibe «no cumple la politica» con todos los motivos juntos, sin poder
distinguir cuales se resolvieron en memoria y cual contra la base de datos. `regla_reutilizada()`
es la unica forma soportada de construir esa regla, para que el servicio no escriba su codigo
ni su texto a mano.

SE DEVUELVEN TODAS LAS REGLAS, NO LA PRIMERA
--------------------------------------------
`evaluar()` no corta en el primer incumplimiento (AC-PWD-03): devuelve la LISTA completa en
orden estable. Cortar convertiria el cambio de contrasenia en una partida de adivinanzas, con
el usuario descubriendo un requisito por intento.

SECRETOS (REQ-063, REQ-076)
---------------------------
Ni la contrasenia ni ningun fragmento suyo se escriben en logs, `repr`, mensajes ni trazas:
este modulo no registra nada y las `ReglaIncumplida` que devuelve solo transportan un codigo
estable y un texto fijo de `mensajes.py`. A `password` tampoco se le aplica NINGUNA
normalizacion -ni `strip()` ni cambio de caja-: los espacios y las mayusculas forman parte del
secreto, y recortarlos haria que se evaluase una contrasenia distinta de la que se guarda.
"""

from dataclasses import dataclass
from enum import Enum

from apps.identidad.credenciales import mensajes


# --- Parametros de la politica (REQ-069) ---------------------------------
# Valor unico y publico del minimo de longitud: lo usan la evaluacion y el texto del mensaje.
LONGITUD_MINIMA = 10

# Cuantas contrasenias anteriores del usuario se comparan para rechazar la reutilizacion. La
# comparacion contra el historico la hace el servicio; esta constante es el contrato que
# comparten ambos lados para que el numero del mensaje y el de la consulta no se separen.
MAXIMO_CONTRASENIAS_HISTORICAS = 3

# Campo del payload JSON al que se imputan todas las reglas incumplidas. El contrato nombra la
# contrasenia propuesta en camelCase (`newPassword`) en los tres flujos, de modo que el cliente
# puede pintar los mensajes junto a su campo sin traducir nada.
CAMPO_CONTRASENIA = "newPassword"

# Separador de la parte local del correo corporativo. Lo que hay ANTES de la arroba es el
# identificador que el usuario reconoce como suyo y el que tiende a reutilizar como secreto.
SEPARADOR_CORREO = "@"


class CodigoRegla(str, Enum):
    """
    Codigos ESTABLES de las reglas de la politica (REQ-069).

    Viajan en el `details` del 422, asi que son contrato hacia fuera: un cliente puede
    reaccionar a `SIN_DIGITO` sin leer el texto en espanol, y por eso renombrarlos rompe
    integraciones aunque el mensaje visible siga diciendo lo mismo. Hereda de `str` para que
    serialicen como su propio valor sin conversiones a mano.

    `REUTILIZADA` esta en el mismo enum que el resto aunque su comprobacion ocurra en el
    servicio y no en este modulo: la lista de incumplimientos es una sola y su vocabulario
    tambien.
    """

    LONGITUD_MINIMA = "LONGITUD_MINIMA"
    SIN_MAYUSCULA = "SIN_MAYUSCULA"
    SIN_MINUSCULA = "SIN_MINUSCULA"
    SIN_DIGITO = "SIN_DIGITO"
    CONTIENE_IDENTIFICADOR = "CONTIENE_IDENTIFICADOR"
    REUTILIZADA = "REUTILIZADA"


# Texto visible de cada regla, ya formateado con los parametros de la politica. Es el UNICO
# puente entre el codigo estable y el literal: ningun otro punto del codigo elige el mensaje
# de una regla, de modo que el mismo incumplimiento se explica igual en los tres flujos.
MENSAJE_POR_CODIGO: dict[CodigoRegla, str] = {
    CodigoRegla.LONGITUD_MINIMA: mensajes.POLITICA_LONGITUD_MINIMA.format(minimo=LONGITUD_MINIMA),
    CodigoRegla.SIN_MAYUSCULA: mensajes.POLITICA_SIN_MAYUSCULA,
    CodigoRegla.SIN_MINUSCULA: mensajes.POLITICA_SIN_MINUSCULA,
    CodigoRegla.SIN_DIGITO: mensajes.POLITICA_SIN_DIGITO,
    CodigoRegla.CONTIENE_IDENTIFICADOR: mensajes.POLITICA_CONTIENE_IDENTIFICADOR,
    CodigoRegla.REUTILIZADA: mensajes.POLITICA_REUTILIZADA.format(maximo=MAXIMO_CONTRASENIAS_HISTORICAS),
}


@dataclass(frozen=True, slots=True)
class ReglaIncumplida:
    """
    Una regla de la politica que la contrasenia propuesta NO cumple.

    Transporta solo dos datos: el codigo estable de la regla y su texto en espanol. No lleva
    -ni puede llevar- la contrasenia, su longitud real, su hash ni nada de la anterior:
    `slots=True` cierra la clase a atributos nuevos y `frozen=True` impide que nadie la
    enriquezca por el camino hacia la respuesta. Es la unidad de la lista que viaja en el 422.
    """

    codigo: str
    mensaje: str

    def como_detalle(self) -> dict[str, str]:
        """
        Serializa la regla al formato canonico de `details` del servicio.

        El cuerpo de error del repo (`apps.core_security.respuestas.cuerpo_error`) espera
        `details` como `list[dict[str, str]]` con las claves `field` y `message`, exactamente
        la forma que produce `manejadores._aplanar_detalle` para los errores de validacion de
        DRF. Se reutiliza esa forma para que el cliente trate un rechazo por politica igual
        que cualquier otro error de campo, sin un segundo formato que aprender.
        """

        return {"field": CAMPO_CONTRASENIA, "message": self.mensaje}


def _regla(codigo: CodigoRegla) -> ReglaIncumplida:
    """Construye la `ReglaIncumplida` de un codigo tomando su texto del catalogo unico."""

    return ReglaIncumplida(codigo=codigo.value, mensaje=MENSAJE_POR_CODIGO[codigo])


def _contiene(password: str, fragmento: str | None) -> bool:
    """
    Indica si la contrasenia contiene el fragmento, ignorando mayusculas y espacios del fragmento.

    El fragmento (username o parte local del correo) se recorta y se compara en minusculas:
    `Pedro` dentro de `pedro.lopez` es la misma pista para un atacante. Un fragmento ausente,
    vacio o compuesto solo de espacios NUNCA cuenta como contenido: la cadena vacia esta
    contenida en cualquier texto, y darla por buena marcaria TODA contrasenia como
    incumplidora. La contrasenia se pasa a minusculas solo para ESTA comparacion; el valor
    que se evalua y se guarda sigue siendo el original.
    """

    if not fragmento:
        return False

    aguja = fragmento.strip().lower()
    if not aguja:
        return False

    return aguja in password.lower()


class PoliticaContrasenia:
    """
    Evaluador unico de la politica de contrasenia (REQ-069).

    Sin estado y sin dependencias: se puede instanciar donde haga falta, aunque el camino
    previsto es que el servicio de credenciales lo use como colaborador unico para los tres
    flujos que establecen contrasenia.
    """

    def evaluar(self, password: str, *, username: str | None = None, corporate_email: str | None = None) -> list[ReglaIncumplida]:
        """
        Devuelve TODAS las reglas de politica que incumple la contrasenia propuesta.

        No corta en el primer incumplimiento (AC-PWD-03) y el orden es estable -longitud,
        mayuscula, minuscula, digito, identificador- para que dos evaluaciones de la misma
        entrada produzcan exactamente el mismo `details`.

        La comprobacion por caracter usa `str.isupper()`, `str.islower()` e `str.isdigit()`
        en vez de rangos ASCII: una contrasenia con acentos, enies o alfabetos no latinos es
        tan valida como cualquier otra y debe clasificarse bien.

        Args:
            password: contrasenia propuesta, TAL CUAL la escribio el usuario. No se recorta
                ni se normaliza: los espacios y la caja forman parte del secreto.
            username: identificador del usuario; si falta, la comprobacion de identificador
                simplemente no aporta regla, nunca falla.
            corporate_email: correo corporativo del usuario; se compara su parte local (lo
                anterior a la arroba).

        Returns:
            Lista de reglas incumplidas, vacia si la contrasenia cumple la politica. La regla
            `REUTILIZADA` NUNCA sale de aqui: la anade el servicio tras consultar el historico.
        """

        incumplidas: list[ReglaIncumplida] = []

        if len(password) < LONGITUD_MINIMA:
            incumplidas.append(_regla(CodigoRegla.LONGITUD_MINIMA))

        if not any(caracter.isupper() for caracter in password):
            incumplidas.append(_regla(CodigoRegla.SIN_MAYUSCULA))

        if not any(caracter.islower() for caracter in password):
            incumplidas.append(_regla(CodigoRegla.SIN_MINUSCULA))

        if not any(caracter.isdigit() for caracter in password):
            incumplidas.append(_regla(CodigoRegla.SIN_DIGITO))

        if self._contiene_identificador(password, username=username, corporate_email=corporate_email):
            incumplidas.append(_regla(CodigoRegla.CONTIENE_IDENTIFICADOR))

        return incumplidas

    def regla_reutilizada(self) -> ReglaIncumplida:
        """
        Devuelve la regla de reutilizacion para que el servicio la anada a la lista del 422.

        La comprobacion contra `usuario_password_historico` vive en el servicio porque exige
        base de datos, pero el codigo y el texto se construyen AQUI: asi la regla de historial
        entra en la misma lista, con el mismo vocabulario, que las evaluadas en memoria, y el
        mensaje sigue hablando solo de la regla, nunca de la contrasenia anterior ni de su hash.
        """

        return _regla(CodigoRegla.REUTILIZADA)

    def _contiene_identificador(self, password: str, *, username: str | None, corporate_email: str | None) -> bool:
        """
        Indica si la contrasenia contiene el username o la parte local del correo corporativo.

        Ambos identificadores producen UNA sola regla incumplida, no dos: al usuario se le dice
        que su contrasenia no puede contener sus identificadores, no cual de ellos ha detectado
        el sistema. La parte local es lo que precede a la primera arroba; un correo sin arroba
        se compara entero, porque en ese caso el valor completo es el identificador.
        """

        parte_local = (corporate_email or "").split(SEPARADOR_CORREO, 1)[0]
        return _contiene(password, username) or _contiene(password, parte_local)
