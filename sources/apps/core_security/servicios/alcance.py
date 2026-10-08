"""
Motor de acotacion de alcance de datos: UNICO punto donde se decide si un recurso es visible.

Este modulo traduce el alcance ya resuelto por la matriz de permisos (`OWN` / `ALL`, ARC-102,
TSK-003) en un PREDICADO DE CONSULTA. No decide quien tiene permiso -eso es de
`servicios/permisos.py`- y no vuelve a declarar ninguna regla rol x operacion.

CUATRO INVARIANTES GOBIERNAN ESTE MODULO
----------------------------------------

1. EL FILTRO `OWN` VIAJA AL `WHERE`, NUNCA A MEMORIA (AC-ALC-01, AC-PERM-02).
   El alcance se aplica como `.filter(...)` sobre el queryset, de modo que Oracle devuelve YA
   solo las filas del solicitante. Esta PROHIBIDO el anti-patron de traer filas y descartarlas
   despues con una comprension de lista o un `if fila.reported_by_id == ...`: ademas de hacer
   que la base entregue datos ajenos al alcance, descuadra el recuento total y la paginacion,
   que se calcularian sobre un conjunto mas amplio del que el usuario puede ver.

2. EL ALCANCE NO SE REINVENTA AQUI.
   Se lee de `contexto.data_scope`, que puso la guardia de permisos con
   `ServicioPermisos.alcance_de(contexto, operation_code)` tras consultar
   `permiso_rol_operacion`. En este fichero no hay -ni puede haber- un solo
   `if role_code == ...`: replicar la matriz en codigo garantizaria que algun dia la API
   acota distinto de lo que dice la base.

3. EL PROPIETARIO SALE DE LA SESION, NUNCA DEL CLIENTE (REQ-064).
   El identificador del propietario contra el que se acota es `contexto.user_id`. Si la
   peticion trae un `reporter_user_id` por query string, se IGNORA EN SILENCIO: ver
   `propietario_efectivo`.

4. COSTE DE RESPUESTA INDISTINGUIBLE (AC-PERM-04, PBT-006).
   Incidencia ajena, incidencia inexistente e identificador mal formado ejecutan el MISMO
   numero de consultas, la MISMA consulta y devuelven la MISMA respuesta. Un atajo que
   devolviera 404 sin pasar por la base ante un identificador invalido convertiria el tiempo
   de respuesta en un oraculo. Para evitarlo existe `PK_CENTINELA`.

SIN EFECTO LATERAL EN LA DENEGACION (REQ-031). El camino de denegacion no escribe en base, no
muta el contexto y no emite eventos: solo lanza. Lo unico que deja es una linea de log con el
motivo y el `user_id`, SIN el identificador solicitado, sin correo corporativo y sin ningun
campo de la incidencia (REQ-063, REQ-076).
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import NoReturn

from django.db.models import Model, QuerySet

from apps.core.contexto import AlcanceDatos, ContextoSesion
from apps.core.models.eventos import IncidenciaHistoricoEntity
from apps.core.models.transaccional import IncidenciaAdjuntoEntity, IncidenciaEntity
from apps.core_security.errores import RecursoFueraDeAlcanceError


# Las denegaciones por alcance se trazan aqui con el motivo tecnico; el cuerpo de la respuesta
# lo compone el manejador unico de excepciones y jamas incluye ese motivo.
logger = logging.getLogger(__name__)


class ClaseRecurso(str, Enum):
    """Los tres recursos que comparten el alcance de su incidencia (REQ-023 regla 3, REQ-027)."""

    DETALLE = "DETALLE"
    HISTORIAL = "HISTORIAL"
    ADJUNTO = "ADJUNTO"


#: Campo propietario de `incidencia` (ARC-113). El alcance OWN compara contra el, en SQL.
CAMPO_PROPIETARIO_INCIDENCIA = "reported_by"

#: PK imposible: `incident_id` es NUMBER IDENTITY (always), que empieza en 1 y nunca es
#: negativo, de modo que esta consulta hace el MISMO viaje a Oracle y no casa jamas. Se usa
#: cuando el identificador recibido no es normalizable, para que un identificador mal formado
#: NO salga por un atajo en memoria: si lo hiciera, su respuesta seria medible por tiempo y el
#: cliente distinguiria "mal formado" de "no es tuyo". Es un REQUISITO DE SEGURIDAD
#: (AC-PERM-04, PBT-006), no una rareza de implementacion.
PK_CENTINELA = -1

#: Digitos maximos admitidos en el identificador. `incident_id` es NUMBER sin precision
#: declarada en el DDL (maximo 38 digitos en Oracle): mas alla de ahi el valor no es
#: representable como identidad y se trata como mal formado, sin lanzar.
DIGITOS_MAXIMOS_IDENTIFICADOR = 38

#: Motivos de denegacion. Son TRAZA INTERNA: no se serializan ni cambian la respuesta.
#: `MOTIVO_AJENO_O_INEXISTENTE` cubre a la vez la incidencia ajena y la inexistente porque
#: distinguirlas exigiria una SEGUNDA consulta ("existe? -> y es mia?"), que es exactamente el
#: oraculo que este modulo existe para cerrar: ni el codigo lo sabe, ni debe saberlo.
MOTIVO_AJENO_O_INEXISTENTE = "ajeno_o_inexistente"
MOTIVO_IDENTIFICADOR_INVALIDO = "identificador_invalido"
MOTIVO_SIN_ADJUNTO = "sin_adjunto"


def identificador_normalizado(valor: object) -> int | None:
    """
    Valida el identificador de incidencia ANTES de consultar.

    `incident_id` es NUMBER IDENTITY en Oracle (ARC-113), asi que el formato valido es un
    entero positivo. Devuelve el entero o `None` si el valor no lo es (cadena vacia, texto,
    negativo, cero, longitud extrema, None). NO lanza: quien llama decide, y decide SIEMPRE
    la misma denegacion uniforme.

    Se rechaza `bool` a proposito aunque Python lo considere un `int`: `True` se colaria como
    identificador 1 y serviria un recurso que nadie ha pedido.
    """

    if isinstance(valor, bool) or valor is None:
        return None

    if isinstance(valor, int):
        entero = valor
    elif isinstance(valor, str):
        texto = valor.strip()
        # Solo digitos: ni signo, ni separadores, ni notacion cientifica. Y se corta por
        # longitud ANTES de construir el entero, para que una cadena de millones de digitos no
        # consuma CPU en la conversion (coste de respuesta acotado).
        if not texto.isdigit() or len(texto) > DIGITOS_MAXIMOS_IDENTIFICADOR:
            return None
        entero = int(texto)
    else:
        return None

    if entero <= 0 or len(str(entero)) > DIGITOS_MAXIMOS_IDENTIFICADOR:
        return None
    return entero


def es_alcance_propio(data_scope: str | None) -> bool:
    """
    True si el alcance es OWN (acota al propietario).

    Fail-closed: un alcance ausente o desconocido se trata como OWN, el MENOR privilegio; nunca
    se degrada a ALL. Solo el valor exacto `ALL` del enumerado del dominio abre la consulta, de
    modo que un `data_scope` corrupto, vacio o inventado jamas ensancha lo que se ve.
    """

    return data_scope != AlcanceDatos.ALL.value


def propietario_efectivo(contexto: ContextoSesion, reporter_user_id: object = None) -> int:
    """
    Devuelve el propietario contra el que se acota: SIEMPRE `contexto.user_id` (REQ-064).

    `reporter_user_id` es el parametro que un cliente podria enviar por query string para pedir
    "las incidencias de otro". Se acepta en la firma unicamente para dejar escrito que SE
    IGNORA EN SILENCIO: no se valida, no se compara y no provoca error. Devolver un 400 seria
    peor, porque confirmaria que el parametro significa algo para el servidor e invitaria a
    seguir probando; aqui simplemente no existe como entrada de decision.
    """

    del reporter_user_id  # Entrada del cliente deliberadamente descartada (REQ-064).
    return contexto.user_id


def predicado_de_propiedad(contexto: ContextoSesion, *, campo_propietario: str = CAMPO_PROPIETARIO_INCIDENCIA) -> dict[str, int]:
    """
    Devuelve los `kwargs` de filtro que acotan al propietario, o `{}` si el alcance es ALL.

    Es la UNICA construccion del predicado del modulo: la comparten `acotar` y la busqueda por
    identificador, para que la lista y el detalle no puedan acotar con criterios distintos.
    """

    if not es_alcance_propio(contexto.data_scope):
        return {}
    return {campo_propietario: propietario_efectivo(contexto)}


def acotar(
    queryset: QuerySet,
    contexto: ContextoSesion,
    *,
    campo_propietario: str = CAMPO_PROPIETARIO_INCIDENCIA,
) -> QuerySet:
    """
    Devuelve el queryset con el predicado de propiedad APLICADO EN LA CONSULTA.

    Con alcance ALL devuelve el queryset intacto; con OWN anade
    `.filter(**{campo_propietario: contexto.user_id})`. El predicado viaja al WHERE de Oracle:
    el recuento, la paginacion y el total se calculan YA acotados (AC-ALC-01, AC-PERM-02).

    El queryset sigue siendo PEREZOSO al salir de aqui: no se evalua, no se materializa y no se
    cuenta. Quien llama puede ordenarlo y paginarlo encima, y el predicado seguira estando en la
    misma sentencia SQL.
    """

    predicado = predicado_de_propiedad(contexto, campo_propietario=campo_propietario)
    if not predicado:
        return queryset
    return queryset.filter(**predicado)


class ServicioAlcance:
    """
    Unico punto donde se decide si un recurso esta dentro del alcance del solicitante.

    Las tres lecturas de incidencia (detalle, historial y adjunto) pasan por la MISMA
    resolucion, `incidencia_en_alcance`, para que compartan criterio y denegacion: el historial
    y el adjunto NO tienen alcance propio, heredan el de su incidencia (REQ-023 regla 3,
    REQ-027 regla 3).
    """

    def consulta_acotada(self, contexto: ContextoSesion, *, modelo: type[Model] | None = None) -> QuerySet:
        """
        Devuelve el queryset de incidencias YA acotado por el alcance de la sesion.

        Es el punto de partida de cualquier listado: quien pagine o cuente sobre lo que devuelve
        esta contando sobre el conjunto acotado, nunca sobre el total del sistema. `modelo`
        permite reutilizar el motor con otra entidad que tenga el mismo campo propietario; por
        defecto es `IncidenciaEntity`.
        """

        entidad = modelo if modelo is not None else IncidenciaEntity
        return acotar(entidad.objects.all(), contexto)

    def incidencia_en_alcance(self, identificador: object, contexto: ContextoSesion) -> IncidenciaEntity:
        """
        Devuelve la incidencia pedida SI esta dentro del alcance; si no, deniega uniformemente.

        UNA SOLA CONSULTA, SIEMPRE LA MISMA. La identidad y la propiedad se comprueban en la
        MISMA sentencia (`filter(pk=..., reported_by=...)` -> `first()`). Esta PROHIBIDO el
        patron de dos pasos "existe? -> y es mia?": revelaria la existencia del recurso por el
        numero de consultas y por el tiempo de respuesta, que es justo lo que AC-PERM-04 y
        PBT-006 cierran. Por eso tampoco se distingue en el motivo entre ajena e inexistente:
        el codigo no lo sabe porque no lo pregunta.

        IDENTIFICADOR MAL FORMADO: SIN ATAJOS. Si `identificador_normalizado` devuelve `None`,
        no se retorna antes de consultar: se consulta con `PK_CENTINELA`, que hace el mismo
        viaje a Oracle y no casa jamas. Los tres casos ejecutan la misma consulta.

        No hay efecto lateral en la denegacion (REQ-031): solo se lanza
        `RecursoFueraDeAlcanceError` y se traza el motivo con el `user_id`, nunca el
        identificador pedido ni ningun campo de la incidencia (REQ-063, REQ-076).
        """

        normalizado = identificador_normalizado(identificador)
        motivo = MOTIVO_AJENO_O_INEXISTENTE if normalizado is not None else MOTIVO_IDENTIFICADOR_INVALIDO
        clave = normalizado if normalizado is not None else PK_CENTINELA

        incidencia = IncidenciaEntity.objects.filter(pk=clave, **predicado_de_propiedad(contexto)).first()
        if incidencia is None:
            self._denegar(motivo, contexto)
        return incidencia

    def historial_en_alcance(self, identificador: object, contexto: ContextoSesion) -> QuerySet:
        """
        Devuelve el historial de la incidencia pedida, previa resolucion de su alcance.

        El historial NO se consulta nunca por su cuenta: primero se resuelve la incidencia con
        `incidencia_en_alcance` (mismo alcance, misma denegacion uniforme) y solo despues se
        leen sus entradas. Asi una incidencia ajena no filtra su trazabilidad -que contiene
        nombres de actores y comentarios- por la puerta de atras.

        Un historial vacio NO es una denegacion: la incidencia esta en alcance, de modo que se
        devuelve el queryset vacio. Devuelve queryset y no lista para que la ordenacion y la
        paginacion sigan resolviendose en la base.
        """

        incidencia = self.incidencia_en_alcance(identificador, contexto)
        return IncidenciaHistoricoEntity.objects.filter(incident_id=incidencia.pk)

    def adjunto_en_alcance(self, identificador: object, contexto: ContextoSesion) -> IncidenciaAdjuntoEntity:
        """
        Devuelve el adjunto de la incidencia pedida, previa resolucion de su alcance.

        El adjunto NUNCA se sirve sin evaluar el alcance de SU incidencia (REQ-027 regla 3) y
        nunca se consulta por `attachment_id` suelto: se resuelve antes la incidencia con
        `incidencia_en_alcance` y solo despues se lee el adjunto por `incident_id`. Un
        identificador de adjunto adivinado no puede, por tanto, saltarse el alcance.

        DECISION EXPLICITA SOBRE LA INCIDENCIA SIN ADJUNTO. Si la incidencia esta en alcance
        pero no tiene adjunto, se lanza igualmente `RecursoFueraDeAlcanceError` con
        `motivo="sin_adjunto"`. El brief de REQ-027 preve un texto distinto para ese caso, pero
        prevalece la INDISTINGUIBILIDAD de PBT-006 y AC-PERM-04: un mensaje propio para "existe
        pero no tiene adjunto" confirmaria la existencia de la incidencia a quien no la puede
        ver, y seria un oraculo mas barato todavia que el del detalle. El matiz se conserva
        donde no hace dano: en el `motivo` de la traza interna.
        """

        incidencia = self.incidencia_en_alcance(identificador, contexto)
        adjunto = IncidenciaAdjuntoEntity.objects.filter(incident_id=incidencia.pk).first()
        if adjunto is None:
            self._denegar(MOTIVO_SIN_ADJUNTO, contexto)
        return adjunto

    def _denegar(self, motivo: str, contexto: ContextoSesion) -> NoReturn:
        """
        Traza y lanza la denegacion uniforme. No escribe en base ni muta nada (REQ-031).

        La linea de log lleva SOLO el motivo tecnico y el `user_id`: ni el identificador
        solicitado -que permitiria reconstruir desde el log que incidencias ajenas se han
        tanteado-, ni el correo corporativo, ni ningun campo de la incidencia (REQ-063,
        REQ-076).
        """

        logger.info("Denegacion por alcance: motivo=%s user_id=%s", motivo, contexto.user_id)
        raise RecursoFueraDeAlcanceError(motivo=motivo)
