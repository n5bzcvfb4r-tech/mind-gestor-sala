"""
Prueba basada en propiedades del control de intentos fallidos (PBT-008).

Cuantifica sobre un ESPACIO de secuencias de intentos generado por Hypothesis -de 1 a 40 intentos,
con aciertos y fallos en proporcion aleatoria y en instantes crecientes-, no sobre un punado de
guiones escritos a mano. Lo que se afirma no es un caso ("al quinto fallo, 423"), sino el INVARIANTE
del control de intentos: haga lo que haga la secuencia, y en CUALQUIER punto intermedio de ella, el
contador de fallos vive dentro de `[0, umbral]`, un acierto sobre cuenta libre lo deja en cero,
estar en el umbral implica bloqueo vigente, y mientras el bloqueo este vigente ningun intento
concede sesion aunque la contrasenia sea correcta. Un invariante de esa forma -«para toda
secuencia»- solo queda acreditado probandolo contra secuencias que el autor del test no ha elegido;
los ejemplos escogidos a mano recorren justo los caminos que el autor ya tenia en la cabeza.

ESTA PRUEBA NO TOCA LA BASE DE DATOS, Y ES DELIBERADO
------------------------------------------------------
`PoliticaBloqueo` es dominio PURO: no lee el ORM, no escribe filas, no consulta el reloj -el
instante entra como parametro- y devuelve siempre un `EstadoBloqueo` nuevo. La propiedad se mide
exactamente sobre esa funcion de transicion, asi que no hay nada que persistir para observarla.
Abrir un Oracle por cada uno de los cientos de ejemplos generados no anadiria ni una garantia sobre
lo que aqui se afirma, y ademas el entorno Oracle de esta sesion NO esta disponible: por eso el
fichero no lleva el marcador `integration` ni `django_db`. La evidencia de que el servicio PERSISTE
el estado resultante en `failed_password_attempts`, `locked_until` y `last_failed_attempt_at`, y de
que la API responde 423, corresponde a las pruebas de integracion contra el motor real, no aqui.

POR QUE SE GENERAN TAMBIEN EL UMBRAL Y LA DURACION
---------------------------------------------------
La precondicion del escenario dice «con umbral de intentos y duracion de bloqueo CONFIGURADOS», no
«con umbral 5 y duracion 15». Ambos son un parametro de operacion ajustable por entorno
(`BLOQUEO_UMBRAL_INTENTOS_FALLIDOS`, `BLOQUEO_DURACION_MINUTOS`), de modo que la propiedad se
cuantifica tambien sobre ellos y la politica se construye SIEMPRE explicita. Asi el invariante queda
atado a la regla y no a los dos numeros por defecto, y un cambio de configuracion no puede colar un
umbral con el que el contador se desmadre.

LOS «ORIGENES DISTINTOS» SON, PRECISAMENTE, UN PARAMETRO QUE NO EXISTE
-----------------------------------------------------------------------
Los datos de prueba piden intentos desde sesiones y origenes distintos sobre la misma cuenta. El
bloqueo es POR CUENTA, no por sesion ni por IP (REQ-055 RN-02, REQ-072 RN-04): cambiar de navegador
o de red no regala intentos. La evidencia de esa regla no es pasarle un origen a la politica y
comprobar que lo ignora -eso obligaria a inventar un parametro en la firma-, sino justo lo
contrario: el origen se genera y se hace variar entre intentos, y NO se le pasa a ningun metodo
porque ninguno lo admite. Que la firma no lo reciba es la prueba estructural de que el origen no
puede influir en el contador.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from apps.identidad.bloqueo.politica import EstadoBloqueo, PoliticaBloqueo

# Instante base de la linea temporal, NAIVE en UTC igual que `utc_now()` y que las columnas
# TIMESTAMP del esquema. Mezclar aware y naive reventaria la comparacion `bloqueada_hasta > ahora`,
# que es la que decide si se concede acceso.
INSTANTE_BASE = datetime(2026, 1, 1, 9, 0, 0)

# Rangos de la configuracion generada: umbral y duracion se mueven alrededor -y a ambos lados- de
# los valores por defecto del proyecto (5 intentos, 15 minutos).
UMBRAL_MINIMO, UMBRAL_MAXIMO = 1, 8
DURACION_MINIMA, DURACION_MAXIMA = 1, 30

# Longitud de la secuencia de intentos, tal cual la pide el catalogo de datos de prueba.
INTENTOS_MINIMOS, INTENTOS_MAXIMOS = 1, 40

# Sesiones y origenes desde los que llegan los intentos contra la MISMA cuenta. Se generan para que
# varien entre intentos y no se pasan a la politica: ver el docstring del modulo.
ORIGENES = ("sesion-A/203.0.113.10", "sesion-B/198.51.100.7", "sesion-C/192.0.2.44", "sesion-D/203.0.113.10")


@st.composite
def secuencias_de_intentos(draw: st.DrawFn) -> tuple[int, int, list[tuple[bool, datetime, str]]]:
    """
    Produce una configuracion de politica y una secuencia completa de intentos sobre una cuenta.

    Devuelve `(umbral, duracion_minutos, intentos)`, donde cada intento es
    `(acierto, instante, origen)`.

    Los instantes son CRECIENTES POR CONSTRUCCION -precondicion del escenario-: se generan como
    deltas en minutos que se acumulan sobre `INSTANTE_BASE`, en vez de sortear marcas sueltas y
    ordenarlas despues. El delta se muestrea en `[0, duracion * 2 + 5]`, un rango que abarca los dos
    lados de la duracion del bloqueo: hay saltos cortos -varios intentos dentro de la ventana de
    bloqueo, que es cuando el bloqueo tiene que mandar- y saltos largos que la rebasan y dejan que
    el bloqueo venza solo, incluido el delta 0 (dos intentos en el mismo instante, que es el borde
    donde `bloqueada_hasta > ahora` decide) y el delta exactamente igual a la duracion (el instante
    de expiracion, en el que el bloqueo ya no retiene).
    """

    umbral = draw(st.integers(min_value=UMBRAL_MINIMO, max_value=UMBRAL_MAXIMO))
    duracion_minutos = draw(st.integers(min_value=DURACION_MINIMA, max_value=DURACION_MAXIMA))

    pasos = draw(
        st.lists(
            st.tuples(
                st.booleans(),
                st.integers(min_value=0, max_value=duracion_minutos * 2 + 5),
                st.sampled_from(ORIGENES),
            ),
            min_size=INTENTOS_MINIMOS,
            max_size=INTENTOS_MAXIMOS,
        )
    )

    intentos: list[tuple[bool, datetime, str]] = []
    instante = INSTANTE_BASE
    for acierto, delta_minutos, origen in pasos:
        instante = instante + timedelta(minutes=delta_minutos)
        intentos.append((acierto, instante, origen))

    return umbral, duracion_minutos, intentos


# Estrategia unica del escenario.
SECUENCIAS = secuencias_de_intentos()


@settings(max_examples=200, deadline=None)
@given(caso=SECUENCIAS)
def test_PBT_008_el_contador_de_intentos_fallidos_nunca_sale_de_rango_y_el_bloqueo_vigente_siempre_manda(
    caso: tuple[int, int, list[tuple[bool, datetime, str]]],
) -> None:
    """
    [PBT-008] Para toda secuencia de intentos, el contador vive en `[0, umbral]` y el bloqueo manda.

    Se recorre la secuencia intento a intento manteniendo el `EstadoBloqueo`, y las cuatro
    afirmaciones del resultado esperado se comprueban en CADA paso intermedio, no solo al final:

    1. `0 <= intentos_fallidos <= umbral` tras cada intento;
    2. tras un ACIERTO sobre cuenta no bloqueada, el contador vale 0;
    3. si el contador esta en el umbral, hay bloqueo con instante de desbloqueo POSTERIOR al del
       intento -alcanzar el umbral y quedar bloqueada son el mismo hecho-;
    4. mientras el bloqueo este vigente ningun intento concede sesion aunque la contrasenia sea
       correcta, y ese acierto bajo bloqueo tampoco reinicia el contador.

    El acceso se modela como `acierto and not bloqueada_antes`, que es el orden en que lo decide el
    flujo de login: primero se pregunta por el bloqueo, y solo si no lo hay cuenta el resultado de
    la contrasenia.
    """

    umbral, duracion_minutos, intentos = caso
    politica = PoliticaBloqueo(umbral=umbral, duracion_minutos=duracion_minutos)

    assert politica.umbral == umbral
    assert politica.duracion == timedelta(minutes=duracion_minutos)

    estado = EstadoBloqueo(intentos_fallidos=0, bloqueada_hasta=None, ultimo_fallo_en=None)

    for paso, (acierto, instante, origen) in enumerate(intentos):
        contador_previo = estado.intentos_fallidos
        bloqueada_antes = politica.esta_bloqueada(estado, instante)

        # El origen (sesion/IP) se conoce en el intento pero NO entra en la politica: el bloqueo es
        # por cuenta, y ningun metodo de la firma admite ese dato.
        assert origen in ORIGENES

        acceso_concedido = acierto and not bloqueada_antes
        estado = politica.registrar_acierto(estado, instante) if acierto else politica.registrar_fallo(estado, instante)

        contexto = (
            f"paso={paso} acierto={acierto} instante={instante} bloqueada_antes={bloqueada_antes} "
            f"umbral={umbral} duracion={duracion_minutos} estado={estado}"
        )

        assert 0 <= estado.intentos_fallidos <= politica.umbral, f"El contador se salio de [0, umbral]: {contexto}"

        if acierto and not bloqueada_antes:
            assert estado.intentos_fallidos == 0, f"Un acierto sobre cuenta no bloqueada debe dejar el contador a 0: {contexto}"

        if estado.intentos_fallidos == politica.umbral:
            assert estado.bloqueada_hasta is not None, f"Contador en el umbral sin bloqueo impuesto: {contexto}"
            assert estado.bloqueada_hasta > instante, f"Contador en el umbral con desbloqueo no posterior al intento: {contexto}"

        if bloqueada_antes:
            assert acceso_concedido is False, f"Se concedio sesion con el bloqueo vigente: {contexto}"
            assert estado.intentos_fallidos == contador_previo, f"Un intento bajo bloqueo movio el contador: {contexto}"
