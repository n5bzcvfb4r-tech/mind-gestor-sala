"""
Traza del restablecimiento administrativo de credencial en `auditoria_acceso` (REQ-073, REQ-075).

QUE SE REGISTRA Y POR QUE AQUI
------------------------------
UN solo hecho observable: el ADMINISTRADOR ha restablecido la credencial de OTRO usuario y la
operacion ha quedado confirmada (`PASSWORD_RESET`, REQ-073 regla 5). Se escribe desde este unico
modulo, igual que hace el paquete de bloqueo con sus tres eventos, para que la evidencia se
produzca siempre con la misma forma: si cada rama del servicio compusiera su propia fila, los
valores de `event_type` y `outcome` divergirian a la primera correccion y la traza dejaria de ser
consultable con un criterio homogeneo.

NO SE CREA NINGUNA TABLA. Se escribe en `auditoria_acceso` (ARC-117), que ya existe en el
inventario T.5 y esta mapeada por `apps.core.models.AuditoriaAccesoEntity` con `managed=False`.
`event_type` y `outcome` salen del enumerado de ARC-117; no se inventa ningun valor nuevo, porque
un valor fuera del catalogo es evidencia que nadie sabe interpretar despues.

QUIEN ES EL SUJETO Y QUIEN EL ACTOR
-----------------------------------
La fila se atribuye al usuario DESTINO (`user_id`), que es sobre quien recae el efecto: es la
unica forma de responder «que le ha pasado a esta cuenta» leyendo su historial. El ADMINISTRADOR
que la ejecuta queda identificado por `session_id`, el identificador de SU sesion, que es lo que
permite remontar hasta el actor sin duplicar la atribucion en una columna que ARC-117 no tiene.

POR QUE ES BEST-EFFORT (mismo criterio que `apps.identidad.bloqueo.auditoria`)
------------------------------------------------------------------------------
La traza es una CONSECUENCIA del restablecimiento, no parte de el. Cuando esta funcion se invoca,
el correo con el acceso temporal YA se ha entregado al usuario: si el INSERT falla -Oracle caido,
pool agotado, tabla bloqueada- y la excepcion escapara, tumbaria una operacion que para el usuario
ya es un hecho consumado y le dejaria una credencial nueva anunciada con un 500 por respuesta. Por
eso el `create(...)` va dentro de un `try/except Exception` que deja UNA linea `logger.error(...)`
con `exc_info` y devuelve sin propagar. El hueco de evidencia queda en el log del servicio.

SECRETOS (REQ-063, REQ-076, REQ-079)
------------------------------------
La credencial temporal NO se recibe en esta funcion, NO se guarda y NO se escribe en el log: la
firma no tiene ningun parametro por el que pudiera colarse, y es deliberado -lo que no se recibe
no se puede filtrar-. Tampoco se registran `password_hash`, el nombre del usuario ni el `motivo`
que tecleo el administrador. `username_attempted` se deja a `None` a proposito: aqui no hay ningun
identificador «tecleado» que investigar -el usuario viene resuelto por su `user_id`- y el correo
corporativo es dato personal que REQ-079 mantiene fuera de la traza.
"""

from __future__ import annotations

import logging

from apps.core.contexto import ContextoSesion


logger = logging.getLogger(__name__)

#: `event_type` del enumerado cerrado de `auditoria_acceso` (ARC-117) para el restablecimiento
#: administrativo de credencial. Es el mismo valor que publica el catalogo de avisos para el
#: aviso correspondiente, y no es casualidad: describen el mismo hecho del sistema.
EVENTO_PASSWORD_RESET = "PASSWORD_RESET"

# Valor del enumerado `auditoria_acceso.outcome` (ARC-117). Solo se registra `OK`: esta funcion la
# invoca el servicio DESPUES de confirmar el restablecimiento, de modo que no hay un desenlace
# denegado que escribir desde aqui. El parametro existe igualmente para no cerrar la firma a un
# literal, pero su valor por defecto es el unico caso que hoy se da.
OUTCOME_OK = "OK"

# Topes de las columnas del DDL. Se trunca en Python porque Oracle no recorta: rechaza el INSERT
# entero (ORA-12899) y perderiamos la evidencia de un restablecimiento ya consumado por un valor
# unos caracteres mas largo de lo que cabe.
LONGITUD_MAXIMA_OPERACION = 100  # VARCHAR2(100) de auditoria_acceso.operation
LONGITUD_MAXIMA_SESSION_ID = 36  # VARCHAR2(36): uuid

#: Operacion funcional asociada al evento, en el mismo estilo que el `USER_UNLOCK` del desbloqueo.
OPERACION_RESTABLECIMIENTO = "USER_PASSWORD_RESET"

TRAZA_FALLO_REGISTRO = "No se pudo registrar el restablecimiento de credencial en auditoria_acceso."


__all__ = [
    "EVENTO_PASSWORD_RESET",
    "LONGITUD_MAXIMA_OPERACION",
    "LONGITUD_MAXIMA_SESSION_ID",
    "OPERACION_RESTABLECIMIENTO",
    "OUTCOME_OK",
    "registrar_restablecimiento",
]


def _truncar(valor: object, limite: int) -> str | None:
    """Normaliza a texto y recorta al tope de la columna; devuelve `None` si no hay nada que guardar."""

    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    return texto[:limite]


def registrar_restablecimiento(
    *,
    usuario: object,
    actor: ContextoSesion,
    outcome: str = OUTCOME_OK,
) -> None:
    """
    Inserta UNA fila en `auditoria_acceso` por cada restablecimiento confirmado, sin romper el flujo.

    Es la unica puerta de escritura de traza del paquete `apps.identidad.reposicion`. Quien llama
    no compone nada: el truncado, la resolucion de los identificadores y la proteccion frente a
    fallos de base se resuelven aqui.

    Args:
        usuario: entidad `UsuarioEntity` DESTINO del restablecimiento. De ella se toma UNICAMENTE
            `user_id`; nunca `password_hash`, `corporate_email` ni `full_name` (REQ-079).
        actor: `ContextoSesion` del ADMINISTRADOR que ejecuta la operacion. De el se toma SOLO el
            `session_id`, que es el identificador de su sesion y jamas su credencial.
        outcome: valor del enumerado `auditoria_acceso.outcome`. Por defecto `OUTCOME_OK`, porque
            esta traza se escribe cuando el restablecimiento ya esta confirmado.

    No devuelve nada y NO PROPAGA: cuando se llega aqui el correo con el acceso temporal ya salio,
    y un fallo al dejar la evidencia no puede convertir esa operacion consumada en un 500.
    `occurred_at` no se informa -lo sella el servidor (REQ-064)- y `retention_until` es columna
    VIRTUAL del DDL, asi que tampoco se toca.
    """

    # Se leen los atributos de la clave ajena para no obligar a una consulta extra solo para dejar
    # la traza. `getattr` con defecto: un actor sin sesion asociada -o un usuario que llegue como
    # None por un error del llamante- no puede impedir que se registre el hecho.
    identificador_usuario = getattr(usuario, "user_id", None) if usuario is not None else None
    identificador_sesion = getattr(actor, "session_id", None) if actor is not None else None

    try:
        # El modelo se importa AQUI DENTRO y no al cargar el modulo: este fichero lo tocan
        # servicios que se resuelven antes de `django.setup()`, y un import de modelos a nivel de
        # modulo reventaria el arranque (`AppRegistryNotReady`).
        from apps.core.models import AuditoriaAccesoEntity

        AuditoriaAccesoEntity.objects.create(
            user_id=identificador_usuario,
            # `username_attempted` queda a None: el destino esta identificado por su `user_id` y el
            # correo corporativo no va a auditoria (REQ-079).
            username_attempted=None,
            event_type=EVENTO_PASSWORD_RESET,
            operation=_truncar(OPERACION_RESTABLECIMIENTO, LONGITUD_MAXIMA_OPERACION),
            outcome=outcome,
            session_id=_truncar(identificador_sesion, LONGITUD_MAXIMA_SESSION_ID),
        )
    except Exception as exc:
        # UNA linea y se sigue. No se interpola `str(exc)` en los datos estructurados para no
        # arrastrar valores de bind de Oracle a la traza tecnica; el detalle va en el `exc_info`,
        # que es lo que se investiga. Ni la credencial, ni el correo, ni el hash pasan por aqui.
        logger.error(
            TRAZA_FALLO_REGISTRO,
            exc_info=True,
            extra={
                "data": {
                    "event_type": EVENTO_PASSWORD_RESET,
                    "outcome": outcome,
                    "user_id": identificador_usuario,
                    "exception": type(exc).__name__,
                }
            },
        )
