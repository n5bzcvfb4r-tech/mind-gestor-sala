"""
Prueba basada en propiedades de la custodia de credenciales (PBT-010).

Cuantifica sobre un ESPACIO de contrasenias generado por Hypothesis, no sobre una lista de ejemplos
escogidos a mano: lo que se afirma es la propiedad de IDA Y VUELTA del servicio -lo que se guarda
con `calcular_verificador` se reconoce con `verificar`, y solo eso se reconoce-, y una propiedad asi
solo queda acreditada probandola contra entradas que el autor del test no ha elegido.

ESTA PRUEBA NO TOCA LA BASE DE DATOS, Y ES DELIBERADO
-----------------------------------------------------
`calcular_verificador` y `verificar` son operaciones PURAS: una llama a `make_password` y la otra a
`check_password`, ninguna de las dos lee ni escribe una fila. Abrir un contenedor Oracle por cada
ejemplo generado haria la propiedad inejecutable sin anadir una sola garantia sobre lo que aqui se
mide. La evidencia de PERSISTENCIA de la credencial (que `establecer` escribe las columnas, archiva
el hash anterior y lo hace en una sola transaccion) corresponde a las pruebas de integracion contra
el motor real, no a este fichero.

LOS «DOS USUARIOS» SON DOS INVOCACIONES DEL SERVICIO
----------------------------------------------------
El escenario pide que dos usuarios con la misma contrasenia presenten verificadores distintos. Dar
de alta a dos usuarios con la misma contrasenia consiste, EXACTAMENTE, en invocar dos veces
`calcular_verificador` con esa cadena: cada invocacion genera su propia sal aleatoria criptografica
(`get_random_string`, sembrado por `secrets`) y la embebe en el hash. Modelar los dos usuarios como
dos invocaciones no es un atajo sobre el escenario: es el mismo codigo que se ejecuta al crear las
dos filas, sin la parte -el INSERT- que no interviene en la propiedad.

EL COSTE DEL ALGORITMO ACOTA EL NUMERO DE EJEMPLOS
---------------------------------------------------
Argon2id es caro A PROPOSITO (unos 80 ms por operacion con los parametros del proyecto) y cada
ejemplo encadena cinco operaciones. Por eso `max_examples` esta acotado y `deadline=None`: sin ese
`deadline` Hypothesis interpretaria la lentitud deliberada del hasher como un fallo de la propiedad.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from apps.identidad.credenciales.politica import LONGITUD_MINIMA, PoliticaContrasenia
from apps.identidad.credenciales.servicio import ServicioCustodiaCredenciales


# --- Alfabeto de las contrasenias generadas ------------------------------
# La politica NO exige caracteres ASCII ni prohibe simbolos: clasifica con `str.isupper()`,
# `str.islower()` e `str.isdigit()`, que entienden acentos y enies. El alfabeto incluye por eso
# letras acentuadas y simbolos, tal y como pide el catalogo de datos de prueba, ademas del espacio
# interior -que forma parte del secreto y que el servicio no recorta.
ACENTUADAS = "áéíóúüñÁÉÍÓÚÜÑçÇ"
SIMBOLOS = "!#$%&()*+,-./:;<=>?@[]^_{|}~ "
LETRAS_MAYUSCULAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÑÇ"
LETRAS_MINUSCULAS = "abcdefghijklmnopqrstuvwxyzáéíóúñç"
DIGITOS = "0123456789"
ALFABETO_LIBRE = LETRAS_MINUSCULAS + LETRAS_MAYUSCULAS + DIGITOS + ACENTUADAS + SIMBOLOS

# Longitud del tramo libre. Los tres caracteres obligatorios -una mayuscula, una minuscula y un
# digito- se anaden aparte, de modo que la contrasenia final mide entre LONGITUD_MINIMA (10) y 64
# caracteres, que es el rango exacto del escenario.
LONGITUD_MAXIMA = 64
TRAMO_LIBRE_MINIMO = LONGITUD_MINIMA - 3
TRAMO_LIBRE_MAXIMO = LONGITUD_MAXIMA - 3

# Las dos formas en que la segunda cadena se separa de la contrasenia, una por cada caso de los
# datos de prueba: un solo caracter cambiado, o la misma cadena con la caja de una letra invertida.
MODO_UN_CARACTER = "un_caracter"
MODO_SOLO_CAJA = "solo_caja"
MODOS: tuple[str, ...] = (MODO_UN_CARACTER, MODO_SOLO_CAJA)

# Rango del entero con el que se eligen posiciones dentro de la contrasenia. Se reduce con un modulo
# sobre la longitud real, asi que cualquier valor grande sirve y la estrategia no depende del tamano
# concreto de la cadena generada.
SELECTOR_MAXIMO = 10**6


def _componer_password(tramo_libre: str, mayuscula: str, minuscula: str, digito: str, corte: int) -> str:
    """
    Arma una contrasenia CONFORME intercalando los tres caracteres obligatorios en el tramo libre.

    El tramo libre puede salir sin mayuscula, sin minuscula o sin digito -el alfabeto es amplio a
    proposito-, asi que los tres se insertan de forma explicita. El punto de corte se deriva del
    entero generado para que no queden siempre en la misma posicion: la propiedad debe cumplirse con
    la mayuscula al principio, en medio o al final.
    """

    posicion = corte % (len(tramo_libre) + 1)
    return f"{tramo_libre[:posicion]}{mayuscula}{minuscula}{digito}{tramo_libre[posicion:]}"


def _componer_variante(password: str, modo: str, selector: int) -> str:
    """
    Deriva de la contrasenia una cadena DISTINTA, por uno de los dos caminos del escenario.

    `MODO_UN_CARACTER` sustituye un unico caracter por otro del alfabeto que no coincida con el
    original: la cadena resultante difiere en una sola posicion, que es el caso limite que de verdad
    pone a prueba la comparacion. `MODO_SOLO_CAJA` invierte la caja de UNA letra y no cambia ninguna
    otra cosa: la cadena solo se distingue por mayusculas/minusculas, y debe fallar igualmente
    porque el servicio no normaliza la caja. Siempre existe al menos una letra con caja invertible,
    porque la contrasenia lleva por construccion una mayuscula y una minuscula.
    """

    if modo == MODO_SOLO_CAJA:
        posiciones = [indice for indice, caracter in enumerate(password) if caracter.swapcase() != caracter]
        indice = posiciones[selector % len(posiciones)]
        sustituto = password[indice].swapcase()
    else:
        indice = selector % len(password)
        sustituto = next(caracter for caracter in ALFABETO_LIBRE if caracter != password[indice])

    return f"{password[:indice]}{sustituto}{password[indice + 1 :]}"


def _componer_caso(datos: tuple[str, str, str, str, int, str, int]) -> tuple[str, str]:
    """Convierte la tupla generada en el par (contrasenia conforme, cadena distinta de ella)."""

    tramo_libre, mayuscula, minuscula, digito, corte, modo, selector = datos
    password = _componer_password(tramo_libre, mayuscula, minuscula, digito, corte)
    return password, _componer_variante(password, modo, selector)


# Estrategia unica del escenario: para cada ejemplo produce una contrasenia conforme a la politica
# -entre 10 y 64 caracteres, con acentuadas y simbolos- y otra cadena distinta de ella, generada por
# uno de los dos caminos de los datos de prueba.
PARES_PASSWORD_Y_VARIANTE = st.tuples(
    st.text(alphabet=ALFABETO_LIBRE, min_size=TRAMO_LIBRE_MINIMO, max_size=TRAMO_LIBRE_MAXIMO),
    st.sampled_from(LETRAS_MAYUSCULAS),
    st.sampled_from(LETRAS_MINUSCULAS),
    st.sampled_from(DIGITOS),
    st.integers(min_value=0, max_value=SELECTOR_MAXIMO),
    st.sampled_from(MODOS),
    st.integers(min_value=0, max_value=SELECTOR_MAXIMO),
).map(_componer_caso)


@settings(max_examples=50, deadline=None)
@given(caso=PARES_PASSWORD_Y_VARIANTE)
def test_PBT_010_la_contrasenia_custodiada_verifica_solo_contra_si_misma_y_nunca_queda_legible(caso: tuple[str, str]) -> None:
    """
    [PBT-010] Para toda contrasenia conforme, su verificador la reconoce a ella y solo a ella.

    Cuatro afirmaciones, las del escenario:

    1. verificar la contrasenia ORIGINAL contra su propio verificador resulta correcta;
    2. verificar cualquier cadena DISTINTA -aunque difiera en un solo caracter, o solo en la caja-
       resulta incorrecta;
    3. dos usuarios con la misma contrasenia (dos invocaciones del servicio, cada una con su propia
       sal) presentan `password_hash` DISTINTOS, y aun asi ambos reconocen la contrasenia: la sal
       nueva no rompe la ida y vuelta;
    4. la contrasenia legible NO figura en el hash, ni en la sal, ni en el `repr` del verificador.

    La precondicion del escenario -«la contrasenia generada cumple la politica vigente»- se afirma
    aqui mismo contra `PoliticaContrasenia`, y no se descarta con `assume`: si el generador llegara
    a producir una cadena no conforme, la prueba debe fallar y delatar el generador, no silenciarlo.
    """

    password, otra_cadena = caso
    servicio = ServicioCustodiaCredenciales()

    assert PoliticaContrasenia().evaluar(password) == [], "La contrasenia generada debe cumplir la politica vigente"
    assert LONGITUD_MINIMA <= len(password) <= LONGITUD_MAXIMA
    assert otra_cadena != password

    verificador_primer_usuario = servicio.calcular_verificador(password)
    verificador_segundo_usuario = servicio.calcular_verificador(password)

    assert servicio.verificar(password, verificador_primer_usuario.password_hash) is True
    assert servicio.verificar(otra_cadena, verificador_primer_usuario.password_hash) is False

    assert verificador_primer_usuario.password_hash != verificador_segundo_usuario.password_hash
    assert servicio.verificar(password, verificador_segundo_usuario.password_hash) is True

    for verificador in (verificador_primer_usuario, verificador_segundo_usuario):
        assert password not in verificador.password_hash
        assert password not in (verificador.password_salt or "")
        assert password not in repr(verificador)
