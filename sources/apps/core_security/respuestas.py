"""
Envolvente UNICA de error del servicio: un solo sitio compone el cuerpo de un error HTTP.

Ningun endpoint, middleware ni manejador devuelve un diccionario de error propio: todos
pasan por `cuerpo_error()`, de modo que el contrato (`code`, `message`, `details`,
`traceId`) no se pueda desalinear entre dos rutas distintas.

CORRELADOR (`traceId`). Es un identificador REAL generado por peticion, no un hueco fijo.
Sirve para cruzar lo que ve el usuario con la linea de log tecnica: el usuario solo recibe
el mensaje uniforme en espanol, y el motivo interno (sesion caducada, revocada, manipulada)
queda en la traza, localizable por ese mismo `traceId`.

SECRETOS (REQ-063, REQ-076). El cuerpo NUNCA transporta credenciales, `password_hash`,
identificadores de sesion ni detalle del motivo del fallo de autenticacion.
"""

from uuid import uuid4

from django.http import JsonResponse


def nuevo_trace_id() -> str:
    """Genera un correlador de peticion (uuid4 en hexadecimal, sin guiones)."""

    return uuid4().hex


def cuerpo_error(
    codigo: str,
    mensaje: str,
    details: list[dict[str, str]] | None = None,
    trace_id: str | None = None,
) -> dict[str, object]:
    """
    Compone el cuerpo canonico de error del servicio.

    Args:
        codigo: identificador estable del error (p. ej. `AUTH_SESSION_INVALID`).
        mensaje: texto visible para el usuario, SIEMPRE en espanol (REQ-050).
        details: detalles por campo; `None` se serializa como lista vacia, nunca como `null`.
        trace_id: correlador de la peticion; si no se aporta se genera uno nuevo.

    Returns:
        Diccionario con las claves `code`, `message`, `details` y `traceId`.
    """

    return {
        "code": codigo,
        "message": mensaje,
        "details": details or [],
        "traceId": trace_id or nuevo_trace_id(),
    }


def respuesta_error_json(codigo: str, mensaje: str, http_status: int, trace_id: str | None = None) -> JsonResponse:
    """
    Devuelve el cuerpo canonico de error como `JsonResponse` con el status indicado.

    Se serializa con `ensure_ascii=False` para que los mensajes en espanol conserven sus
    tildes y la enie en el cuerpo de la respuesta.
    """

    return JsonResponse(
        cuerpo_error(codigo, mensaje, trace_id=trace_id),
        status=http_status,
        json_dumps_params={"ensure_ascii": False},
    )
