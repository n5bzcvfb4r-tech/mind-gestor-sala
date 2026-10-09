"""
Manejador UNICO de excepciones de DRF del servicio.

UNA SOLA FORMA DE ERROR. Antes convivian dos envolventes en la misma API: el guardia de
sesion respondia el cuerpo canonico (`code`, `message`, `details`, `traceId`) y cualquier
fallo atendido por DRF respondia su envolvente por defecto (`{"username": ["..."]}`). Este
manejador cierra ese hueco: TODO error que atraviese el ciclo de DRF sale por aqui y se
serializa con `respuestas.cuerpo_error()`, la misma funcion que usa el middleware. No se
compone ningun diccionario de error a mano en ninguna otra parte.

ERRORES DE DOMINIO. DRF enruta a este manejador CUALQUIER excepcion que escape del handler
de la vista (`APIView.dispatch` captura `Exception` y llama a `handle_exception`), no solo
las `APIException`. Por eso las excepciones de dominio del servicio (`ErrorDominio` con
`codigo`/`http_status`) se traducen aqui y las vistas pueden dejarlas propagar sin repetir
la traduccion en cada `post`.

SECRETOS (REQ-063, REQ-076). Ni el cuerpo ni la traza transportan contrasenias,
`password_hash`, la credencial de sesion ni la cabecera `Authorization`. Una excepcion no
controlada NUNCA devuelve su `str(exc)` ni su traza: se responde un mensaje generico y el
detalle tecnico se queda en el log, localizable por el `traceId`.
"""

import logging
from collections.abc import Iterator
from typing import Any

from django.core.exceptions import PermissionDenied as PermisoDenegadoDjango
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as manejador_por_defecto_drf

from apps.core import mensajes as mensajes_core
from apps.core.errores import ErrorDominio
from apps.core_security import mensajes
from apps.core_security.auditoria import OUTCOME_DENEGADO_401, OUTCOME_DENEGADO_403, registrar_intento_denegado
from apps.core_security.respuestas import cuerpo_error, nuevo_trace_id


logger = logging.getLogger(__name__)

# Codigos estables de error de la superficie de enrutado.
CODIGO_VALIDACION = "VAL_INVALID_REQUEST"
CODIGO_SESION_INVALIDA = "AUTH_SESSION_INVALID"
CODIGO_PERMISO_DENEGADO = "PERM_DENIED"
CODIGO_NO_ENCONTRADO = "NOT_FOUND"
CODIGO_METODO_NO_PERMITIDO = "METHOD_NOT_ALLOWED"
CODIGO_NO_ACEPTABLE = "NOT_ACCEPTABLE"
CODIGO_MEDIO_NO_SOPORTADO = "UNSUPPORTED_MEDIA_TYPE"
CODIGO_DEMASIADAS_PETICIONES = "TOO_MANY_REQUESTS"
CODIGO_INESPERADO = "SYS_UNEXPECTED"

# Nombre de campo para los errores de validacion que no cuelgan de ningun campo concreto.
CAMPO_SIN_CAMPO = "non_field_errors"

# Unicos status que REQ-044 considera INTENTO DENEGADO. Se mapean al `outcome` observable de la
# traza. El resto (400, 404, 405, 429, 500...) son otra clase de fallo y no entran: mezclarlos
# haria inservible la muestra auditada, porque «hay fila» dejaria de significar «hubo denegacion».
OUTCOMES_POR_STATUS: dict[int, str] = {
    status.HTTP_401_UNAUTHORIZED: OUTCOME_DENEGADO_401,
    status.HTTP_403_FORBIDDEN: OUTCOME_DENEGADO_403,
}

# Codigo y mensaje genericos por `status_code` para las `APIException` sin traduccion propia.
CODIGOS_POR_STATUS: dict[int, str] = {
    status.HTTP_406_NOT_ACCEPTABLE: CODIGO_NO_ACEPTABLE,
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: CODIGO_MEDIO_NO_SOPORTADO,
    status.HTTP_429_TOO_MANY_REQUESTS: CODIGO_DEMASIADAS_PETICIONES,
}

MENSAJES_POR_STATUS: dict[int, str] = {
    status.HTTP_406_NOT_ACCEPTABLE: mensajes.FORMATO_NO_ACEPTABLE,
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: mensajes.MEDIO_NO_SOPORTADO,
    status.HTTP_429_TOO_MANY_REQUESTS: mensajes.DEMASIADAS_PETICIONES,
}


def _ruta_de_la_peticion(context: dict[str, Any] | None) -> str:
    """Devuelve la ruta de la peticion para la traza; cadena vacia si el contexto no la trae."""

    peticion = (context or {}).get("request")
    return str(getattr(peticion, "path", "") or "")


def _metodo_de_la_peticion(context: dict[str, Any] | None) -> str:
    """Devuelve el metodo HTTP de la peticion para la traza; cadena vacia si no esta disponible."""

    peticion = (context or {}).get("request")
    return str(getattr(peticion, "method", "") or "")


def _aplanar_detalle(detalle: Any, ruta: str = "") -> Iterator[dict[str, str]]:
    """
    Aplana el `detail` de una `ValidationError` de DRF a una lista de `{"field", "message"}`.

    El `detail` puede ser un texto, una lista, un diccionario o cualquier anidamiento de los
    tres (serializadores anidados, `many=True`). Se recorre entero para que NINGUN mensaje se
    pierda por el camino: las claves componen la ruta del campo con puntos (`items.titulo`) y
    las listas de objetos anaden el indice (`items[0].titulo`). Las listas de textos, que son
    la forma normal de los errores de un campo, NO se indexan: todos sus mensajes comparten
    el mismo `field`.
    """

    if isinstance(detalle, dict):
        for clave, valor in detalle.items():
            yield from _aplanar_detalle(valor, f"{ruta}.{clave}" if ruta else str(clave))
        return

    if isinstance(detalle, (list, tuple)):
        for indice, valor in enumerate(detalle):
            if isinstance(valor, (dict, list, tuple)):
                yield from _aplanar_detalle(valor, f"{ruta}[{indice}]" if ruta else f"[{indice}]")
            else:
                yield from _aplanar_detalle(valor, ruta)
        return

    yield {"field": ruta or CAMPO_SIN_CAMPO, "message": str(detalle)}


def detalles_de_validacion(detalle: Any) -> list[dict[str, str]]:
    """Traduce el `detail` de una `ValidationError` de DRF al `details` del cuerpo canonico."""

    return list(_aplanar_detalle(detalle))


def _traducir(exc: Exception, http_status_drf: int) -> tuple[str, str, int, list[dict[str, str]]]:
    """
    Traduce una excepcion de DRF (o de Django ya enrutada por DRF) a `(codigo, mensaje, http_status, details)`.

    `http_status_drf` es el status que DRF habia resuelto para la excepcion y solo se usa como
    respaldo para las `APIException` que no tienen traduccion propia.
    """

    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, PermisoDenegadoDjango):
        exc = exceptions.PermissionDenied()

    if isinstance(exc, exceptions.ValidationError):
        return (CODIGO_VALIDACION, mensajes.DATOS_INVALIDOS, status.HTTP_400_BAD_REQUEST, detalles_de_validacion(exc.detail))

    # Un fallo de autenticacion responde el MISMO 401 uniforme que da el guardia de sesion
    # (AC-SES-04): el cliente ve una sola forma de «no tienes sesion» venga de donde venga.
    if isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
        return (CODIGO_SESION_INVALIDA, mensajes.SESION_REQUERIDA, status.HTTP_401_UNAUTHORIZED, [])

    if isinstance(exc, exceptions.PermissionDenied):
        return (CODIGO_PERMISO_DENEGADO, mensajes.PERMISO_DENEGADO, status.HTTP_403_FORBIDDEN, [])

    if isinstance(exc, exceptions.NotFound):
        return (CODIGO_NO_ENCONTRADO, mensajes_core.RECURSO_NO_ENCONTRADO, status.HTTP_404_NOT_FOUND, [])

    if isinstance(exc, exceptions.MethodNotAllowed):
        return (CODIGO_METODO_NO_PERMITIDO, mensajes.METODO_NO_PERMITIDO, status.HTTP_405_METHOD_NOT_ALLOWED, [])

    # Resto de `APIException`: se conserva su status y se responde un mensaje en espanol.
    # NUNCA se devuelve el `str(exc)` crudo de una excepcion que no sabemos describir.
    codigo = CODIGOS_POR_STATUS.get(http_status_drf, CODIGO_INESPERADO)
    mensaje = MENSAJES_POR_STATUS.get(http_status_drf, mensajes.ERROR_INESPERADO)
    return (codigo, mensaje, http_status_drf, [])


def _registrar(trace_id: str, codigo: str, http_status: int, context: dict[str, Any] | None, tipo: str) -> None:
    """
    Deja UNA linea de traza por error manejado, con el correlador que ve el cliente.

    SECRETOS (REQ-063, REQ-076). Se registran solo el correlador, el codigo, el status, la
    ruta, el metodo y el tipo de excepcion: jamas la contrasenia, el `password_hash`, la
    credencial de sesion ni la cabecera `Authorization`. Tampoco se vuelcan los `details`,
    que pueden arrastrar datos de entrada del usuario.
    """

    logger.warning(
        "Peticion resuelta con error.",
        extra={
            "data": {
                "trace_id": trace_id,
                "code": codigo,
                "http_status": http_status,
                "path": _ruta_de_la_peticion(context),
                "method": _metodo_de_la_peticion(context),
                "exception": tipo,
            }
        },
    )


def _registrar_denegacion(http_status: int, context: dict[str, Any] | None) -> None:
    """
    Deja la fila de `auditoria_acceso` de los intentos denegados (REQ-044 RN-04, AC-ROL-06).

    Solo registra el 401 (`AUTH_SESSION_INVALID`, `NotAuthenticated`/`AuthenticationFailed`) y el
    403 (`PermisoDenegadoError` de la matriz o de la guardia de ADMINISTRADOR, `PermissionDenied`
    de DRF). Cualquier otro status se ignora en silencio: un 404, un 422 o un 500 no son intentos
    denegados y ensuciarian la traza de accesos.

    DISJUNTO DE `SesionRequeridaMiddleware._componer_denegacion`. El guardia de sesion deniega POR
    DELANTE de DRF y devuelve su respuesta sin entrar en el ciclo, de modo que lo que el middleware
    registra NUNCA vuelve a pasar por aqui: un mismo intento denegado produce UNA fila, no dos.

    La escritura es best-effort (`registrar_intento_denegado` no propaga) y se hace sobre la
    respuesta YA compuesta: ni el status, ni el cuerpo canonico, ni el `traceId` que ve el cliente
    cambian por lo que ocurra aqui.
    """

    outcome = OUTCOMES_POR_STATUS.get(http_status)
    if outcome is None:
        return

    # Si el contexto no trae peticion se registra igual con `request=None`: `auditoria` anotara
    # `operation="DESCONOCIDA"` antes que perder la fila, y el acuerdo es el 100% de los intentos.
    registrar_intento_denegado(request=(context or {}).get("request"), outcome=outcome)


def manejador_excepciones(exc: Exception, context: dict[str, Any] | None) -> Response | None:
    """
    Manejador de excepciones de DRF del servicio (`REST_FRAMEWORK["EXCEPTION_HANDLER"]`).

    Args:
        exc: excepcion que ha escapado de la vista.
        context: contexto que aporta DRF (`view`, `args`, `kwargs`, `request`).

    Returns:
        La respuesta con el cuerpo canonico de error, o `None` si la excepcion no esta
        controlada: en ese caso Django la convierte en un 500 generico, que no debe filtrar
        ni el mensaje de la excepcion ni la traza.
    """

    trace_id = nuevo_trace_id()
    tipo = type(exc).__name__

    # Los errores de dominio del servicio ya traen su contrato HTTP (`codigo`, `http_status`).
    if isinstance(exc, ErrorDominio) and hasattr(exc, "codigo") and hasattr(exc, "http_status"):
        codigo = str(exc.codigo)
        http_status = int(exc.http_status)
        # Un fallo de sesion responde SIEMPRE el mismo texto (AC-SES-04), aunque la excepcion
        # se haya construido con un mensaje propio: el motivo real solo viaja por la traza.
        mensaje = mensajes.SESION_REQUERIDA if codigo == CODIGO_SESION_INVALIDA else str(exc.mensaje)
        # Algunos errores de dominio traen una LISTA de incumplimientos que es parte de su
        # contrato: el rechazo por politica de contrasenias (`AUTH_PASSWORD_POLICY`) debe
        # responder 422 con todas las reglas incumplidas (AC-PWD-03, REQ-069), y `details` es
        # el unico hueco del cuerpo canonico que puede transportarlas. Se lee con `getattr`
        # para no acoplar este manejador transversal a una excepcion concreta de identidad: las
        # `ErrorDominio` que no publican `detalles` siguen saliendo con `details: []`.
        detalles = getattr(exc, "detalles", None)
        _registrar(trace_id, codigo, http_status, context, tipo)
        # Aqui salen `SesionInvalidaError` (401) y `PermisoDenegadoError`/`RolNoResolubleError`
        # (403). Esta rama hace `return`, asi que no puede solaparse con la del manejador por
        # defecto de DRF de mas abajo: cada intento denegado se contabiliza una sola vez.
        _registrar_denegacion(http_status, context)
        return Response(cuerpo_error(codigo, mensaje, detalles, trace_id), status=http_status)

    # El manejador por defecto resuelve el status y, sobre todo, las cabeceras de la respuesta
    # (`WWW-Authenticate` en los 401, `Retry-After` en los 429), que hay que preservar.
    respuesta = manejador_por_defecto_drf(exc, context)
    if respuesta is None:
        # Excepcion no-DRF: se deja escapar para que Django responda un 500 sin detalle.
        logger.exception(
            "Error no controlado al atender la peticion.",
            extra={
                "data": {
                    "trace_id": trace_id,
                    "code": CODIGO_INESPERADO,
                    "http_status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "path": _ruta_de_la_peticion(context),
                    "method": _metodo_de_la_peticion(context),
                    "exception": tipo,
                }
            },
        )
        return None

    codigo, mensaje, http_status, details = _traducir(exc, respuesta.status_code)
    respuesta.data = cuerpo_error(codigo, mensaje, details, trace_id)
    respuesta.status_code = http_status
    _registrar(trace_id, codigo, http_status, context, tipo)
    # Denegaciones que no son `ErrorDominio`: el 401 `AUTH_SESSION_INVALID` y el 403 `PERM_DENIED`
    # que `_traducir` acaba de resolver. Se registra DESPUES de componer la respuesta para que un
    # fallo de la traza no pueda alterarla, y sobre el `http_status` ya traducido (no el de DRF).
    _registrar_denegacion(http_status, context)
    return respuesta
