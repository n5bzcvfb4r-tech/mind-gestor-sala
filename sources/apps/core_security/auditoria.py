"""
Traza de los intentos de acceso DENEGADOS (REQ-044 RN-04, AC-ROL-06).

AC-ROL-06 exige que, por cada operacion denegada por falta de permisos, exista una entrada en la
traza con `attempted_at`, `user_id`, `operation` y `outcome`, para el 100% de los intentos. Este
modulo es el UNICO punto del servicio que escribe esa entrada, de modo que la evidencia se produce
siempre igual y no depende de que cada guardia se acuerde de registrarla a su manera.

NOMBRE LOGICO FRENTE A NOMBRE FISICO. `access_denied_log` es el nombre con el que el requisito se
refiere a la traza; NO es una tabla. La tabla fisica ya existe en el inventario T.5 y es
`auditoria_acceso` (ARC-117), mapeada por `apps.core.models.AuditoriaAccesoEntity`. Aqui no se crea
ninguna tabla ni ningun modelo nuevo: se escribe en la que ya esta. La correspondencia es:

===============================  ===========================================================
REQ-044 (`access_denied_log`)    `auditoria_acceso` (ARC-117)
===============================  ===========================================================
`attempted_at`                   `occurred_at`
`user_id`                        `user_id` (anulable: nulo en el 401 sin sesion)
`operation`                      `operation`
`outcome`                        `outcome` (`DENIED_401` | `DENIED_403`)
===============================  ===========================================================

El resto de columnas de `auditoria_acceso` (`event_type`, `ip_address`, `user_agent`,
`session_id`) son contexto de investigacion que la tabla ya ofrecia y que se rellena cuando se
conoce; `retention_until` es una columna VIRTUAL del DDL y NUNCA se informa.

POR QUE ES BEST-EFFORT. La traza es una consecuencia de la denegacion, no parte de ella. Si el
INSERT falla (Oracle caido, tabla bloqueada, pool agotado), la excepcion no puede escapar: un 403
limpio se convertiria en un 500 y el fallo de la traza pasaria a ser un fallo de la API, con el
agravante de que el cliente aprenderia que algo distinto ocurrio. Por eso el cuerpo entero va en un
`try/except Exception` que deja UNA linea `logger.error(...)` y devuelve `None`. El hueco de
evidencia queda registrado en el log del servicio, que es donde se puede investigar.

POR QUE EL `outcome` INVALIDO SI PROPAGA. La validacion del `outcome` ocurre ANTES del `try`, a
proposito: un valor fuera de `DENIED_401`/`DENIED_403` no es un dato de usuario, es un defecto de
programacion del llamante. Silenciarlo escribiria evidencia con un valor que nadie sabe interpretar
despues; preferimos que reviente en las pruebas y nunca llegue a produccion.

IDENTIDAD DEL SERVIDOR (REQ-064). El `user_id` sale SIEMPRE del servidor (parametro explicito que
el guardia ya resolvio, `request.contexto_sesion` o el `ContextVar` del nucleo) y JAMAS del payload
de la peticion: si lo tomasemos de la entrada, cualquiera podria atribuir sus denegaciones a otro.
La marca temporal tampoco se informa: la sella `AuditoriaAccesoEntity.save()` con `utc_now()` (y el
trigger `trg_auditoria_acceso_servidor` la refija en la base), de modo que nadie puede antedatar un
evento.

SECRETOS (REQ-063, REQ-076). Aqui no se registra ni se persiste jamas la contrasenia, la cabecera
`Authorization`, la credencial de sesion ni el cuerpo de la peticion. `session_id` es el
IDENTIFICADOR de la sesion, no su credencial, y por eso si puede guardarse.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from apps.core.contexto import ContextoSesion, obtener_contexto


if TYPE_CHECKING:  # pragma: no cover - solo para el tipado, evita importar modelos antes de django.setup()
    from apps.core.models import AuditoriaAccesoEntity


logger = logging.getLogger(__name__)

# Valores observables que fija REQ-044 para `outcome`. No hay mas: cualquier otro es un defecto.
OUTCOME_DENEGADO_401 = "DENIED_401"
OUTCOME_DENEGADO_403 = "DENIED_403"

# Valor del catalogo de `auditoria_acceso.event_type` (ARC-117) que clasifica estas entradas.
EVENTO_PERMISO_DENEGADO = "PERMISSION_DENIED"

# Topes de las columnas del DDL. Se trunca en Python porque Oracle no recorta: rechaza el INSERT
# entero (ORA-12899) y perderiamos la evidencia por un `User-Agent` largo.
LONGITUD_MAXIMA_OPERACION = 100  # VARCHAR2(100) de auditoria_acceso.operation
LONGITUD_MAXIMA_IP = 45  # VARCHAR2(45): cabe una IPv6 con zona
LONGITUD_MAXIMA_USER_AGENT = 255  # VARCHAR2(255)
LONGITUD_MAXIMA_SESSION_ID = 36  # VARCHAR2(36): uuid

# Valor de relleno cuando no hay peticion de la que componer la operacion. La columna admite nulos,
# pero una entrada sin operacion no es investigable: es preferible decir explicitamente que no se supo.
OPERACION_DESCONOCIDA = "DESCONOCIDA"

OUTCOMES_DENEGACION: tuple[str, ...] = (OUTCOME_DENEGADO_401, OUTCOME_DENEGADO_403)

TRAZA_FALLO_REGISTRO = "No se pudo registrar el intento de acceso denegado en auditoria_acceso."


__all__ = [
    "EVENTO_PERMISO_DENEGADO",
    "LONGITUD_MAXIMA_OPERACION",
    "OPERACION_DESCONOCIDA",
    "OUTCOMES_DENEGACION",
    "OUTCOME_DENEGADO_401",
    "OUTCOME_DENEGADO_403",
    "operacion_de_la_peticion",
    "registrar_intento_denegado",
]


def _truncar(valor: Any, limite: int) -> str | None:
    """Normaliza a texto y recorta al tope de la columna; devuelve `None` si no hay nada que guardar."""

    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    return texto[:limite]


def operacion_de_la_peticion(request: Any) -> str:
    """
    Compone el valor de `operation` a partir del metodo y la ruta: p. ej. `"GET /api/users/7"`.

    Se usa `path` y NO `get_full_path()` a proposito: la cadena de consulta puede arrastrar valores
    que algun cliente haya colado en la URL (REQ-056 rechaza precisamente la credencial por ahi) y
    la traza es almacenamiento duradero. Lo que no se guarda no se puede filtrar despues.

    Acepta tanto `HttpRequest` como la `Request` de DRF, y tambien `None`: una denegacion siempre
    debe poder registrarse, asi que la funcion es total y nunca lanza. Si no hay de donde componer
    la operacion devuelve `OPERACION_DESCONOCIDA`.
    """

    if request is None:
        return OPERACION_DESCONOCIDA

    metodo = str(getattr(request, "method", "") or "").strip().upper()
    ruta = str(getattr(request, "path", "") or "").strip()
    compuesta = f"{metodo} {ruta}".strip()
    if not compuesta:
        return OPERACION_DESCONOCIDA

    return compuesta[:LONGITUD_MAXIMA_OPERACION]


def _contexto_de_sesion(request: Any) -> ContextoSesion | None:
    """
    Resuelve el contexto de sesion del SERVIDOR: el publicado en la peticion o el del `ContextVar`.

    Se mira primero `request.contexto_sesion` porque el guardia de sesion lo deja ahi antes incluso
    de que `ContextoSesionMiddleware` lo publique en el `ContextVar`; asi una denegacion temprana
    tambien queda atribuida. Nunca se lee nada del cuerpo ni de los parametros de la peticion.
    """

    contexto = getattr(request, "contexto_sesion", None) if request is not None else None
    if isinstance(contexto, ContextoSesion):
        return contexto

    # DRF deja el resultado de la autenticacion en `request.auth`; en esta API es el mismo contexto.
    autenticacion = getattr(request, "auth", None) if request is not None else None
    if isinstance(autenticacion, ContextoSesion):
        return autenticacion

    return obtener_contexto()


def registrar_intento_denegado(
    *,
    request: Any = None,
    outcome: str,
    operation: str | None = None,
    user_id: int | None = None,
    session_id: str | None = None,
) -> AuditoriaAccesoEntity | None:
    """
    Inserta UNA fila en `auditoria_acceso` por cada intento de acceso denegado (AC-ROL-06).

    No filtra, no agrupa y no muestrea: el acuerdo es el 100% de los intentos denegados, de modo que
    la ausencia de una entrada signifique siempre "no hubo denegacion" y no "no nos parecio
    interesante". Quien llama solo tiene que decir QUE denegacion fue (`outcome`); el resto del
    contexto se resuelve aqui desde el servidor.

    `outcome` admite unicamente `OUTCOME_DENEGADO_401` (no hay sesion valida) y
    `OUTCOME_DENEGADO_403` (rol o permiso insuficiente). Cualquier otro valor lanza `ValueError`
    ANTES de entrar en la proteccion best-effort, porque es un defecto del llamante y tiene que
    fallar ruidosamente en las pruebas.

    `user_id` llega del guardia o del contexto de sesion, nunca del payload, y puede ser `None`: es
    el caso legitimo del 401 de un peticionario no autenticado, que REQ-044 declara anulable.

    Devuelve la entrada escrita, o `None` si la escritura fallo. Un fallo de la traza NUNCA rompe la
    respuesta: la excepcion se queda aqui, se registra y la denegacion sigue su curso.
    """

    if outcome not in OUTCOMES_DENEGACION:
        raise ValueError(f"outcome no admitido para un intento denegado: {outcome!r}. Esperado uno de {OUTCOMES_DENEGACION}.")

    operacion = _truncar(operation, LONGITUD_MAXIMA_OPERACION) or operacion_de_la_peticion(request)

    try:
        # El modelo se importa aqui dentro: importarlo al cargar el modulo lo haria antes de
        # `django.setup()` (este modulo lo tocan middlewares y guardias, que se resuelven muy pronto).
        from apps.core.models import AuditoriaAccesoEntity as _AuditoriaAccesoEntity

        contexto = _contexto_de_sesion(request)
        identificador_usuario = user_id if user_id is not None else (contexto.user_id if contexto is not None else None)
        identificador_sesion = session_id if session_id is not None else (contexto.session_id if contexto is not None else None)

        metadatos = getattr(request, "META", None) or {}

        # `occurred_at` NO se informa: lo sella `save()` con la hora del servidor (`utc_now()`), que
        # es lo que REQ-044 llama `attempted_at`. `retention_until` es columna VIRTUAL del DDL y
        # tampoco se toca. `user_id` se pasa como atributo de la clave ajena para no tener que
        # cargar el usuario: registrar la denegacion no debe costar una consulta extra.
        entrada = _AuditoriaAccesoEntity.objects.create(
            user_id=identificador_usuario,
            event_type=EVENTO_PERMISO_DENEGADO,
            operation=operacion,
            outcome=outcome,
            ip_address=_truncar(metadatos.get("REMOTE_ADDR"), LONGITUD_MAXIMA_IP),
            user_agent=_truncar(metadatos.get("HTTP_USER_AGENT"), LONGITUD_MAXIMA_USER_AGENT),
            # Solo el IDENTIFICADOR de la sesion, jamas su credencial (REQ-063, REQ-076).
            session_id=_truncar(identificador_sesion, LONGITUD_MAXIMA_SESSION_ID),
        )
    except Exception as exc:
        # UNA linea y se devuelve `None`. No se incluye `str(exc)` en los datos estructurados para no
        # arrastrar valores de bind de Oracle a la traza; el detalle tecnico va en el `exc_info`.
        logger.error(
            TRAZA_FALLO_REGISTRO,
            exc_info=True,
            extra={
                "data": {
                    "outcome": outcome,
                    "operation": operacion,
                    "event_type": EVENTO_PERMISO_DENEGADO,
                    "exception": type(exc).__name__,
                }
            },
        )
        return None

    return entrada
