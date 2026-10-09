"""
Traza de los eventos de bloqueo de cuenta en `auditoria_acceso` (REQ-055, REQ-072, REQ-075).

QUE SE REGISTRA Y POR QUE AQUI
------------------------------
Tres hechos observables del ciclo de vida del bloqueo: el intento de contrasenia fallido
(`LOGIN_FAILED`), el momento exacto en que la cuenta queda bloqueada al alcanzar el umbral
(`ACCOUNT_LOCKED`, el evento `UserAccountLocked` de REQ-055) y el levantamiento administrativo
del bloqueo (`ACCOUNT_UNLOCKED`, REQ-075). Los tres se escriben desde este unico modulo para
que la evidencia se produzca siempre con la misma forma: si cada rama del servicio compusiera
su propia fila, los valores de `event_type` y `outcome` divergirian a la primera correccion y
la traza dejaria de ser consultable con un criterio homogeneo.

NO SE CREA NINGUNA TABLA. Se escribe en `auditoria_acceso` (ARC-117), que ya existe en el
inventario T.5 y esta mapeada por `apps.core.models.AuditoriaAccesoEntity` con
`managed=False`. Los valores de `event_type` y `outcome` salen del enumerado de ARC-117; no se
inventa ninguno nuevo, porque un valor fuera del catalogo es evidencia que nadie sabe
interpretar despues.

POR QUE ES BEST-EFFORT (mismo criterio que `apps.core_security.auditoria`)
---------------------------------------------------------------------------
La traza es una CONSECUENCIA del intento fallido o del desbloqueo, no parte de ellos. Si el
INSERT falla -Oracle caido, pool agotado, tabla bloqueada- la excepcion no puede escapar: un
401 limpio se convertiria en un 500 y, peor aun, el cliente aprenderia por el codigo de estado
que algo distinto ocurrio en su intento, que es justo el oraculo que REQ-053 prohibe. Por eso
el `create(...)` va dentro de un `try/except Exception` que deja UNA linea `logger.error(...)`
con `exc_info` y devuelve sin propagar. El hueco de evidencia queda en el log del servicio.

EL ACTOR PUEDE SER NULO, Y ES LEGITIMO
--------------------------------------
Un intento contra un usuario INEXISTENTE no tiene `user_id` que atribuir: solo queda el
`username_attempted` tecleado. ARC-117 declara la columna anulable precisamente para ese caso,
y `AuditoriaAccesoEntity` no usa `AtribucionMixin` por lo mismo. Registrar esos intentos es lo
que permite detectar despues una enumeracion de cuentas, asi que NO se descartan.

LA MARCA TEMPORAL ES DEL SERVIDOR (REQ-064)
-------------------------------------------
`occurred_at` NO se informa nunca desde aqui: la sella `AuditoriaAccesoEntity.save()` con
`utc_now()` y el trigger `trg_auditoria_acceso_servidor` la refija en la base. Nadie puede
antedatar un evento de bloqueo. `retention_until` es columna VIRTUAL del DDL y tampoco se toca.

SECRETOS (REQ-063, REQ-072, REQ-076)
------------------------------------
La contrasenia introducida NO se recibe en esta funcion, NO se guarda y NO se escribe en el
log. La firma no tiene ningun parametro por el que pudiera colarse, y es deliberado: lo que no
se recibe no se puede filtrar. `username_attempted` se registra SOLO -nunca acompanado de la
contrasenia, de su longitud, de un prefijo o de su hash-, porque es el identificador tecleado
y sirve para investigar la racha; el secreto que se probo contra el no deja rastro en ninguna
parte. Tampoco se registra `password_hash` ni el contador de intentos.
"""

from __future__ import annotations

import logging

from apps.core_security.auditoria import OUTCOME_DENEGADO_401


logger = logging.getLogger(__name__)

# Valores del enumerado cerrado `auditoria_acceso.event_type` (ARC-117) que clasifican los
# eventos de este modulo. No hay mas: cualquier otro valor seria evidencia no interpretable.
EVENTO_LOGIN_FALLIDO = "LOGIN_FAILED"
EVENTO_CUENTA_BLOQUEADA = "ACCOUNT_LOCKED"
EVENTO_CUENTA_DESBLOQUEADA = "ACCOUNT_UNLOCKED"

# Valores del enumerado `auditoria_acceso.outcome` (ARC-117). `DENIED_401` se reutiliza de
# `apps.core_security.auditoria`, que ya lo declara, para que exista UN solo literal en el
# servicio. `OK` no vive alli -ese modulo solo registra denegaciones- y se declara aqui: el
# desbloqueo administrativo es una operacion CORRECTA de un actor autorizado, no una denegacion.
OUTCOME_OK = "OK"

# Topes de las columnas del DDL. Se trunca en Python porque Oracle no recorta: rechaza el
# INSERT entero (ORA-12899) y perderiamos la evidencia por un identificador tecleado demasiado
# largo, que es justo lo que haria un atacante que quisiera quedarse sin traza.
LONGITUD_MAXIMA_USERNAME_INTENTADO = 150  # VARCHAR2(150) de auditoria_acceso.username_attempted
LONGITUD_MAXIMA_OPERACION = 100  # VARCHAR2(100)
LONGITUD_MAXIMA_SESSION_ID = 36  # VARCHAR2(36): uuid

TRAZA_FALLO_REGISTRO = "No se pudo registrar el evento de bloqueo de cuenta en auditoria_acceso."


__all__ = [
    "EVENTO_CUENTA_BLOQUEADA",
    "EVENTO_CUENTA_DESBLOQUEADA",
    "EVENTO_LOGIN_FALLIDO",
    "LONGITUD_MAXIMA_OPERACION",
    "LONGITUD_MAXIMA_SESSION_ID",
    "LONGITUD_MAXIMA_USERNAME_INTENTADO",
    "OUTCOME_DENEGADO_401",
    "OUTCOME_OK",
    "registrar_evento_de_bloqueo",
]


def _truncar(valor: object, limite: int) -> str | None:
    """Normaliza a texto y recorta al tope de la columna; devuelve `None` si no hay nada que guardar."""

    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    return texto[:limite]


def registrar_evento_de_bloqueo(
    *,
    event_type: str,
    outcome: str,
    usuario: object | None = None,
    username_attempted: str | None = None,
    operation: str | None = None,
    session_id: str | None = None,
) -> None:
    """
    Inserta UNA fila en `auditoria_acceso` por cada evento de bloqueo, sin romper nunca el flujo.

    Es la unica puerta de escritura de traza del paquete `apps.identidad.bloqueo`. Quien llama
    solo declara QUE hecho ocurrio (`event_type`) y COMO termino (`outcome`); el truncado, la
    resolucion del `user_id` y la proteccion frente a fallos de base se resuelven aqui.

    Args:
        event_type: valor del catalogo de ARC-117. Los de este modulo son
            `EVENTO_LOGIN_FALLIDO`, `EVENTO_CUENTA_BLOQUEADA` y `EVENTO_CUENTA_DESBLOQUEADA`.
        outcome: `OUTCOME_DENEGADO_401` para los intentos denegados (fallo de contrasenia y
            bloqueo alcanzado) y `OUTCOME_OK` para el desbloqueo administrativo, que es una
            operacion correcta y no una denegacion.
        usuario: entidad `UsuarioEntity` afectada, o `None`. De ella se toma UNICAMENTE
            `user_id`; nunca `password_hash` ni ninguna otra columna de credencial.
        username_attempted: identificador TECLEADO en el intento. Se guarda solo (REQ-063,
            REQ-072, REQ-076): jamas se acompana de la contrasenia probada, de su longitud ni
            de fragmento alguno de ella, que ni siquiera llegan a esta funcion.
        operation: operacion funcional asociada, p. ej. `"USER_UNLOCK"` en el desbloqueo.
        session_id: IDENTIFICADOR de la sesion del actor, nunca su credencial.

    No devuelve nada y NO PROPAGA: un fallo al escribir la traza no puede tumbar la
    autenticacion ni el desbloqueo. `occurred_at` no se informa, lo sella el servidor.
    """

    # `user_id` anulable a proposito: el intento contra un usuario inexistente es un caso
    # legitimo y ARC-117 lo declara nulo. Se lee el atributo de la clave ajena para no obligar
    # a una consulta extra solo para dejar la traza.
    identificador_usuario = getattr(usuario, "user_id", None) if usuario is not None else None

    try:
        # El modelo se importa AQUI DENTRO y no al cargar el modulo: este fichero lo tocan
        # servicios que se resuelven antes de `django.setup()`, y un import de modelos a nivel
        # de modulo reventaria el arranque (`AppRegistryNotReady`).
        from apps.core.models import AuditoriaAccesoEntity

        AuditoriaAccesoEntity.objects.create(
            user_id=identificador_usuario,
            username_attempted=_truncar(username_attempted, LONGITUD_MAXIMA_USERNAME_INTENTADO),
            event_type=event_type,
            operation=_truncar(operation, LONGITUD_MAXIMA_OPERACION),
            outcome=outcome,
            session_id=_truncar(session_id, LONGITUD_MAXIMA_SESSION_ID),
        )
    except Exception as exc:
        # UNA linea y se sigue. No se interpola `str(exc)` en los datos estructurados para no
        # arrastrar valores de bind de Oracle -que incluirian el `username_attempted`- a la
        # traza tecnica; el detalle va en el `exc_info`, que es lo que se investiga.
        logger.error(
            TRAZA_FALLO_REGISTRO,
            exc_info=True,
            extra={
                "data": {
                    "event_type": event_type,
                    "outcome": outcome,
                    "user_id": identificador_usuario,
                    "exception": type(exc).__name__,
                }
            },
        )
