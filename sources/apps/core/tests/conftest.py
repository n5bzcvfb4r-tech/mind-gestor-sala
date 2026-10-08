"""
Fixtures compartidas de las pruebas de `apps.core`.

Aqui solo vive lo transversal: el actor de sesion de prueba, su publicacion en el
`ContextVar` real del servicio y la deteccion del engine Docker. Las fixtures de base
de datos (contenedor Oracle 23ai con Testcontainers) viven en el conftest del paquete
de pruebas de integracion, no en este.
"""

import os
from collections.abc import Iterator

import pytest

from apps.core.contexto import AlcanceDatos, ContextoSesion, contexto_de_sesion

#: Motivo unico del `skipif` de las pruebas que necesitan un contenedor real.
MOTIVO_SIN_DOCKER = "Requiere un engine Docker alcanzable para levantar Oracle 23ai con Testcontainers."


@pytest.fixture
def actor_sesion() -> ContextoSesion:
    """Actor de prueba: tecnico de mantenimiento con alcance de datos total."""

    return ContextoSesion(
        user_id=7,
        role_code="TECNICO_MANTENIMIENTO",
        session_id="11111111-1111-4111-8111-111111111111",
        data_scope=AlcanceDatos.ALL.value,
        display_name="Tecnico de prueba",
    )


@pytest.fixture
def sesion_activa(actor_sesion: ContextoSesion) -> Iterator[ContextoSesion]:
    """Publica el actor de prueba en el contexto de sesion real durante la prueba."""

    with contexto_de_sesion(actor_sesion) as contexto:
        yield contexto


@pytest.fixture(scope="session")
def docker_disponible() -> bool:
    """Indica si hay un engine Docker alcanzable; nunca falla, solo devuelve el booleano."""

    try:
        import docker  # type: ignore[import-not-found]

        cliente = docker.from_env()
        cliente.ping()
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
