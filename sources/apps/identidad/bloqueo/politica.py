"""
Politica PURA del bloqueo temporal de cuenta por intentos fallidos (REQ-055, REQ-072).

Este modulo es dominio puro: no toca la base de datos, no conoce el ORM, no escribe logs y no
lee el reloj. Describe COMO evoluciona el estado de bloqueo de una cuenta ante un intento de
autenticacion acertado o fallido, y devuelve siempre un estado NUEVO; quien persiste ese
estado -el servicio- es otra unidad. Esa separacion es lo que permite probar la regla de los
cinco intentos y del vencimiento a los quince minutos sin base de datos y sin esperar.

EL RELOJ LO PONE QUIEN LLAMA, Y SIEMPRE ES EL DEL SERVIDOR
-----------------------------------------------------------
Todos los metodos reciben `ahora` como parametro en vez de leerlo: una politica que consulta
el reloj por dentro no se puede ejercitar sobre una linea temporal controlada. El instante lo
sella SIEMPRE el servidor con `apps.core.contexto.utc_now()` -nunca el cliente, que podria
adelantar su reloj para dar por vencido un bloqueo- y es un `datetime` NAIVE en UTC, igual que
el resto de marcas temporales del servicio y que las columnas `TIMESTAMP` del esquema. Mezclar
aware y naive aqui reventaria la comparacion `bloqueada_hasta > ahora`, que es justo la que
decide si se concede acceso.

LOS PARAMETROS SON CONFIGURACION, NO CONSTANTES DE NEGOCIO
-----------------------------------------------------------
El umbral de intentos y la duracion del bloqueo son un GAP declarado del RFP (REQ-055,
REQ-072): el valor inferido -5 intentos, 15 minutos- es una decision de operacion y debe poder
ajustarse por entorno. Por eso viven en `settings` (`BLOQUEO_UMBRAL_INTENTOS_FALLIDOS`,
`BLOQUEO_DURACION_MINUTOS`) y se leen PEREZOSAMENTE dentro del `__init__`, nunca a nivel de
modulo: leerlos al importar congelaria el valor en el primer import, romperia los tests que
los sobreescriben con `override_settings` y obligaria a `django.setup()` solo para importar
dominio puro.

ESTADO FISICO: TRES COLUMNAS DE `usuario`, SIN RENOMBRAR
---------------------------------------------------------
`EstadoBloqueo` es la proyeccion en memoria de tres columnas FISICAS de la tabla `usuario`
del esquema T.5 -`failed_password_attempts`, `locked_until` y `last_failed_attempt_at`-, y de
ninguna mas. Los nombres fisicos NO se renombran en ningun sitio: el mapeo vive aqui, en el
docstring del dataclass, para que nadie tenga que adivinarlo ni inventar una columna nueva.

INVARIANTE QUE GARANTIZAN ESTOS METODOS (lo verifica PBT-008)
---------------------------------------------------------------
Para CUALQUIER secuencia de aciertos y fallos en instantes crecientes:

(a) `0 <= intentos_fallidos <= umbral` en todo momento. El contador no baja de cero -la columna
    es `>= 0`- y no pasa del umbral: `registrar_fallo` lo satura con `min(...)` y los intentos
    contra una cuenta ya bloqueada no lo empujan, de modo que un atacante que insiste durante
    el bloqueo no puede inflar el contador ni encadenar bloqueos al vencer el primero.
(b) Tras un acierto sobre una cuenta NO bloqueada, `intentos_fallidos == 0`. Un inicio de sesion
    correcto cierra la racha de fallos (REQ-055 RN-04, AC-PWD-10).
(c) Si `intentos_fallidos == umbral`, entonces `bloqueada_hasta` es POSTERIOR al instante del
    intento: alcanzar el umbral y quedar bloqueada son el mismo hecho, no dos pasos.
(d) Mientras el bloqueo este vigente NINGUN intento concede acceso, aunque la contrasenia sea
    correcta: `registrar_acierto` bajo bloqueo no reinicia nada y el llamador sigue obligado a
    consultar `esta_bloqueada()` ANTES de dar por bueno el acceso.

EL VENCIMIENTO SE RESUELVE SOLO (REQ-055 RN-04, AC-PWD-10)
------------------------------------------------------------
No hay tarea programada que limpie bloqueos: `normalizar()` observa que `locked_until` ya paso
y deja el estado limpio -contador a 0 y sin bloqueo- en el siguiente intento. El desbloqueo por
tiempo no necesita intervencion manual ni proceso de fondo, y por tanto no puede quedarse a
medias si ese proceso no corre.

LA TRAZA DEL ULTIMO FALLO NO SE BORRA
--------------------------------------
Ni el acierto, ni el vencimiento, ni el desbloqueo administrativo tocan `ultimo_fallo_en`
(AC-RST-06): es evidencia de cuando ocurrio el ultimo intento fallido y sirve para investigar
un ataque DESPUES de que la cuenta vuelva a estar operativa. Lo que se reinicia es el contador,
que es la cuenta atras hacia el bloqueo, no el registro de lo que paso.

SECRETOS (REQ-063, REQ-076)
---------------------------
Por aqui no pasa la contrasenia intentada ni su hash: la politica solo sabe si un intento fue
acierto o fallo, y el `EstadoBloqueo` que entra y sale no tiene donde alojar material de
credencial (`frozen=True`, `slots=True`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class EstadoBloqueo:
    """
    Estado de bloqueo de una cuenta en un instante dado.

    Proyeccion EXACTA de tres columnas fisicas de la tabla `usuario` (esquema T.5), que NO se
    renombran en ninguna capa:

    - `intentos_fallidos`  -> `failed_password_attempts` (NUMBER, `>= 0`, contador de la racha
      de fallos consecutivos; se reinicia con un acierto o al vencer el bloqueo).
    - `bloqueada_hasta`    -> `locked_until` (TIMESTAMP nullable, instante NAIVE UTC en que el
      bloqueo expira; `None` significa «sin bloqueo», nunca «bloqueo infinito»).
    - `ultimo_fallo_en`    -> `last_failed_attempt_at` (TIMESTAMP nullable, traza del ultimo
      intento fallido; sobrevive al acierto, al vencimiento y al desbloqueo administrativo).

    Es inmutable y cerrada (`frozen=True`, `slots=True`): la politica devuelve siempre
    instancias NUEVAS y nadie puede mutar el estado a mitad de una decision ni colgarle
    atributos -por ejemplo, la contrasenia intentada- por el camino.
    """

    intentos_fallidos: int
    bloqueada_hasta: datetime | None
    ultimo_fallo_en: datetime | None


class PoliticaBloqueo:
    """
    Evaluador unico del bloqueo temporal por intentos fallidos (REQ-055, REQ-072).

    Sin estado mutable y sin dependencias de infraestructura: se instancia donde haga falta,
    aunque el camino previsto es que el servicio de bloqueo lo use como colaborador unico,
    tanto en la verificacion de login como en el desbloqueo administrativo. Teniendo UN solo
    evaluador, la regla de los cinco intentos no puede divergir entre la puerta que la aplica
    y la que la deshace.
    """

    def __init__(self, umbral: int | None = None, duracion_minutos: int | None = None) -> None:
        """
        Fija los parametros de la politica, tomandolos de `settings` si no se indican.

        La lectura de `django.conf.settings` ocurre AQUI y no al importar el modulo: asi la
        carga del modulo sigue siendo dominio puro -no exige `django.setup()`- y un
        `override_settings` en pruebas o un cambio de variable de entorno afecta a la siguiente
        instancia en vez de quedar congelado en la primera carga del proceso.

        Args:
            umbral: intentos fallidos consecutivos que disparan el bloqueo. `None` toma
                `settings.BLOQUEO_UMBRAL_INTENTOS_FALLIDOS`.
            duracion_minutos: minutos que dura el bloqueo. `None` toma
                `settings.BLOQUEO_DURACION_MINUTOS`.

        Raises:
            ValueError: si el umbral o la duracion son menores que 1. Un umbral de 0 bloquearia
                toda cuenta antes del primer intento y una duracion de 0 produciria un bloqueo
                ya vencido al nacer: en ambos casos es un defecto de CONFIGURACION, y tiene que
                fallar ruidosamente al construir la politica en vez de degradar en silencio la
                proteccion contra fuerza bruta.
        """

        if umbral is None or duracion_minutos is None:
            from django.conf import settings

            if umbral is None:
                umbral = int(settings.BLOQUEO_UMBRAL_INTENTOS_FALLIDOS)
            if duracion_minutos is None:
                duracion_minutos = int(settings.BLOQUEO_DURACION_MINUTOS)

        if umbral < 1:
            raise ValueError(f"El umbral de intentos fallidos debe ser >= 1; recibido {umbral}")
        if duracion_minutos < 1:
            raise ValueError(f"La duracion del bloqueo en minutos debe ser >= 1; recibida {duracion_minutos}")

        self.umbral: int = umbral
        self.duracion: timedelta = timedelta(minutes=duracion_minutos)

    def esta_bloqueada(self, estado: EstadoBloqueo, ahora: datetime) -> bool:
        """
        Indica si hay un bloqueo VIGENTE en el instante `ahora` (REQ-072).

        Vigente significa `locked_until` estrictamente POSTERIOR a `ahora`: en el instante
        exacto de la expiracion el bloqueo ya no retiene, porque «bloqueada hasta las 10:15»
        describe un intervalo que termina a las 10:15 y no uno que la incluye.

        Es la unica pregunta que debe hacerse el flujo de login ANTES de verificar la
        contrasenia: mientras devuelva `True` no se concede acceso aunque el secreto sea
        correcto (invariante (d)).
        """

        return estado.bloqueada_hasta is not None and estado.bloqueada_hasta > ahora

    def normalizar(self, estado: EstadoBloqueo, ahora: datetime) -> EstadoBloqueo:
        """
        Devuelve el estado saneado y con el bloqueo ya vencido retirado (REQ-055 RN-04, AC-PWD-10).

        Dos trabajos, y ninguno escribe en base:

        1. VENCIMIENTO AUTOMATICO. Si habia `bloqueada_hasta` y ya paso (`<= ahora`), el estado
           resultante queda con `intentos_fallidos = 0` y `bloqueada_hasta = None`. El
           vencimiento del bloqueo devuelve la cuenta a cero por si solo, sin intervencion
           manual ni tarea programada: si solo se retirase el bloqueo y el contador siguiera en
           el umbral, el primer fallo posterior volveria a bloquear y la cuenta quedaria presa
           de un bloqueo perpetuo de un intento.
        2. SANEADO DEL CONTADOR. En cualquier otro caso el estado se devuelve igual, pero con
           `intentos_fallidos` acotado a `[0, umbral]`. La columna es `>= 0` y el invariante no
           admite pasarse del umbral; un valor fuera de rango -una fila heredada, un umbral que
           se bajo por configuracion- se corrige en lectura en vez de propagarse.

        Es el PRIMER paso de `registrar_fallo` y `registrar_acierto`, de modo que ninguno de
        ellos tiene que repetir estas dos comprobaciones ni puede olvidarlas.
        """

        if estado.bloqueada_hasta is not None and estado.bloqueada_hasta <= ahora:
            return replace(estado, intentos_fallidos=0, bloqueada_hasta=None)

        return replace(estado, intentos_fallidos=max(0, min(estado.intentos_fallidos, self.umbral)))

    def registrar_fallo(self, estado: EstadoBloqueo, ahora: datetime) -> EstadoBloqueo:
        """
        Aplica un intento FALLIDO y devuelve el estado resultante (REQ-055, AC-PWD-09).

        Parte de `normalizar()`, asi que un bloqueo ya vencido se retira y el contador arranca
        de cero antes de contar este fallo.

        BLOQUEO VIGENTE: el estado normalizado se devuelve SIN tocar el contador. Un intento
        contra una cuenta ya bloqueada no suma: si sumara, un atacante que insiste durante los
        quince minutos dejaria el contador muy por encima del umbral y la cuenta encadenaria
        bloqueos al vencer el primero, convirtiendo una proteccion temporal en una denegacion
        de servicio contra el usuario legitimo. Tampoco se refresca `ultimo_fallo_en` ni se
        prorroga `bloqueada_hasta`: el bloqueo dura lo que dura desde que se impuso.

        SIN BLOQUEO: `intentos_fallidos = min(previos + 1, umbral)`, `ultimo_fallo_en = ahora` y,
        si con este fallo se alcanza el umbral, `bloqueada_hasta = ahora + duracion` (AC-PWD-09:
        `locked_until = now + 15 min` a la quinta verificacion fallida, con los valores por
        defecto). Alcanzar el umbral y quedar bloqueada son el MISMO hecho, en la misma
        transicion, nunca dos pasos separados (invariante (c)).
        """

        normalizado = self.normalizar(estado, ahora)
        if self.esta_bloqueada(normalizado, ahora):
            return normalizado

        intentos = min(normalizado.intentos_fallidos + 1, self.umbral)
        bloqueada_hasta = ahora + self.duracion if intentos >= self.umbral else normalizado.bloqueada_hasta

        return replace(normalizado, intentos_fallidos=intentos, bloqueada_hasta=bloqueada_hasta, ultimo_fallo_en=ahora)

    def registrar_acierto(self, estado: EstadoBloqueo, ahora: datetime) -> EstadoBloqueo:
        """
        Aplica un intento ACERTADO y devuelve el estado resultante (REQ-055 RN-04, AC-PWD-10).

        Parte de `normalizar()`, de modo que un bloqueo ya vencido se retira antes de nada.

        BLOQUEO VIGENTE: se devuelve el estado normalizado SIN cambio alguno. Acertar la
        contrasenia durante el bloqueo no concede acceso ni reinicia el contador (invariante
        (d)): si lo reiniciase, bastaria con conocer la contrasenia -o con que el titular
        legitimo la escribiera bien- para anular una medida que existe precisamente porque hubo
        una racha sospechosa de fallos. El llamador debe seguir respondiendo 423.

        SIN BLOQUEO: `intentos_fallidos = 0` y `bloqueada_hasta = None`. El acierto cierra la
        racha de fallos (invariante (b)).

        `ultimo_fallo_en` SE CONSERVA en ambos casos: es la traza del ultimo intento FALLIDO, no
        del acierto, y borrarla destruiria la unica pista de cuando ocurrio el ultimo ataque.
        """

        normalizado = self.normalizar(estado, ahora)
        if self.esta_bloqueada(normalizado, ahora):
            return normalizado

        return replace(normalizado, intentos_fallidos=0, bloqueada_hasta=None)

    def desbloquear(self, estado: EstadoBloqueo) -> EstadoBloqueo:
        """
        Levanta el bloqueo por decision ADMINISTRATIVA (REQ-075, AC-RST-06).

        Deja `intentos_fallidos = 0` y `bloqueada_hasta = None` sin mirar el reloj: a diferencia
        del vencimiento por tiempo, aqui no se evalua si el bloqueo seguia vigente -eso lo
        comprueba el servicio ANTES, para responder 409 `CuentaNoBloqueadaError` cuando no habia
        nada que levantar-, de modo que esta operacion no necesita `ahora` y es idempotente.

        `ultimo_fallo_en` se CONSERVA (AC-RST-06): el desbloqueo administrativo devuelve el
        acceso, no borra la evidencia de que hubo intentos fallidos. Quien investigue despues
        sigue pudiendo ver cuando fue el ultimo.
        """

        return replace(estado, intentos_fallidos=0, bloqueada_hasta=None)
