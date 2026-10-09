"""
Generador de la credencial temporal del restablecimiento administrativo (REQ-073 regla 4).

Este modulo es dominio PURO: no toca la base de datos, no conoce el ORM, no importa Django y no
registra NADA. Produce una cadena y calcula su caducidad; quien la persiste, la cifra y la envia
son otros (`ServicioCustodiaCredenciales` y el servicio de entrega de avisos).

ENTROPIA CRIPTOGRAFICA, NO PSEUDOALEATORIEDAD
---------------------------------------------
REQ-073 regla 4 exige que la credencial temporal sea de minimo 12 caracteres y de entropia
criptografica. Todo el azar de este modulo sale de `secrets` -`secrets.choice` para elegir cada
caracter y `secrets.SystemRandom().shuffle` para barajar-, NUNCA de `random`: el generador de
`random` es un Mersenne Twister reproducible, de modo que observar unas pocas credenciales
bastaria para predecir las siguientes. Por eso `random` no se importa siquiera en este fichero.

LA CREDENCIAL NACE CUMPLIENDO LA POLITICA, NO SE ESPERA QUE LA CUMPLA
----------------------------------------------------------------------
Quien persiste la credencial es `ServicioCustodiaCredenciales.establecer`, que la evalua contra
la politica UNICA del proyecto (`apps.identidad.credenciales.politica`, REQ-069) y responde 422
si falla. Un generador que dejase el cumplimiento al azar produciria 422 intermitentes en una
operacion donde el administrador no ha escrito ninguna contrasenia y no tiene nada que corregir.
Por eso la credencial se construye SEMBRANDO una mayuscula, una minuscula y un digito, y ademas
se verifica contra la propia politica antes de devolverla: la garantia es por construccion y la
comprobacion es la red de seguridad que detecta el unico caso que la construccion no puede
evitar -que la cadena contenga por azar el identificador del usuario-.

LA CREDENCIAL NO SE REGISTRA EN NINGUN SITIO (REQ-063, REQ-076)
-----------------------------------------------------------------
El valor devuelto no se escribe en logs, no se interpola en mensajes, no viaja dentro de ninguna
excepcion de este modulo y no se guarda en ningun atributo. `CredencialNoGenerableError` sale con
un texto fijo del catalogo, sin decir siquiera cuantos intentos se consumieron: cualquier dato
sobre los candidatos descartados acotaria el espacio de busqueda del secreto que si se entrego.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from apps.identidad.credenciales.politica import PoliticaContrasenia
from apps.identidad.reposicion.errores import CredencialNoGenerableError

# --- Parametros de la credencial temporal (REQ-073 regla 4) --------------
# Longitud que se genera. Queda por encima del minimo de 12 que fija REQ-073 y del
# `LONGITUD_MINIMA_CREDENCIAL` que valida `apps.avisos.credenciales.entrega` antes de componer el
# correo: cuatro caracteres de margen cuestan nada de usabilidad y multiplican el espacio de
# busqueda, y evitan que un cambio futuro de cualquiera de los dos minimos deje este valor justo.
LONGITUD_CREDENCIAL_TEMPORAL = 16

# Minimo del requisito. No es decorativo: `generar_credencial_temporal` lo comprueba sobre la
# longitud recibida, de modo que ningun llamador pueda pedir una credencial mas corta de lo que
# REQ-073 permite aunque pase el parametro a mano.
LONGITUD_MINIMA_REQUERIDA = 12

# Vigencia de la credencial temporal, en horas. REQ-073 deja la caducidad como [gap] -habla de
# una credencial temporal pero no dice cuanto dura-, mientras que REQ-038 SI fija 48 h para el
# `password_expires_at` de esa misma credencial temporal. Se adopta el valor que el requisito ya
# declara en vez de inventar uno nuevo: dos numeros distintos para la misma credencial seria una
# decision de negocio tomada por el codigo.
VIGENCIA_CREDENCIAL_TEMPORAL_HORAS = 48

# --- Alfabeto -------------------------------------------------------------
# Se EXCLUYEN los caracteres ambiguos `O`, `I`, `l`, `0` y `1`. La credencial se transcribe a mano
# desde un correo, y una `l` confundida con un `1` -o una `O` con un `0`- acaba en un intento
# fallido, en un bloqueo temporal (REQ-055) y en una llamada al administrador para repetir el
# restablecimiento que ya habia funcionado.
_MAYUSCULAS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_MINUSCULAS = "abcdefghijkmnopqrstuvwxyz"
_DIGITOS = "23456789"

# No se incluyen simbolos: la politica unica del proyecto (REQ-069) exige mayuscula, minuscula y
# digito, y NO exige caracteres especiales. Anadirlos endureceria el secreto por la puerta de
# atras a cambio de complicar la transcripcion y de chocar con teclados y clientes de correo.
_ALFABETO = _MAYUSCULAS + _MINUSCULAS + _DIGITOS

# Cuantas credenciales se construyen como maximo antes de rendirse. El unico incumplimiento que la
# construccion no puede prevenir es que la cadena contenga por azar el username o la parte local
# del correo; con este alfabeto es rarisimo, y diez intentos independientes lo hacen despreciable
# sin arriesgar un bucle infinito si algun dia la politica incorpora una regla nueva.
_MAXIMO_INTENTOS = 10

_POLITICA = PoliticaContrasenia()


def generar_credencial_temporal(
    *,
    username: str | None = None,
    corporate_email: str | None = None,
    longitud: int = LONGITUD_CREDENCIAL_TEMPORAL,
) -> str:
    """
    Genera una credencial temporal con entropia criptografica que cumple la politica del proyecto.

    La cadena se construye sembrando una mayuscula, una minuscula y un digito -cada uno elegido
    con `secrets.choice` de su propio alfabeto-, rellenando el resto desde el alfabeto completo y
    barajando el total con `secrets.SystemRandom().shuffle`. El barajado no es cosmetico: sin el,
    los tres caracteres sembrados ocuparian siempre las mismas posiciones y un atacante sabria la
    clase de cada una de las tres primeras, reduciendo el espacio de busqueda.

    Tras construirla se verifica contra `PoliticaContrasenia().evaluar()`, la MISMA politica que
    aplicara `ServicioCustodiaCredenciales.establecer` al persistirla. Si queda alguna regla
    incumplida -en la practica, solo que la cadena contenga por azar el identificador del
    usuario- se descarta el candidato y se genera otro, hasta `_MAXIMO_INTENTOS`.

    Args:
        username: identificador del usuario destino; se pasa a la politica para que la credencial
            no lo contenga. Si falta, esa regla simplemente no aporta incumplimiento.
        corporate_email: correo corporativo del usuario destino; la politica compara su parte
            local (lo anterior a la arroba).
        longitud: numero de caracteres a generar. Por defecto `LONGITUD_CREDENCIAL_TEMPORAL`.

    Returns:
        La credencial temporal en claro. Es el UNICO punto del flujo donde existe sin cifrar: el
        llamador debe entregarsela al servicio de custodia y al de entrega, y NO escribirla en
        logs, mensajes ni trazas (REQ-063, REQ-076).

    Raises:
        CredencialNoGenerableError: si `longitud` queda por debajo de `LONGITUD_MINIMA_REQUERIDA`
            o si se agotan los intentos sin obtener una credencial que cumpla la politica. La
            excepcion NO transporta ningun candidato descartado.
    """

    if longitud < LONGITUD_MINIMA_REQUERIDA:
        # No se incluye la longitud pedida en el mensaje visible: el detalle tecnico va a la traza
        # del llamador, no al administrador, que no ha elegido este parametro.
        raise CredencialNoGenerableError()

    for _ in range(_MAXIMO_INTENTOS):
        candidata = _construir(longitud)
        if not _POLITICA.evaluar(candidata, username=username, corporate_email=corporate_email):
            return candidata

    # Se agotaron los intentos. Ni la ultima candidata ni el numero de intentos salen de aqui.
    raise CredencialNoGenerableError()


def caducidad_credencial_temporal(desde: datetime) -> datetime:
    """
    Calcula el instante en que caduca la credencial temporal (REQ-038: 48 h).

    Args:
        desde: instante de referencia, normalmente el del restablecimiento. Es un `datetime` NAIVE
            en UTC, el que sella el servidor con `apps.core.contexto.utc_now()`.

    Returns:
        El mismo instante mas `VIGENCIA_CREDENCIAL_TEMPORAL_HORAS`, tambien naive en UTC. El valor
        alimenta `password_expires_at`.

    El proyecto trabaja con instantes NAIVE en UTC de punta a punta y mezclar naive y aware esta
    PROHIBIDO: sumar un `timedelta` conserva la naturaleza del operando, asi que un `desde` aware
    produciria un resultado aware que reventaria al compararse con el resto de marcas del sistema
    -o, peor, que se guardaria con un desfase silencioso en una columna sin zona horaria-.
    """

    return desde + timedelta(hours=VIGENCIA_CREDENCIAL_TEMPORAL_HORAS)


def _construir(longitud: int) -> str:
    """
    Construye una credencial candidata garantizando mayuscula, minuscula y digito por construccion.

    Todo el azar viene de `secrets`: `secrets.choice` para cada caracter y
    `secrets.SystemRandom().shuffle` para la posicion final de los sembrados. `random.shuffle`
    esta PROHIBIDO aqui: barajar con un generador predecible desharia la entropia de la eleccion.
    """

    caracteres: list[str] = [
        secrets.choice(_MAYUSCULAS),
        secrets.choice(_MINUSCULAS),
        secrets.choice(_DIGITOS),
    ]
    caracteres.extend(secrets.choice(_ALFABETO) for _ in range(longitud - len(caracteres)))
    secrets.SystemRandom().shuffle(caracteres)

    return "".join(caracteres)


__all__ = [
    "LONGITUD_CREDENCIAL_TEMPORAL",
    "LONGITUD_MINIMA_REQUERIDA",
    "VIGENCIA_CREDENCIAL_TEMPORAL_HORAS",
    "caducidad_credencial_temporal",
    "generar_credencial_temporal",
]
