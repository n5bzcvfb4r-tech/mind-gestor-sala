"""
Guardia de sesion de la superficie HTTP: exige sesion valida en toda ruta no declarada publica.

Va por delante de la vista (middleware clasico de Django) a proposito, para cubrir tambien
las vistas Django planas que no pasan por DRF, y para que la denegacion se decida antes de
tocar la base de datos de negocio.
"""

import logging
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from apps.core_security import mensajes
from apps.core_security.errores import SesionInvalidaError
from apps.core_security.respuestas import nuevo_trace_id, respuesta_error_json
from apps.core_security.rutas_publicas import es_ruta_exenta
from apps.core_security.servicios.sesiones import ServicioSesiones


logger = logging.getLogger(__name__)

# Esquema unico admitido en la cabecera `Authorization`.
ESQUEMA_AUTORIZACION = "bearer"

# Nombres de parametro de consulta que algun cliente podria usar para colar la credencial en
# la URL. Se rechazan de plano: la credencial de sesion NO viaja en la URL (REQ-056).
PARAMETROS_CREDENCIAL_PROHIBIDOS: tuple[str, ...] = ("session_id", "sessionId", "access_token", "token")

MOTIVO_CREDENCIAL_EN_URL = "credencial_en_url"


def credencial_de_la_peticion(request: HttpRequest) -> str | None:
    """
    Extrae la credencial de sesion de la cabecera `Authorization` con el esquema `Bearer`.

    El esquema se compara sin distinguir mayusculas (`Bearer`, `bearer`, `BEARER`). Si la
    cabecera falta, usa otro esquema o no trae valor, devuelve `None`: sin credencial no hay
    sesion, y la decision de denegar la toma quien llama.

    La credencial devuelta NUNCA debe registrarse en logs ni trazas (REQ-063, REQ-076).
    """

    cabecera = request.META.get("HTTP_AUTHORIZATION") or ""
    if not cabecera:
        return None

    partes = cabecera.split(" ", 1)
    if len(partes) != 2:
        return None

    esquema, valor = partes
    if esquema.strip().lower() != ESQUEMA_AUTORIZACION:
        return None

    credencial = valor.strip()
    return credencial or None


class SesionRequeridaMiddleware:
    """
    Guardia PROTEGIDO POR DEFECTO de la superficie HTTP (AC-XFN-01, AC-SES-04).

    PROTEGIDO POR DEFECTO (AC-XFN-01). El guardia no pregunta por decoradores ni por
    convenciones de nombres: pregunta por `rutas_publicas.es_ruta_exenta()`. CUALQUIER ruta
    que no este declarada explicitamente alli queda protegida, de modo que un endpoint NUEVO
    responde 401 sin sesion aunque nadie se acuerde de protegerlo. Abrir una ruta exige una
    decision de seguridad explicita y visible en `rutas_publicas`.

    POR DELANTE DE LA VISTA. Al ser middleware clasico de Django y no una clase de
    autenticacion de DRF, cubre tambien las vistas Django planas que no pasan por el ciclo
    de DRF; ninguna puede quedarse sin guardia por estar fuera del framework REST.

    RESPUESTA UNIFORME (AC-SES-04). La denegacion es IDENTICA exista o no el recurso
    solicitado y sea cual sea el motivo real (credencial ausente, manipulada, sesion revocada
    o caducada): mismo 401, mismo `code` (`AUTH_SESSION_INVALID`) y mismo `message`
    (`mensajes.SESION_REQUERIDA`). El motivo concreto solo viaja a la traza interna, junto al
    `traceId` que se devuelve al cliente, para poder investigar sin convertir la API en un
    oraculo que confirme que una sesion o un recurso existieron.

    CREDENCIAL FUERA DE LA URL (REQ-056). Si la peticion trae la credencial como parametro de
    consulta se deniega igual, sin usarla: en la URL quedaria registrada en logs de
    servidores intermedios, en el historial del navegador y en la cabecera `Referer`.

    SECRETOS (REQ-063, REQ-076). No se registra jamas la credencial, la cabecera
    `Authorization`, el `session_id` ni ninguna contrasenia.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if es_ruta_exenta(request.path, request.method or ""):
            return self.get_response(request)

        if any(parametro in request.GET for parametro in PARAMETROS_CREDENCIAL_PROHIBIDOS):
            return self._denegar(request, SesionInvalidaError(motivo=MOTIVO_CREDENCIAL_EN_URL))

        credencial = credencial_de_la_peticion(request)
        try:
            contexto = ServicioSesiones().validar(credencial)
        except SesionInvalidaError as exc:
            return self._denegar(request, exc)

        # `ContextoSesionMiddleware` (apps.core.middleware), que va DESPUES en la cadena, es
        # quien publica este contexto en el `ContextVar` del nucleo.
        request.contexto_sesion = contexto
        return self.get_response(request)

    def _denegar(self, request: HttpRequest, exc: SesionInvalidaError) -> HttpResponse:
        """Compone la respuesta 401 uniforme y deja UNA linea de traza con el motivo interno."""

        trace_id = nuevo_trace_id()
        logger.warning(
            "Peticion denegada por sesion invalida.",
            extra={
                "data": {
                    "trace_id": trace_id,
                    "motivo": exc.motivo,
                    "path": request.path,
                    "method": request.method,
                }
            },
        )
        return respuesta_error_json(exc.codigo, mensajes.SESION_REQUERIDA, exc.http_status, trace_id)
