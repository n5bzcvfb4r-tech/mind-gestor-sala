"""
Contexto de sesion del servicio: fuente UNICA de la identidad de atribucion (REQ-064).

Quien ejecuta una operacion NO se lee nunca del payload de la peticion: se lee de aqui.
El contexto lo publica la capa de identidad (autenticacion de sesion opaca) a traves del
middleware `ContextoSesionMiddleware`, y vive en un `ContextVar`, por lo que es seguro
tanto entre hilos como entre tareas asincronas.

Aqui vive ademas `utc_now()`, la unica fuente de marca temporal del servicio.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from apps.core.errores import ContextoSesionNoDisponibleError


class AlcanceDatos(str, Enum):
    """Alcance de datos que el rol autoriza a consultar."""

    OWN = "OWN"
    ALL = "ALL"


@dataclass(frozen=True, slots=True)
class ContextoSesion:
    """
    Identidad efectiva de la peticion en curso.

    Es inmutable a proposito: nadie puede reescribir el actor a mitad de una operacion.
    """

    user_id: int
    role_code: str
    session_id: str | None = None
    data_scope: str = AlcanceDatos.OWN.value
    display_name: str | None = None


_CONTEXTO_SESION: ContextVar[ContextoSesion | None] = ContextVar("_CONTEXTO_SESION", default=None)


def establecer_contexto(contexto: ContextoSesion) -> Token:
    """Publica el contexto de sesion y devuelve el token necesario para restaurarlo."""

    return _CONTEXTO_SESION.set(contexto)


def limpiar_contexto(token: Token) -> None:
    """Restaura el contexto previo al token indicado; se llama SIEMPRE en un `finally`."""

    _CONTEXTO_SESION.reset(token)


def obtener_contexto() -> ContextoSesion | None:
    """Devuelve el contexto de sesion activo, o `None` si no hay ninguno."""

    return _CONTEXTO_SESION.get()


def contexto_requerido() -> ContextoSesion:
    """
    Devuelve el contexto de sesion activo o falla.

    Deny-by-default: sin identidad autenticada no hay atribucion posible, y preferimos
    romper la operacion antes que registrar un actor inventado.
    """

    contexto = _CONTEXTO_SESION.get()
    if contexto is None:
        raise ContextoSesionNoDisponibleError()
    return contexto


@contextmanager
def contexto_de_sesion(contexto: ContextoSesion) -> Iterator[ContextoSesion]:
    """Gestor de contexto que establece y limpia el contexto de sesion (servicios, comandos y pruebas)."""

    token = establecer_contexto(contexto)
    try:
        yield contexto
    finally:
        limpiar_contexto(token)


def utc_now() -> datetime:
    """
    Marca temporal actual en UTC como `datetime` NAIVE.

    El proyecto prohibe el `datetime` aware: la columna Oracle es TIMESTAMP sin zona y
    `USE_TZ = False`, asi que mezclar aware y naive produciria comparaciones incoherentes
    y desplazamientos silenciosos. Se normaliza en UTC y se descarta el `tzinfo`.
    """

    return datetime.now(timezone.utc).replace(tzinfo=None)
