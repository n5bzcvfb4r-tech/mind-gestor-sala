"""
Fixtures del paquete de pruebas del motor de avisos.

Aqui vive el andamiaje que sostiene las pruebas de integracion del motor de cola
(`aviso_correo` como outbox) contra Oracle Database 23ai Free REAL: la deteccion del
engine Docker, el contenedor de Oracle levantado con Testcontainers, la aplicacion del
changelog de Liquibase y la sobrescritura de `django_db_setup` que apunta Django a ese
contenedor sin ejecutar `migrate`.

Por que se REPLICA el patron de `apps/core/tests` en vez de importarlo
---------------------------------------------------------------------
Las fixtures de contenedor de `apps.core` NO viven en su `conftest.py`, sino dentro del
modulo de pruebas `apps/core/tests/test_persistencia_oracle.py`. Importar fixtures desde
un modulo de TEST hacia un `conftest.py` de otro paquete es fragil: arrastra la coleccion
de aquel modulo (sus `pytestmark`, sus constantes y sus propias pruebas) a este paquete y
acopla el arranque de estas pruebas a cambios en un fichero de pruebas ajeno. Se replica
por tanto lo minimo imprescindible, manteniendo IDENTICAS la imagen (`gvenzl/oracle-free:23-slim`),
las credenciales y el mecanismo de aplicacion del esquema, que son el contrato real del
entorno de prueba del proyecto.

PROHIBIDO degradar el motor a SQLite, H2 o cualquier doble en memoria: un `dict` en memoria
no acredita persistencia ni concurrencia, y el bloqueo FIFO del motor solo se puede acreditar
contra el `SELECT ... FOR UPDATE SKIP LOCKED` de un Oracle real. Si no hay engine Docker
alcanzable, las pruebas se SALTAN con `MOTIVO_SIN_DOCKER`; nunca cambian de backend.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connections

import apps.avisos
from apps.avisos.motor.configuracion import ConfiguracionMotorAvisos, configuracion_motor

#: Motivo unico del `skipif` de las pruebas que necesitan un contenedor real.
MOTIVO_SIN_DOCKER = "Requiere un engine Docker alcanzable para levantar Oracle 23ai con Testcontainers."

#: Imagenes y credenciales: las MISMAS que `local/docker-compose.yml` y el manifiesto.
IMAGEN_ORACLE = "gvenzl/oracle-free:23-slim"
IMAGEN_LIQUIBASE = "liquibase/liquibase:4.31-alpine"
ALIAS_RED_ORACLE = "oracle-facilities"
SERVICIO_ORACLE = "facilities"
USUARIO_APP = "liquibase"
CLAVE_APP = "entornodev"
CLAVE_ADMIN = "entornodev"
PUERTO_ORACLE = 1521
#: Oracle 23ai tarda en arrancar la primera vez; margen amplio a proposito.
ESPERA_ARRANQUE_ORACLE = 300
LOG_ORACLE_LISTO = "DATABASE IS READY TO USE!"

#: Identificador de worker fijo en las pruebas: el real lleva host y PID, que cambian en cada CI.
IDENTIFICADOR_WORKER_PRUEBA = "worker-de-prueba"


def hay_docker() -> bool:
    """Indica si hay un engine Docker alcanzable; nunca falla, solo devuelve el booleano."""

    try:
        import docker  # type: ignore[import-not-found]

        docker.from_env().ping()
        return True
    except Exception:
        pass

    host = os.environ.get("DOCKER_HOST", "").strip()
    if not host:
        return False

    try:
        import docker  # type: ignore[import-not-found]

        docker.DockerClient(base_url=host).ping()
        return True
    except Exception:
        return False


def _resolver_raiz_repo() -> Path:
    """Resuelve la raiz del repositorio subiendo directorios desde `apps.avisos`.

    Nunca se codifica una ruta absoluta: se parte del fichero del paquete
    (`apps/avisos/__init__.py`) y se sube por sus `parents` hasta el directorio que
    contiene el `project-manifest.json` del repo. Si no apareciese (layout movido), se
    cae al cuarto ancestro, que es la raiz en el layout actual
    (`<raiz>/sources/apps/avisos/__init__.py`).
    """

    paquete = Path(apps.avisos.__file__).resolve()
    for ancestro in paquete.parents:
        if (ancestro / "project-manifest.json").is_file():
            return ancestro
    return paquete.parents[3]


#: Raiz del repositorio montada dentro del contenedor de Liquibase.
RAIZ_REPO = _resolver_raiz_repo()


@pytest.fixture(scope="session")
def docker_disponible() -> bool:
    """Indica si hay un engine Docker alcanzable; nunca falla, solo devuelve el booleano."""

    return hay_docker()


@pytest.fixture(scope="session")
def red_docker() -> Iterator[object]:
    """Red Docker efimera para que Oracle y Liquibase se vean por alias de red."""

    if not hay_docker():
        pytest.skip(MOTIVO_SIN_DOCKER)

    from testcontainers.core.network import Network

    with Network() as red:
        yield red


@pytest.fixture(scope="session")
def oracle_contenedor(red_docker: object) -> Iterator[object]:
    """Levanta Oracle 23ai Free REAL con la imagen y las credenciales del proyecto.

    Se une a `red_docker` con el alias `oracle-facilities`, el mismo nombre de host que
    `local/facilities.properties` usa en la URL JDBC, de modo que el contenedor de
    Liquibase pueda alcanzarlo sin tocar la configuracion real. El puerto 1521 queda
    expuesto al anfitrion para que Django se conecte desde el proceso de test.
    """

    from testcontainers.core.waiting_utils import wait_for_logs
    from testcontainers.oracle import OracleDbContainer

    contenedor = OracleDbContainer(
        image=IMAGEN_ORACLE,
        oracle_password=CLAVE_ADMIN,
        username=USUARIO_APP,
        password=CLAVE_APP,
        dbname=SERVICIO_ORACLE,
        port=PUERTO_ORACLE,
        network=red_docker,
        network_aliases=[ALIAS_RED_ORACLE],
    )
    contenedor.with_exposed_ports(PUERTO_ORACLE)

    with contenedor as oracle:
        # Espera explicita por el log de arranque de la imagen: hasta que no aparece
        # `DATABASE IS READY TO USE!` el listener acepta conexiones a medias.
        wait_for_logs(oracle, LOG_ORACLE_LISTO, timeout=ESPERA_ARRANQUE_ORACLE)
        yield oracle


@pytest.fixture(scope="session")
def esquema_aplicado(oracle_contenedor: object, red_docker: object) -> Iterator[bool]:
    """Aplica el changelog REAL de Liquibase sobre el Oracle del contenedor.

    Ejecuta `liquibase/liquibase:4.31-alpine` (su imagen ya trae el driver ojdbc) montando
    la raiz del repo en `/liquibase/project` y usando el mismo `local/facilities.properties`
    y `sources/facilities/master.xml` que el entorno local. Asi el esquema bajo prueba es el
    DDL de produccion y no una recreacion desde los modelos (las entidades son
    `managed = False`, ARC-016). Si `update` no termina con codigo 0, la prueba FALLA con los
    logs del contenedor: un error de Liquibase nunca se ignora en silencio.
    """

    from testcontainers.core.container import DockerContainer

    comando = [
        "--defaultsFile=local/facilities.properties",
        f"--url=jdbc:oracle:thin:@//{ALIAS_RED_ORACLE}:{PUERTO_ORACLE}/{SERVICIO_ORACLE}",
        f"--username={USUARIO_APP}",
        f"--password={CLAVE_APP}",
        "update",
    ]

    liquibase = DockerContainer(
        IMAGEN_LIQUIBASE,
        command=comando,
        network=red_docker,
        volumes=[(str(RAIZ_REPO), "/liquibase/project", "rw")],
        working_dir="/liquibase/project",
    )

    with liquibase as ejecucion:
        interno = ejecucion.get_wrapped_container()
        resultado = interno.wait(timeout=ESPERA_ARRANQUE_ORACLE)
        codigo_salida = resultado.get("StatusCode", 1) if isinstance(resultado, dict) else resultado
        salida = interno.logs().decode("utf-8", errors="replace")

        if codigo_salida != 0:
            pytest.fail(
                f"Liquibase `update` fallo con codigo {codigo_salida} sobre "
                f"{IMAGEN_LIQUIBASE} (changelog sources/facilities/master.xml).\n"
                f"--- logs del contenedor ---\n{salida}"
            )

        yield True


@pytest.fixture(scope="session")
def django_db_setup(oracle_contenedor: object, esquema_aplicado: bool, django_db_blocker) -> Iterator[None]:
    """Sobreescribe la fixture de pytest-django para apuntar al Oracle del contenedor.

    Todos los modelos del inventario T.5 son `managed = False` (el DDL lo gobierna
    Liquibase, ARC-016), asi que esta fixture NO ejecuta `migrate` ni crea ninguna tabla
    desde Django: el esquema ya esta puesto por `esquema_aplicado`. Tampoco se crea una
    base de pruebas (`TEST["CREATE_DB"] = False`); se trabaja contra el esquema real del
    contenedor.

    El host y el puerto se leen del contenedor YA ARRANCADO (los mapeados al anfitrion),
    nunca se codifican: Testcontainers asigna un puerto efimero.
    """

    anfitrion = oracle_contenedor.get_container_host_ip()
    puerto = oracle_contenedor.get_exposed_port(PUERTO_ORACLE)
    #: Formato `host:puerto/service_name` que entiende el backend Oracle de Django.
    nombre = f"{anfitrion}:{puerto}/{SERVICIO_ORACLE}"

    # Se cierran las conexiones heredadas antes de reapuntar el `settings`.
    connections.close_all()

    settings.DATABASES["default"] = {
        "ENGINE": "django.db.backends.oracle",
        "NAME": nombre,
        "USER": USUARIO_APP,
        "PASSWORD": CLAVE_APP,
        "HOST": "",
        "PORT": "",
        "ATOMIC_REQUESTS": False,
        "AUTOCOMMIT": True,
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": False,
        "OPTIONS": {},
        "TIME_ZONE": None,
        "TEST": {
            "CREATE_DB": False,
            "NAME": nombre,
            "USER": USUARIO_APP,
            "PASSWORD": CLAVE_APP,
            "CHARSET": None,
            "COLLATION": None,
            "MIGRATE": False,
            "MIRROR": None,
        },
    }

    connections.close_all()

    # Comprobacion temprana: si Django no puede hablar con el Oracle real, que el fallo
    # salga aqui y no disfrazado dentro del primer test.
    with django_db_blocker.unblock():
        connections["default"].ensure_connection()

    yield

    connections.close_all()


@pytest.fixture
def config_motor() -> ConfiguracionMotorAvisos:
    """Configuracion del motor apta para prueba, derivada de la REAL de `settings`.

    Se parte de `configuracion_motor()` (la misma que lee el codigo productivo) y solo se
    fijan dos cosas con `dataclasses.replace`, sin inventar una configuracion paralela:

    * `respetar_horario_laboral=False`: las pruebas no pueden depender de la hora ni del dia
      de la semana en que arranque el CI. La franja laboral se prueba aparte, con su propio
      reloj controlado, no de forma implicita en cada caso.
    * `identificador_worker="worker-de-prueba"`: el real lleva host y PID y cambia en cada
      ejecucion, lo que impediria afirmar sobre `aviso_correo.locked_by`.
    """

    return replace(
        configuracion_motor(),
        respetar_horario_laboral=False,
        identificador_worker=IDENTIFICADOR_WORKER_PRUEBA,
    )
