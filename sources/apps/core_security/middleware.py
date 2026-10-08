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
from apps.core_security.auditoria import OUTCOME_DENEGADO_401, OUTCOME_DENEGADO_403, registrar_intento_denegado
from apps.core_security.errores import RolNoResolubleError, SesionInvalidaError
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

# Traduccion del status de la denegacion al `outcome` observable de REQ-044. Se deriva del
# `http_status` que ya compone la respuesta y no de una bandera aparte, para que la traza no
# pueda decir una cosa distinta de la que el cliente recibe. Cualquier otro status no es un
# intento denegado y no entra en la traza de accesos.
OUTCOMES_POR_STATUS: dict[int, str] = {401: OUTCOME_DENEGADO_401, 403: OUTCOME_DENEGADO_403}


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

    ROL VIGENTE Y FAIL-CLOSED (REQ-018, AC-ROL-02). Ademas del 401 de sesion, el guardia
    resuelve el ROL EFECTIVO de la peticion releyendolo de la base de datos a partir del
    usuario de la sesion, nunca del `role_code` congelado al emitirla ni de nada que venga del
    cliente. Si ese rol no se puede resolver (usuario inactivo, sin rol o rol fuera de
    `cat_rol`) se deniega con 403 FAIL-CLOSED, sin degradar jamas a un permiso mas amplio. El
    403 se compone aqui porque el middleware va POR DELANTE de DRF y su manejador global de
    excepciones no veria esta denegacion.

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
            # La peticion se pasa SOLO para dejar traza de lo que se ignora; el rol y la identidad NUNCA salen de ella (REQ-018).
            contexto = ServicioSesiones().validar(credencial, request=request)
        except SesionInvalidaError as exc:
            return self._denegar(request, exc)
        except RolNoResolubleError as exc:
            return self._denegar_rol(request, exc)

        # `ContextoSesionMiddleware` (apps.core.middleware), que va DESPUES en la cadena, es
        # quien publica este contexto en el `ContextVar` del nucleo.
        request.contexto_sesion = contexto
        return self.get_response(request)

    def _denegar(self, request: HttpRequest, exc: SesionInvalidaError) -> HttpResponse:
        """Compone la respuesta 401 uniforme y deja UNA linea de traza con el motivo interno."""

        # AC-SES-04: el texto del 401 es SIEMPRE el mismo, sea cual sea el motivo real.
        return self._componer_denegacion(
            request,
            resumen="Peticion denegada por sesion invalida.",
            motivo=exc.motivo,
            codigo=exc.codigo,
            mensaje=mensajes.SESION_REQUERIDA,
            http_status=exc.http_status,
        )

    def _denegar_rol(self, request: HttpRequest, exc: RolNoResolubleError) -> HttpResponse:
        """Compone el 403 FAIL-CLOSED de rol no resoluble (REQ-018, AC-ROL-02)."""

        return self._componer_denegacion(
            request,
            resumen="Peticion denegada: el rol del usuario de la sesion no se puede resolver.",
            motivo=exc.motivo,
            codigo=exc.codigo,
            mensaje=exc.mensaje,
            http_status=exc.http_status,
        )

    def _componer_denegacion(
        self,
        request: HttpRequest,
        *,
        resumen: str,
        motivo: str,
        codigo: str,
        mensaje: str,
        http_status: int,
    ) -> HttpResponse:
        """
        Compone el cuerpo canonico de error, deja UNA linea de traza con el motivo interno y
        registra el intento denegado en `auditoria_acceso` (REQ-044 RN-04, AC-ROL-06).

        El motivo real viaja SOLO al log, nunca a la respuesta. Ni la credencial ni el
        `session_id` se registran jamas (REQ-063, REQ-076).

        PUNTO UNICO. Las DOS denegaciones del guardia (el 401 de sesion invalida y el 403
        FAIL-CLOSED de rol no resoluble) pasan por aqui, asi que basta con registrar en este
        metodo para cubrir el 100% de los intentos que deniega el middleware.
        """

        trace_id = nuevo_trace_id()
        logger.warning(
            resumen,
            extra={
                "data": {
                    "trace_id": trace_id,
                    "motivo": motivo,
                    "path": request.path,
                    "method": request.method,
                }
            },
        )

        # TRAZA DE ACCESOS DENEGADOS (REQ-044 RN-04, AC-ROL-06).
        #
        # DISJUNTO DE `manejadores.manejador_excepciones`. Si el guardia deniega aqui, devuelve la
        # respuesta y la peticion NUNCA entra en el ciclo de DRF, de modo que el manejador de
        # excepciones no llega a verla: un mismo intento produce UNA fila, no dos. Quien audite la
        # traza no tiene que deduplicar nada.
        #
        # El `outcome` se deriva del `http_status` que ya se va a responder. Si algun dia llegase
        # otro status (hoy solo se compone 401 y 403), NO se registra y no se lanza: este metodo es
        # el camino de la respuesta al usuario y una traza nunca puede cambiarla.
        #
        # No se informa `session_id` a proposito: el motivo mismo de la denegacion es que no hay
        # sesion valida. El `user_id` lo resuelve `auditoria` desde el servidor (sera nulo en el
        # 401, y en el 403 de rol no resoluble lo sera o no segun se haya publicado el contexto);
        # forzarlo desde aqui solo podria empeorar lo que el modulo ya sabe.
        outcome = OUTCOMES_POR_STATUS.get(http_status)
        if outcome is not None:
            # `registrar_intento_denegado` es best-effort y nunca lanza con un `outcome` valido:
            # un fallo de la escritura no altera el status, el cuerpo ni el `traceId` de abajo.
            registrar_intento_denegado(request=request, outcome=outcome)

        return respuesta_error_json(codigo, mensaje, http_status, trace_id)
