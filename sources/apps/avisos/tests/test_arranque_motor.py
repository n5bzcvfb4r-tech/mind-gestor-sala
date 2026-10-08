"""
Smoke de import y arranque del motor de avisos (ARC-014).

Acredita el Definition of Done de la unidad de registro: la app `apps.avisos` esta dada de alta en
el composition root (`config/settings.py`), el composition root sigue importando despues del alta,
todos los modulos del motor se importan sin excepcion, la configuracion del motor se construye con
lo que hay en settings, `AppConfig.ready()` NO deja un planificador vivo bajo pytest y el motor se
puede instanciar.

NINGUNA de estas pruebas necesita base de datos: todas las dependencias del motor (repositorio y
transporte) se resuelven de forma PEREZOSA en el primer uso, de modo que importar los modulos y
construir `MotorAvisos` no abre ninguna conexion. Por eso aqui no hay `django_db` ni se invoca a
ningun metodo de ciclo del motor, que si consultaria Oracle.
"""

import importlib

from django.apps import apps as registro_de_apps
from django.conf import settings

from apps.avisos.apps import AvisosConfig
from apps.avisos.motor.configuracion import ConfiguracionMotorAvisos, configuracion_motor
from apps.avisos.motor.despachador import MotorAvisos
from apps.avisos.motor.planificador import planificador_actual

#: Modulos del motor que el proceso servidor arrastra al arrancar. Si cualquiera de ellos deja de
#: importar (ciclo de imports, simbolo movido, acceso a modelos a nivel de modulo), el arranque del
#: contenedor se cae y esta lista es lo primero que lo detecta.
MODULOS_DEL_MOTOR = [
    "apps.avisos.motor.estados",
    "apps.avisos.motor.errores",
    "apps.avisos.motor.claves",
    "apps.avisos.motor.configuracion",
    "apps.avisos.motor.calendario",
    "apps.avisos.motor.reintentos",
    "apps.avisos.motor.repositorio",
    "apps.avisos.motor.transporte",
    "apps.avisos.motor.outbox",
    "apps.avisos.motor.despachador",
    "apps.avisos.motor.planificador",
]


def test_la_app_de_avisos_esta_registrada_en_el_composition_root() -> None:
    """La app debe estar en INSTALLED_APPS y resolverse como `AvisosConfig` con su ruta de paquete."""

    assert "apps.avisos" in settings.INSTALLED_APPS, (
        f"'apps.avisos' no esta en INSTALLED_APPS de config/settings.py: {settings.INSTALLED_APPS}"
    )

    configuracion_de_la_app = registro_de_apps.get_app_config("avisos")

    assert isinstance(configuracion_de_la_app, AvisosConfig), (
        f"el registro de apps resuelve 'avisos' como {type(configuracion_de_la_app).__name__} en vez de AvisosConfig"
    )
    assert configuracion_de_la_app.name == "apps.avisos", (
        f"AvisosConfig.name es '{configuracion_de_la_app.name}' y deberia ser 'apps.avisos'"
    )


def test_el_composition_root_importa_sin_excepciones_con_la_app_de_avisos(settings) -> None:
    """
    Dar de alta la app no puede romper el import de las urls ni del punto de entrada WSGI.

    `config/wsgi.py` termina invocando el gate de catalogos de `apps.core.arranque`, que CONSULTA la
    base de datos; se desactiva con su propia palanca de configuracion para que esta prueba mida lo
    que le toca (que el modulo WSGI se importa con la app de avisos registrada) y no la
    disponibilidad de Oracle. El `get_wsgi_application()` del modulo si se ejecuta de verdad.
    """

    settings.VERIFICAR_CATALOGOS_AL_ARRANQUE = False

    urls = importlib.import_module("config.urls")
    wsgi = importlib.import_module("config.wsgi")

    assert urls.urlpatterns, "config.urls.urlpatterns ha quedado vacio: el composition root no expone ninguna ruta"
    assert wsgi.application is not None, "config.wsgi no expone el callable 'application' del punto de entrada WSGI"


def test_todos_los_modulos_del_motor_de_avisos_importan_sin_excepciones() -> None:
    """Cada modulo del motor debe importarse en frio, sin base de datos ni efectos al importar."""

    for nombre in MODULOS_DEL_MOTOR:
        modulo = importlib.import_module(nombre)

        assert modulo.__name__ == nombre, f"el import de '{nombre}' ha resuelto al modulo '{modulo.__name__}'"


def test_la_configuracion_del_motor_se_construye_sin_base_de_datos() -> None:
    """`configuracion_motor()` solo lee settings, asi que debe devolver parametros validos en frio."""

    config = configuracion_motor()

    assert isinstance(config, ConfiguracionMotorAvisos), (
        f"configuracion_motor() ha devuelto {type(config).__name__} en vez de ConfiguracionMotorAvisos"
    )
    assert config.tamano_lote >= 1, f"el tamano de lote es {config.tamano_lote}: con menos de 1 la cola nunca avanzaria"
    assert config.backoff_minutos, "backoff_minutos esta vacio: no habria politica de reintentos que aplicar"
    assert config.identificador_worker, "identificador_worker esta vacio: el lock de un aviso no diria quien lo tiene"


def test_ready_no_arranca_el_planificador_bajo_pytest() -> None:
    """Bajo pytest, `ready()` omite el arranque: un hilo de fondo consumiendo la cola haria la suite no determinista."""

    planificador = planificador_actual()

    assert planificador is None, (
        f"hay un planificador vivo en el proceso de pruebas ({planificador!r}): consumiria la cola por su cuenta"
    )


def test_el_motor_de_avisos_se_instancia_sin_tocar_la_base_de_datos() -> None:
    """Construir `MotorAvisos` no resuelve sus puertos, asi que debe exponer sus fases sin conexion abierta."""

    motor = MotorAvisos()

    for fase in ("procesar_lote", "procesar_aviso", "recuperar_atascados", "reenviar"):
        assert callable(getattr(motor, fase, None)), f"MotorAvisos no expone '{fase}' como invocable"
