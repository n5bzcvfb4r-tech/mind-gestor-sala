"""
Middleware que publica el contexto de sesion de la peticion en el `ContextVar` del nucleo.
"""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from apps.core.contexto import ContextoSesion, establecer_contexto, limpiar_contexto


class ContextoSesionMiddleware:
    """
    Traslada el contexto de sesion ya resuelto por la capa de identidad al `ContextVar` del nucleo.

    La autenticacion (sesion opaca en servidor) es responsabilidad de la app de identidad; cuando
    resuelve la sesion deja un `ContextoSesion` en `request.contexto_sesion`. Este middleware se
    limita a publicarlo durante la peticion y a limpiarlo SIEMPRE en el `finally`, de modo que
    ningun contexto sobreviva al hilo o se filtre a la peticion siguiente.

    Si la peticion no trae contexto (anonima, o endpoint publico) no se establece nada:
    deny-by-default. La atribucion posterior fallara con `ContextoSesionNoDisponibleError`
    en lugar de inventar un actor.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        contexto: ContextoSesion | None = getattr(request, "contexto_sesion", None)
        if contexto is None:
            return self.get_response(request)

        token = establecer_contexto(contexto)
        try:
            return self.get_response(request)
        finally:
            limpiar_contexto(token)
