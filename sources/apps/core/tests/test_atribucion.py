"""
Pruebas del mixin de atribucion (REQ-064, AC-TRZ-01).

Verifican la invariante transversal del servicio: el actor y la marca temporal de una
accion registrable salen SIEMPRE del contexto de sesion y del reloj del servidor, nunca
del payload del cliente. Un `user_id` ajeno enviado en el cuerpo se ignora.

No tocan base de datos a proposito: `aplicar_atribucion()` es el metodo que `save()`
invoca por dentro, de modo que se puede ejercitar sobre instancias en memoria y la suite
corre en cualquier clon limpio, sin Oracle.
"""

from datetime import datetime

import pytest

from apps.core.contexto import ContextoSesion, utc_now
from apps.core.errores import ContextoSesionNoDisponibleError
from apps.core.models.eventos import IncidenciaHistoricoEntity, UsuarioHistoricoEntity
from apps.core.models.transaccional import IncidenciaEntity, UsuarioEntity

#: `user_id` ajeno al de la sesion que el cliente intenta colar en el cuerpo de la peticion.
USER_ID_AJENO_DEL_PAYLOAD = 99999

#: Marca temporal antedatada que el cliente intenta colar junto al actor suplantado.
FECHA_FALSA_DEL_PAYLOAD = datetime(2000, 1, 1)

#: Entidades del esquema T.5 que heredan `AtribucionMixin` (los cuatro casos probados).
MODELOS_CON_ATRIBUCION = [
    UsuarioEntity,
    IncidenciaEntity,
    UsuarioHistoricoEntity,
    IncidenciaHistoricoEntity,
]


def _atributo_de_escritura(modelo: type, nombre_campo: str) -> str:
    """
    Devuelve el atributo que guarda el valor crudo del campo (`attname`).

    Para una clave foranea el `attname` es la columna `_id` (p. ej. `reported_by_id`), que es
    donde el mixin escribe el identificador del actor sin necesidad de cargar la instancia.
    """

    return modelo._meta.get_field(nombre_campo).attname


@pytest.mark.parametrize("modelo", MODELOS_CON_ATRIBUCION, ids=lambda m: m.__name__)
def test_AC_TRZ_01_la_atribucion_sale_de_la_sesion_y_no_del_payload(
    modelo: type, sesion_activa: ContextoSesion
) -> None:
    """
    [AC-TRZ-01] Con una sesion iniciada, un `user_id` ajeno en el cuerpo se ignora.

    La entidad se construye con el actor y la fecha que "envia" el cliente ya puestos; tras
    `aplicar_atribucion(es_alta=True)` el dato persistido debe ser el `user_id` de la sesion
    y una marca temporal del servidor (UTC naive), no la antedatada del payload.
    """

    atributo_actor = _atributo_de_escritura(modelo, modelo.CAMPO_ACTOR_ALTA)
    atributo_fecha = _atributo_de_escritura(modelo, modelo.CAMPO_FECHA_ALTA)

    entidad = modelo()
    setattr(entidad, atributo_actor, USER_ID_AJENO_DEL_PAYLOAD)
    setattr(entidad, atributo_fecha, FECHA_FALSA_DEL_PAYLOAD)

    antes = utc_now()
    entidad.aplicar_atribucion(es_alta=True)
    despues = utc_now()

    actor_persistido = getattr(entidad, atributo_actor)
    assert actor_persistido == sesion_activa.user_id, (
        f"{modelo.__name__}.{atributo_actor} deberia atribuirse al usuario de la sesion"
    )
    assert actor_persistido != USER_ID_AJENO_DEL_PAYLOAD, (
        f"{modelo.__name__}.{atributo_actor} ha aceptado el actor suplantado del payload"
    )

    fecha_persistida = getattr(entidad, atributo_fecha)
    assert fecha_persistida != FECHA_FALSA_DEL_PAYLOAD, (
        f"{modelo.__name__}.{atributo_fecha} ha aceptado la fecha antedatada del payload"
    )
    assert fecha_persistida.tzinfo is None, "la marca temporal debe ser UTC naive (USE_TZ = False)"
    assert antes <= fecha_persistida <= despues, (
        f"{modelo.__name__}.{atributo_fecha} no procede del reloj del servidor"
    )


@pytest.mark.parametrize("modelo", MODELOS_CON_ATRIBUCION, ids=lambda m: m.__name__)
def test_AC_TRZ_01_sin_contexto_de_sesion_no_se_atribuye_nada(modelo: type) -> None:
    """
    [AC-TRZ-01] Fuera de una sesion activa no hay atribucion posible: se rompe la operacion.

    Deny-by-default: antes que inventar un actor (o aceptar el del payload), el mixin lanza
    `ContextoSesionNoDisponibleError` y deja la entidad con el valor ajeno sin promocionar.
    """

    atributo_actor = _atributo_de_escritura(modelo, modelo.CAMPO_ACTOR_ALTA)

    entidad = modelo()
    setattr(entidad, atributo_actor, USER_ID_AJENO_DEL_PAYLOAD)

    with pytest.raises(ContextoSesionNoDisponibleError):
        entidad.aplicar_atribucion(es_alta=True)
