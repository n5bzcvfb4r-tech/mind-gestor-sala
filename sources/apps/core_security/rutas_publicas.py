"""
Lista EXPLICITA de rutas publicas: todo lo demas esta protegido.

PROTEGIDO POR DEFECTO (AC-XFN-01). El guardia de sesion no consulta ninguna convencion de
nombres ni ningun decorador optativo: consulta esta lista. Un endpoint nuevo que nadie
declare aqui responde 401 sin que haya que acordarse de protegerlo, que es justo lo que se
quiere.

ANADIR UNA ENTRADA A `RUTAS_PUBLICAS` ES UNA DECISION DE SEGURIDAD EXPLICITA: abre el
recurso a cualquiera que alcance el servicio, y debe estar respaldada por un requisito.
Hoy la UNICA ruta publica de negocio es el inicio de sesion (EP-001, REQ-058): no se puede
exigir sesion para crearla.

`PREFIJOS_EXENTOS` es otra cosa y por eso va aparte: son recursos NO funcionales
(documentacion de la API, estaticos y media) que no exponen datos de negocio. Sus rutas se
leen de `django.conf.settings` en tiempo de llamada para que no haya dos fuentes de verdad
que se puedan desincronizar.
"""

from dataclasses import dataclass

from django.conf import settings


# Documentacion de la API (drf-spectacular): esquema y visores.
PREFIJO_DOCUMENTACION = "/docs/"


@dataclass(frozen=True, slots=True)
class RutaPublica:
    """Ruta accesible SIN sesion, acotada a los metodos HTTP que se declaren."""

    path: str
    metodos: tuple[str, ...]


# El inicio de sesion (EP-001, REQ-058) es la unica ruta publica de negocio del servicio.
RUTAS_PUBLICAS: tuple[RutaPublica, ...] = (RutaPublica(path="/api/auth/sessions", metodos=("POST",)),)


def _normalizar_path(path: str) -> str:
    """Normaliza la barra final: `/api/auth/sessions` y `/api/auth/sessions/` son la MISMA ruta."""

    if not path:
        return "/"
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path


def _normalizar_prefijo(valor: str) -> str:
    """Devuelve el prefijo como ruta absoluta terminada en barra (`static/` -> `/static/`)."""

    prefijo = valor or ""
    if not prefijo.startswith("/"):
        prefijo = f"/{prefijo}"
    if not prefijo.endswith("/"):
        prefijo = f"{prefijo}/"
    return prefijo


def prefijos_exentos() -> tuple[str, ...]:
    """
    Prefijos de recursos NO funcionales que quedan fuera del guardia de sesion.

    Se resuelven aqui, leyendo `settings`, para no duplicar las rutas de estaticos y media.
    """

    return (
        PREFIJO_DOCUMENTACION,
        _normalizar_prefijo(getattr(settings, "STATIC_URL", "static/")),
        _normalizar_prefijo(getattr(settings, "MEDIA_URL", "media/")),
    )


# Prefijos exentos como constante publica. Se declara SIN valor y se resuelve en cada acceso
# via `__getattr__` (PEP 562): asi la unica fuente de verdad sigue siendo `prefijos_exentos()`
# y no se congela `settings` en el instante del import.
PREFIJOS_EXENTOS: tuple[str, ...]


def __getattr__(nombre: str) -> tuple[str, ...]:
    if nombre == "PREFIJOS_EXENTOS":
        return prefijos_exentos()
    raise AttributeError(f"module {__name__!r} has no attribute {nombre!r}")


def es_ruta_exenta(path: str, metodo: str) -> bool:
    """
    Indica si la peticion puede atenderse SIN sesion.

    Devuelve `True` solo si el path (normalizada la barra final) y el metodo casan con una
    `RutaPublica`, o si el path cuelga de un prefijo exento. CUALQUIER otra combinacion es
    ruta protegida: deny-by-default.
    """

    path_normalizado = _normalizar_path(path)
    metodo_normalizado = (metodo or "").upper()

    for ruta in RUTAS_PUBLICAS:
        if _normalizar_path(ruta.path) == path_normalizado and metodo_normalizado in ruta.metodos:
            return True

    return any(path.startswith(prefijo) for prefijo in prefijos_exentos())
