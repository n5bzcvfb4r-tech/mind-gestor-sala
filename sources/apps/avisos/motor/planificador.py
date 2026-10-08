"""
Planificador EN PROCESO del motor de avisos: el que pone la cola a trabajar (ARC-014, REQ-132).

Que hace este modulo
--------------------
Levanta un `BackgroundScheduler` de APScheduler dentro del propio proceso servidor y le cuelga los
DOS trabajos periodicos del motor: el despacho del lote (`MotorAvisos.procesar_lote`) y la
recuperacion de solicitudes atascadas en `ENVIANDO` (`MotorAvisos.recuperar_atascados`). Aqui no se
decide nada del ciclo de vida de un aviso: eso es del despachador. Este modulo solo responde a
CUANDO se ejecuta cada fase y a COMO sobrevive el hilo de fondo a los fallos.

Por que en proceso y no un broker externo
-----------------------------------------
El despliegue del producto no contempla Celery ni Redis: el motor es una cola en BASE DE DATOS
(patron outbox) y el unico consumidor necesario es un temporizador dentro del proceso servidor.
APScheduler aporta exactamente eso, sin infraestructura adicional.

Las tres reglas que hacen que esto sea operable
-----------------------------------------------
1. NINGUNA excepcion escapa de un job. APScheduler, ante un job que lanza, inunda el log con el
   traceback de cada tick y, con ciertos ejecutores, acaba desactivando el trabajo. Los dos metodos
   de job envuelven su cuerpo en `try/except Exception` y devuelven el valor neutro. Esa es la razon
   de que una caida temporal de la base no mate el motor: el siguiente tick vuelve a intentarlo.
2. Cada job CIERRA sus conexiones al terminar. Un hilo de fondo mantiene su propia conexion de
   Django por hilo; si no se cierra, cada ciclo deja una sesion Oracle colgada y el pool se agota.
3. El arranque es IDEMPOTENTE. Con el autoreloader de `runserver` y con varios workers de gunicorn,
   `AppConfig.ready()` se ejecuta mas de una vez por proceso; sin el guardia de `_LOCK` habria
   varios planificadores disparando el mismo lote a la vez.

Varias REPLICAS del contenedor siguen siendo seguras: cada proceso tiene su planificador, pero el
reparto del trabajo lo arbitra la base, porque `tomar_pendientes` toma el lote con bloqueo
(`FOR UPDATE SKIP LOCKED`) y marca `ENVIANDO` con el `locked_by` del worker.
"""

import atexit
import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from django.db import connections

from apps.avisos.motor.configuracion import ConfiguracionMotorAvisos, configuracion_motor
from apps.avisos.motor.despachador import MotorAvisos, ResumenCiclo

logger = logging.getLogger(__name__)

#: Identificadores ESTABLES de los dos trabajos periodicos. Al ser estables, un segundo
#: `add_job` con el mismo id reemplaza al anterior en lugar de duplicar el disparo.
JOB_DESPACHO = "avisos_despacho"
JOB_RECUPERACION = "avisos_recuperacion"

#: Un unico ciclo en vuelo por trabajo: si un lote se alarga mas que el intervalo, el tick
#: siguiente se descarta en vez de solaparse. `coalesce` ademas colapsa los ticks atrasados en
#: uno solo, de modo que una pausa larga del proceso no provoca una rafaga de ejecuciones.
MAX_INSTANCIAS_POR_JOB = 1

# --- Mensajes de traza ---------------------------------------------------
TRAZA_DESACTIVADO = "Planificador del motor de avisos no arrancado: el motor esta desactivado por configuracion"
TRAZA_YA_ACTIVO = "Planificador del motor de avisos ya estaba activo en este proceso: no se arranca un segundo"
TRAZA_ARRANCADO = "Planificador del motor de avisos arrancado"
TRAZA_DETENIDO = "Planificador del motor de avisos detenido"
TRAZA_FALLO_DETENER = "Fallo al detener el planificador del motor de avisos: se da por detenido"
TRAZA_FALLO_DESPACHO = "Fallo del ciclo de despacho de avisos: el trabajo sigue planificado y reintentara en el proximo tick"
TRAZA_FALLO_RECUPERACION = "Fallo del ciclo de recuperacion de avisos: el trabajo sigue planificado y reintentara en el proximo tick"

#: Instancia unica del planificador en este proceso y su cerrojo. El cerrojo protege el
#: `comprobar y crear` de `iniciar_planificador` frente a dos hilos que llamen a la vez.
_planificador: "PlanificadorMotorAvisos | None" = None
_LOCK = threading.Lock()


class PlanificadorMotorAvisos:
    """
    Temporizador en proceso que dispara las dos fases del motor de avisos.

    El motor y la configuracion son INYECTABLES para poder planificar contra un doble en pruebas y
    verificaciones sin tocar la base; en produccion se resuelven de forma PEREZOSA en el primer uso,
    nunca al importar el modulo, porque `AppConfig.ready()` importa este fichero antes de que haya
    ninguna conexion abierta.
    """

    def __init__(self, *, motor: MotorAvisos | None = None, config: ConfiguracionMotorAvisos | None = None) -> None:
        self._motor = motor
        self._config = config
        self._scheduler = BackgroundScheduler()

    # --- Dependencias perezosas -------------------------------------------

    @property
    def config(self) -> ConfiguracionMotorAvisos:
        """Parametros de operacion del motor; se leen una sola vez y se conservan mientras viva el planificador."""

        if self._config is None:
            self._config = configuracion_motor()
        return self._config

    @property
    def motor(self) -> MotorAvisos:
        """
        Motor de avisos; se construye en el primer uso (sus puertos exigen `django.setup()` hecho).

        AQUI se inyecta el compositor del aviso de alta (AVI-02), porque este es el UNICO sitio del
        producto donde se construye el motor real: sin el, el despachador encontraria la solicitud
        sin asunto ni cuerpo, supondria el contenido incompleto y la suprimiria con
        `COMPOSICION_INCOMPLETA`. El import es PEREZOSO, dentro de la propiedad, igual que el resto
        del arranque, para no arrastrar los modelos antes de que el registro de apps este listo.

        Y AQUI se inyecta tambien la ENTREGA COLECTIVA del aviso de alta (REQ-133), por la misma
        razon y en el mismo sitio: el aviso de alta se encola con `recipient_email` a NULO porque su
        colectivo se resuelve en el instante del envio, de modo que sin esta inyeccion el despachador
        no encontraria destinatario alguno y suprimiria el aviso con `NO_RECIPIENTS`, es decir, el
        equipo de mantenimiento no recibiria nunca el correo del alta.
        """

        if self._motor is None:
            from apps.avisos.alta.composicion import compositor_por_defecto
            from apps.avisos.alta.entrega import entrega_alta_por_defecto

            self._motor = MotorAvisos(
                config=self._config,
                compositor=compositor_por_defecto(),
                entrega_colectiva=entrega_alta_por_defecto(),
            )
        return self._motor

    @property
    def activo(self) -> bool:
        """Indica si el scheduler de fondo esta corriendo en este proceso."""

        return bool(self._scheduler.running)

    # --- Ciclo de vida ------------------------------------------------------

    def iniciar(self) -> bool:
        """
        Planifica los dos trabajos periodicos y arranca el hilo de fondo.

        No arranca nada si el motor esta desactivado por configuracion (`AVISOS_MOTOR['habilitado']`):
        es la palanca que permite desplegar el producto sin consumir la cola, por ejemplo en un
        entorno de solo lectura o mientras se diagnostica un problema de correo.

        Returns:
            bool: `True` si el planificador ha arrancado en esta llamada; `False` si estaba
            desactivado por configuracion o si ya estaba corriendo.
        """

        if self.activo:
            logger.info(TRAZA_YA_ACTIVO, extra={"data": {"jobs": [trabajo.id for trabajo in self._scheduler.get_jobs()]}})
            return False

        config = self.config
        if not config.habilitado:
            logger.info(TRAZA_DESACTIVADO, extra={"data": {"habilitado": config.habilitado}})
            return False

        self._scheduler.add_job(
            self.ejecutar_ciclo_despacho,
            trigger=IntervalTrigger(seconds=config.intervalo_despacho_segundos),
            id=JOB_DESPACHO,
            name="Despacho de la cola de avisos por correo",
            max_instances=MAX_INSTANCIAS_POR_JOB,
            coalesce=True,
            replace_existing=True,
        )
        self._scheduler.add_job(
            self.ejecutar_ciclo_recuperacion,
            trigger=IntervalTrigger(seconds=config.intervalo_recuperacion_segundos),
            id=JOB_RECUPERACION,
            name="Recuperacion de avisos atascados en ENVIANDO",
            max_instances=MAX_INSTANCIAS_POR_JOB,
            coalesce=True,
            replace_existing=True,
        )
        self._scheduler.start()

        logger.info(
            TRAZA_ARRANCADO,
            extra={
                "data": {
                    "intervalo_despacho_segundos": config.intervalo_despacho_segundos,
                    "intervalo_recuperacion_segundos": config.intervalo_recuperacion_segundos,
                    "tamano_lote": config.tamano_lote,
                    "identificador_worker": config.identificador_worker,
                }
            },
        )
        return True

    def detener(self) -> None:
        """
        Para el hilo de fondo sin esperar a que termine el ciclo en vuelo.

        `wait=False` es deliberado: el apagado no puede quedarse colgado detras de un envio SMTP
        lento. Lo que quede a medias se queda en `ENVIANDO` y lo rescata la ventana de recuperacion.
        Tolera que el scheduler ya estuviese parado, porque esto se invoca tambien desde `atexit`.
        """

        try:
            self._scheduler.shutdown(wait=False)
        except Exception:  # el scheduler ya estaba parado o nunca llego a arrancar
            logger.debug(TRAZA_FALLO_DETENER, exc_info=True)
            return
        logger.info(TRAZA_DETENIDO, extra={"data": {"activo": self.activo}})

    # --- Trabajos periodicos ------------------------------------------------

    def ejecutar_ciclo_despacho(self) -> ResumenCiclo:
        """
        Trabajo periodico de despacho: consume un lote de la cola (REQ-132).

        NINGUNA excepcion puede escapar de aqui: APScheduler trazaria el fallo en cada tick y el
        motor quedaria inservible ante una caida temporal de la base. Un fallo se traza y se
        devuelve un resumen vacio; el siguiente tick vuelve a intentarlo.

        Returns:
            ResumenCiclo: recuento del ciclo, o un resumen vacio si el ciclo fallo.
        """

        try:
            return self.motor.procesar_lote()
        except Exception:
            logger.exception(TRAZA_FALLO_DESPACHO, extra={"data": {"job_id": JOB_DESPACHO}})
            return ResumenCiclo()
        finally:
            self._cerrar_conexiones()

    def ejecutar_ciclo_recuperacion(self) -> int:
        """
        Trabajo periodico de recuperacion: devuelve a `PENDIENTE` lo atascado en `ENVIANDO`.

        Misma garantia que el despacho: ninguna excepcion escapa del job. Si el ciclo falla se
        devuelve `0` recuperados y se reintenta en el proximo tick.

        Returns:
            int: numero de solicitudes devueltas a `PENDIENTE`, o `0` si el ciclo fallo.
        """

        try:
            return self.motor.recuperar_atascados()
        except Exception:
            logger.exception(TRAZA_FALLO_RECUPERACION, extra={"data": {"job_id": JOB_RECUPERACION}})
            return 0
        finally:
            self._cerrar_conexiones()

    # --- Utilidades internas ------------------------------------------------

    @staticmethod
    def _cerrar_conexiones() -> None:
        """
        Cierra las conexiones de Django del hilo del job.

        Django abre una conexion POR HILO y solo la recicla en la frontera de la peticion HTTP, que
        aqui no existe: un hilo de fondo que no cierra deja una sesion Oracle colgada por ciclo y
        acaba agotando el pool del servidor. Va en un `finally` para que tambien se cierre cuando el
        ciclo termina en error, que es justo cuando la conexion suele quedar inutilizable.
        """

        try:
            connections.close_all()
        except Exception:  # cerrar conexiones nunca puede ser el motivo de que falle un job
            logger.debug("No se han podido cerrar las conexiones del hilo del planificador de avisos", exc_info=True)


def iniciar_planificador() -> PlanificadorMotorAvisos | None:
    """
    Arranca el planificador del motor en este proceso, UNA sola vez (composition root del motor).

    Es IDEMPOTENTE: la instancia viva se guarda en una variable de modulo protegida por `_LOCK` y,
    si ya hay una activa, se devuelve esa misma sin crear un segundo scheduler. Hace falta porque
    `AppConfig.ready()` se ejecuta mas de una vez por proceso (autoreloader de `runserver`, forks de
    gunicorn) y dos planificadores en el mismo proceso dispararian el mismo lote en paralelo.

    Al arrancar registra `atexit.register(detener_planificador)`: sin ello el hilo de fondo
    mantendria el proceso vivo y el contenedor no terminaria al recibir la senal de parada.

    Returns:
        PlanificadorMotorAvisos | None: el planificador activo, o `None` si el motor esta
        desactivado por configuracion y no se ha arrancado nada.
    """

    global _planificador

    with _LOCK:
        if _planificador is not None and _planificador.activo:
            return _planificador

        planificador = PlanificadorMotorAvisos()
        if not planificador.iniciar():
            return None

        _planificador = planificador
        atexit.register(detener_planificador)
        return _planificador


def detener_planificador() -> None:
    """
    Detiene el planificador de este proceso, si lo hay, y suelta la referencia de modulo.

    Es idempotente y no lanza: lo invoca `atexit` durante el apagado del proceso, donde una
    excepcion solo serviria para ensuciar la salida.
    """

    global _planificador

    with _LOCK:
        if _planificador is None:
            return
        _planificador.detener()
        _planificador = None


def planificador_actual() -> PlanificadorMotorAvisos | None:
    """Devuelve el planificador vivo en este proceso, o `None` si no se ha arrancado ninguno."""

    return _planificador


__all__ = [
    "JOB_DESPACHO",
    "JOB_RECUPERACION",
    "MAX_INSTANCIAS_POR_JOB",
    "PlanificadorMotorAvisos",
    "detener_planificador",
    "iniciar_planificador",
    "planificador_actual",
]
