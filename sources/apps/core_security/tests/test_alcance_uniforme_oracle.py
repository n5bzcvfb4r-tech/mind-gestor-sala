"""
PBT-006 - No revelacion de recursos protegidos por alcance de datos (REQ-023, REQ-031, AC-PERM-04).

PROPIEDAD METAMORFICA DE NO REVELACION. La propiedad que se verifica aqui no es "tal caso
devuelve tal codigo", sino que TRES ENTRADAS DISTINTAS PRODUCEN LA MISMA SALIDA: pedir una
incidencia AJENA, pedir una incidencia INEXISTENTE y pedir un IDENTIFICADOR MAL FORMADO deben
responder exactamente lo mismo. "Lo mismo" se mide en cuatro dimensiones, y basta que una sola
difiera para que el atacante tenga un oraculo con el que enumerar incidencias que no puede ver:

  1. CODIGO de estado HTTP identico.
  2. CUERPO de la respuesta identico, byte a byte (ni mensajes distintos, ni campos de detalle,
     ni el identificador solicitado devuelto como eco).
  3. CABECERAS identicas en las que dependen del caso (sin `X-...` delatoras, misma forma).
  4. COSTE DE RESPUESTA indistinguible: el MISMO numero de consultas a la base y la MISMA
     consulta. Un atajo en memoria ante el identificador mal formado -devolver 404 sin viajar a
     Oracle- convertiria el tiempo de respuesta en el oraculo que esta suite cierra; de ahi
     `PK_CENTINELA` en la capa de alcance.

La propiedad se comprueba sobre los TRES RECURSOS EQUIVALENTES que comparten el alcance de su
incidencia (REQ-023 regla 3, REQ-027 regla 3): DETALLE, HISTORIAL y ADJUNTO. El historial y el
adjunto no tienen alcance propio: lo heredan, y por tanto deben denegar igual que el detalle.

MOTOR REAL, NUNCA SUSTITUTO. Las pruebas que tocan base se ejecutan contra ORACLE 23ai FREE REAL
levantado con Testcontainers (`gvenzl/oracle-free:23-slim`). Esta PROHIBIDO sustituirlo por H2,
SQLite o cualquier doble en memoria: el recuento de consultas, el comportamiento de
`NUMBER IDENTITY` y la semantica del `WHERE` acotado son justo lo que se esta verificando, y un
backend distinto los falsea. Si no hay engine Docker alcanzable, las pruebas que tocan la base se
SALTAN con el motivo `SALTAR_SIN_DOCKER` -nunca se degradan a otro backend ni se dan por verdes-.

REUTILIZACION DE FIXTURES. La cadena de fixtures de contenedor (arranque de Oracle, migraciones y
datos de apoyo) se REUTILIZA del `conftest.py` del paquete; este modulo no la duplica ni la
redefine, para que todas las suites de integracion compartan un unico contenedor y un unico
criterio de salto.
"""

import json
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pytest
from django.db import connections
from django.db.models import Max
from django.test.utils import CaptureQueriesContext
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.core.contexto import AlcanceDatos, ContextoSesion, contexto_de_sesion
from apps.core.models import (
    CategoriaIncidenciaEntity,
    EstadoIncidenciaEntity,
    IncidenciaAdjuntoEntity,
    IncidenciaEntity,
    SalaEntity,
    UsuarioEntity,
)
from apps.core_security.alcance import AlcanceRecursoMixin
from apps.core_security.servicios.alcance import identificador_normalizado
from apps.core_security.tests.conftest import MOTIVO_SIN_DOCKER, SALTAR_SIN_DOCKER
from apps.core_security.tests.test_contexto_autorizacion_oracle import ROL_EMPLEADO, sembrar_usuario

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------------------------
# CORPUS DE IDENTIFICADORES
#
# La propiedad de no revelacion se enuncia sobre TODO el espacio de identificadores posibles,
# pero este proyecto NO tiene Hypothesis en `pyproject.toml` y esta tarea no anade dependencias.
# El espacio se cubre, por tanto, con un corpus DETERMINISTA y EXHAUSTIVO POR CLASES DE
# EQUIVALENCIA: una entrada por cada forma de "no ser un entero positivo" que
# `identificador_normalizado` distingue (vacio, blanco, texto, uuid, negativo, cero, decimal,
# con signo, longitud extrema e inyeccion). Es una eleccion consciente, no una limitacion
# disimulada: un corpus fijo es ademas REPRODUCIBLE -un fallo se reproduce con el mismo dato,
# sin semilla aleatoria- y mantiene acotado el numero de viajes al Oracle real.
# ---------------------------------------------------------------------------------------------

#: Identificadores MAL FORMADOS: ninguno es un entero positivo, asi que ninguno puede designar
#: fila alguna. La capa de alcance NO debe atajarlos en memoria: debe consultar con
#: `PK_CENTINELA` y responder exactamente lo mismo que ante una incidencia ajena.
IDENTIFICADORES_MAL_FORMADOS: tuple[str, ...] = (
    "",  # cadena vacia: el cliente no envia nada en el segmento de ruta
    "   ",  # solo espacios en blanco
    "no-es-un-numero",  # texto arbitrario
    "3f6a1d9e-5c1b-4a8e-9f2d-7b0c4e1a8d35",  # uuid: identificador valido de OTRO dominio
    "-1",  # entero negativo
    "0",  # cero: `incident_id` es IDENTITY y empieza en 1
    "1.5",  # decimal
    "+3",  # entero con signo explicito
    "9" * 5000,  # longitud extrema: no debe consumir CPU ni reventar la consulta
    "1 OR 1=1",  # intento de inyeccion: viaja parametrizado y no casa con nada
)

#: Identificadores INEXISTENTES: enteros con FORMATO VALIDO que no corresponden a ninguna fila.
#: NO son constantes magicas. Una constante magica (`999999`) es un defecto conocido en este tipo
#: de prueba: contra una base con datos reales puede coincidir con una incidencia viva y
#: convertir el caso "inexistente" en un caso "ajeno" -verde por accidente y por el motivo
#: equivocado-. Se calculan en tiempo de prueba a partir de `MAX(incident_id)` de la tabla (ver
#: la fixture `identificadores_inexistentes`), de modo que la ausencia la GARANTIZA la propia
#: base y no una suposicion del autor de la prueba.
DESPLAZAMIENTOS_SOBRE_EL_MAXIMO: tuple[int, ...] = (1, 2, 7, 1_000, 10_000_000)


# ---------------------------------------------------------------------------------------------
# BANCO DE PRUEBAS HTTP
# ---------------------------------------------------------------------------------------------

#: Prefijo de ruta del banco de pruebas. NO esta enrutado en `urls.py` y no pretende estarlo:
#: `APIRequestFactory` invoca la vista directamente, sin resolver URL.
RUTA_BANCO_DE_PRUEBAS = "/_banco-de-pruebas/alcance"

#: Fabrica de peticiones de DRF. Se invoca la vista con ella -y no con `APIClient`- porque esta
#: tarea NO monta rutas: lo que se ejercita es la vista mas el ciclo de DRF (`dispatch`,
#: `handle_exception`, `finalize_response`), que es donde actua el manejador unico de errores.
FABRICA_DE_PETICIONES = APIRequestFactory()

#: Cabeceras que NO entran en la huella comparable. `Date` y `Server` las compone el servidor con
#: el instante y el software de turno: difieren entre dos respuestas IDENTICAS y compararlas
#: produciria un falso rojo. No son canal de fuga para esta propiedad porque no dependen del CASO
#: (ajeno / inexistente / mal formado) sino del momento. Cualquier otra cabecera -incluidas
#: `Content-Type`, `Content-Length`, `Allow` o `Vary`- SI se compara: una `Content-Length`
#: distinta entre dos denegaciones ya seria un oraculo.
CABECERAS_VOLATILES: frozenset[str] = frozenset({"date", "server"})

#: Clave del correlador de peticion dentro del cuerpo canonico de error.
CLAVE_TRACE_ID = "traceId"


class _DetalleDePrueba(AlcanceRecursoMixin, APIView):
    """
    Vista de DETALLE del banco de pruebas de la capa de alcance.

    No son codigo de producto ni rutas del contrato; son el banco de pruebas de la capa de
    alcance, montadas tal y como el docstring de `AlcanceRecursoMixin` prescribe para las TSK
    duenas de EP-028/029/030. Esta tarea entrega la CAPA, no los endpoints: el `openapi.yaml`
    asigna EP-028, EP-029 y EP-030 a otras tareas, y por eso aqui no se registra ninguna URL.

    `permission_classes` y `authentication_classes` van VACIAS a proposito: la autorizacion
    (matriz `permiso_rol_operacion`, guardia `requiere(...)`) ya la acreditan las pruebas de
    TSK-003, y dejarla puesta aqui mezclaria dos responsabilidades en el mismo oraculo. Lo que
    se aisla es la ACOTACION DE ALCANCE y su denegacion uniforme. El `ContextoSesion` lo publica
    el helper `invocar` en `request.contexto_sesion`, que es exactamente donde lo deja la guardia
    `PermisoOperacion` en produccion.

    El camino de EXITO existe para que la ASIMETRIA sea visible: si la vista denegase siempre,
    comparar entre si las respuestas de denegacion saldria verde sin probar nada.
    """

    permission_classes: list = []
    authentication_classes: list = []

    def get(self, request: Any, identificador: object) -> Response:
        """Devuelve el identificador de la incidencia en alcance; fuera de alcance deja propagar."""

        incidencia = self.incidencia_en_alcance(request, identificador)
        return Response({"incidentId": incidencia.incident_id})


class _HistorialDePrueba(AlcanceRecursoMixin, APIView):
    """
    Vista de HISTORIAL del banco de pruebas de la capa de alcance.

    No son codigo de producto ni rutas del contrato; son el banco de pruebas de la capa de
    alcance, montadas tal y como el docstring de `AlcanceRecursoMixin` prescribe para las TSK
    duenas de EP-028/029/030.

    El historial NO tiene alcance propio: hereda el de su incidencia (REQ-023 regla 3), de modo
    que debe denegar EXACTAMENTE igual que el detalle. El camino de exito devuelve el numero de
    entradas; un historial vacio es exito (0 entradas), nunca una denegacion.
    """

    permission_classes: list = []
    authentication_classes: list = []

    def get(self, request: Any, identificador: object) -> Response:
        """Devuelve el recuento de entradas del historial en alcance; fuera de alcance deja propagar."""

        historial = self.historial_en_alcance(request, identificador)
        return Response({"entradas": historial.count()})


class _AdjuntoDePrueba(AlcanceRecursoMixin, APIView):
    """
    Vista de ADJUNTO del banco de pruebas de la capa de alcance.

    No son codigo de producto ni rutas del contrato; son el banco de pruebas de la capa de
    alcance, montadas tal y como el docstring de `AlcanceRecursoMixin` prescribe para las TSK
    duenas de EP-028/029/030.

    El adjunto tampoco tiene alcance propio (REQ-027 regla 3) y nunca se busca por su
    `attachment_id`: el servicio resuelve antes la incidencia. El camino de exito devuelve el
    identificador del adjunto.
    """

    permission_classes: list = []
    authentication_classes: list = []

    def get(self, request: Any, identificador: object) -> Response:
        """Devuelve el identificador del adjunto en alcance; fuera de alcance deja propagar."""

        adjunto = self.adjunto_en_alcance(request, identificador)
        return Response({"attachmentId": adjunto.attachment_id})


# ---------------------------------------------------------------------------------------------
# HELPERS DE INVOCACION Y DE COMPARACION
# ---------------------------------------------------------------------------------------------


def invocar(vista: type[APIView], identificador: object, contexto: ContextoSesion) -> Response:
    """
    Invoca la vista con `APIRequestFactory` y devuelve la respuesta YA RENDERIZADA.

    El `contexto` se publica en `request.contexto_sesion`, que es el MISMO sitio donde lo deja la
    guardia `PermisoOperacion` en produccion: la prueba no parchea el mixin ni le pasa el alcance
    por un atajo, de modo que lo que se ejercita es el cableado real.

    La respuesta se devuelve renderizada (`.render()`) a proposito. Una `Response` de DRF sin
    renderizar no tiene `content`, y comparar `respuesta.data` compararia ESTRUCTURAS DE PYTHON,
    no lo que viaja por el cable: dos diccionarios iguales pueden serializarse distinto (orden de
    claves, escapado de tildes, `Content-Length`). Como la propiedad que se verifica es que el
    cliente no pueda distinguir dos respuestas, hay que comparar BYTES y CABECERAS.

    El identificador viaja ademas en la ruta tal cual lo escribiria el cliente (percent-encoded),
    para que la traza del manejador unico vea una peticion verosimil; la vista lo recibe por
    `kwargs`, sin normalizar, porque normalizarlo aqui seria validarlo fuera de la capa que se
    esta probando.
    """

    segmento = quote(str(identificador), safe="")
    peticion = FABRICA_DE_PETICIONES.get(f"{RUTA_BANCO_DE_PRUEBAS}/{segmento}")
    # Mismo punto de publicacion que la guardia real (`request.contexto_sesion`).
    peticion.contexto_sesion = contexto
    respuesta = vista.as_view()(peticion, identificador=identificador)
    respuesta.render()
    return respuesta


def cuerpo_de(respuesta: Response) -> dict[str, Any]:
    """Devuelve el cuerpo JSON renderizado de la respuesta; `{}` si no trae cuerpo."""

    contenido = respuesta.content.decode("utf-8")
    if not contenido:
        return {}
    return json.loads(contenido)


def trace_id_de(respuesta: Response) -> str:
    """Devuelve el `traceId` del cuerpo canonico de error, o cadena vacia si no viene."""

    return str(cuerpo_de(respuesta).get(CLAVE_TRACE_ID, ""))


def cabeceras_comparables(respuesta: Response) -> dict[str, str]:
    """
    Devuelve las cabeceras normalizadas a minusculas, sin las volatiles (`CABECERAS_VOLATILES`).

    Se normaliza el nombre porque HTTP lo define insensible a mayusculas: una diferencia de
    capitalizacion no es informacion que el cliente pueda explotar, pero si un falso rojo.
    """

    return {
        str(nombre).lower(): str(valor)
        for nombre, valor in respuesta.items()
        if str(nombre).lower() not in CABECERAS_VOLATILES
    }


def huella(respuesta: Response) -> dict[str, Any]:
    """
    Extrae la FIRMA COMPARABLE de una respuesta: lo que un cliente podria usar como oraculo.

    La firma la componen tres cosas: el `status_code`, el cuerpo JSON y las cabeceras
    comparables. Dos respuestas con la misma huella son indistinguibles para quien solo ve HTTP.

    EL `traceId` SE EXCLUYE DEL CUERPO, Y NO ES UNA CONCESION. Por contrato
    (`respuestas.cuerpo_error`) es un correlador NUEVO EN CADA PETICION -un uuid4 por respuesta-,
    cuyo fin es cruzar lo que ve el usuario con la linea de log tecnica. Comparar dos respuestas
    incluyendolo seria comparar dos uuid distintos y fallar SIEMPRE, incluso con la propiedad
    perfectamente cumplida: no mide la fuga, mide el reloj. Como su valor es aleatorio y no
    depende del caso, tampoco es un canal por el que se pueda distinguir "ajena" de "inexistente".
    Lo que SI hay que verificar -y se verifica aparte, con `trace_id_de`- es que este SIEMPRE
    presente y no vacio en toda denegacion: un `traceId` ausente en un caso y presente en otro
    volveria a ser un oraculo, y ademas dejaria sin correlador una denegacion real en produccion.
    """

    cuerpo = cuerpo_de(respuesta)
    cuerpo.pop(CLAVE_TRACE_ID, None)
    return {
        "status_code": respuesta.status_code,
        "cuerpo": cuerpo,
        "cabeceras": cabeceras_comparables(respuesta),
    }


# ---------------------------------------------------------------------------------------------
# SEMILLA EN EL ORACLE REAL
#
# Todo lo que sigue siembra filas REALES en el Oracle 23ai del contenedor, dentro de la
# transaccion que `db` revierte al terminar cada prueba. Esta PROHIBIDO sustituirlo por H2,
# SQLite o un doble en memoria: lo que se verifica es que el predicado de propiedad viaja al
# WHERE y que las tres entradas producen la MISMA consulta, y eso no se puede acreditar contra
# un `dict`.
# ---------------------------------------------------------------------------------------------

#: Misma condicion que gobierna `SALTAR_SIN_DOCKER`, extraida de la marca para poder usarla
#: tambien FUERA de un decorador. En pytest 8 una marca aplicada a una FIXTURE no tiene efecto
#: (avisa con `PytestRemovedIn9Warning`), asi que las fixtures que tocan base no se decoran: o
#: bien saltan por la cadena de contenedor del conftest -que ya hace `pytest.skip` cuando no hay
#: engine Docker-, o bien saltan explicitamente con `exigir_oracle_real()`, SIEMPRE con el mismo
#: motivo. Las PRUEBAS si llevan `@SALTAR_SIN_DOCKER`.
SIN_ENGINE_DOCKER: bool = bool(SALTAR_SIN_DOCKER.mark.args[0])


def exigir_oracle_real() -> None:
    """Salta con el motivo unico del proyecto si no hay engine Docker para el Oracle real."""

    if SIN_ENGINE_DOCKER:
        pytest.skip(MOTIVO_SIN_DOCKER)


def sembrar_incidencia(reportante: UsuarioEntity, descripcion: str) -> IncidenciaEntity:
    """
    Siembra UNA incidencia REAL atribuida a `reportante` y la devuelve.

    Los prerrequisitos de catalogo (sala, categoria, estado) se LEEN de las filas que siembra
    Liquibase (ARC-016); no se inventan ni se crean aqui. Si el catalogo viniese vacio la fixture
    falla a proposito: seria un defecto real de las semillas, no algo que esta prueba deba tapar.

    El `reference_code` lleva un sufijo `uuid4` porque la UNIQUE `uk_incidencia_reference_code`
    no admite repeticion entre pruebas.

    EL REPORTANTE NO SE PASA EN EL PAYLOAD: `IncidenciaEntity` redefine `CAMPO_ACTOR_ALTA` como
    `reported_by` y `AtribucionMixin` lo sobrescribe con el actor del contexto publicado
    (REQ-064), asi que el alta se hace DENTRO de un `contexto_de_sesion` del reportante. Fijar
    `reported_by_id` a mano no serviria de nada: el mixin lo pisaria.
    """

    sala = SalaEntity.objects.filter(is_active="Y").select_related("office").first()
    assert sala is not None, "cat_sala no tiene ninguna sala activa sembrada"
    categoria = CategoriaIncidenciaEntity.objects.filter(is_active="Y").first()
    assert categoria is not None, "cat_categoria_incidencia no tiene ninguna categoria activa sembrada"
    estado_abierta = EstadoIncidenciaEntity.objects.get(pk="ABIERTA")

    incidencia = IncidenciaEntity(
        reference_code=f"INC-ALC-{uuid4().hex[:8].upper()}",
        room=sala,
        category=categoria,
        room_name_snapshot=sala.room_name,
        office_name_snapshot=sala.office.office_name,
        category_name_snapshot=categoria.category_name,
        description=descripcion,
        status=estado_abierta,
    )

    contexto = ContextoSesion(
        user_id=reportante.user_id,
        role_code=reportante.role_code_id,
        display_name=reportante.full_name,
    )
    with contexto_de_sesion(contexto):
        incidencia.save()

    assert incidencia.incident_id is not None, "incident_id es IDENTITY: lo genera Oracle, no la prueba"
    assert incidencia.reported_by_id == reportante.user_id, "la incidencia debe quedar atribuida al reportante sembrado"
    return incidencia


def sembrar_adjunto(incidencia: IncidenciaEntity, subido_por: UsuarioEntity) -> IncidenciaAdjuntoEntity:
    """
    Siembra el adjunto REAL de la incidencia indicada (como maximo uno por incidencia, REQ-091).

    Los valores respetan las CHECK del DDL: `mime_type` de la lista permitida, `file_size_bytes`
    dentro del rango, `file_checksum` de 64 digitos hexadecimales, `storage_key` opaca de 32
    caracteres o mas y `file_name` sin separadores de ruta.
    """

    adjunto = IncidenciaAdjuntoEntity(
        incident=incidencia,
        file_name="evidencia-alcance.png",
        mime_type="image/png",
        file_size_bytes=2048,
        file_checksum=f"{uuid4().hex}{uuid4().hex}",
        storage_key=f"adjuntos/alcance/{uuid4().hex}.png",
        uploaded_by=subido_por,
    )
    adjunto.save()
    assert adjunto.attachment_id is not None, "attachment_id es IDENTITY: lo genera Oracle, no la prueba"
    return adjunto


@pytest.fixture
def empleado_solicitante(db) -> UsuarioEntity:
    """Usuario ACTIVO con rol EMPLEADO: es QUIEN PREGUNTA en toda la suite (alcance OWN)."""

    exigir_oracle_real()
    return sembrar_usuario(ROL_EMPLEADO, "alc-solicitante")


@pytest.fixture
def otro_reportante(db) -> UsuarioEntity:
    """
    OTRO usuario ACTIVO con rol EMPLEADO: es el dueno de las incidencias ajenas.

    Tiene el mismo rol que el solicitante a proposito. Si fuese de otro rol, una denegacion
    podria explicarse por la matriz de permisos y no por el alcance, y la prueba acreditaria algo
    distinto de lo que dice acreditar.
    """

    exigir_oracle_real()
    return sembrar_usuario(ROL_EMPLEADO, "alc-ajeno")


@pytest.fixture
def contexto_empleado(empleado_solicitante: UsuarioEntity) -> ContextoSesion:
    """
    Contexto de sesion del solicitante con `data_scope=OWN`, tal y como lo deja la guardia.

    El `data_scope` NO se inventa aqui: `OWN` es el alcance que la matriz `permiso_rol_operacion`
    concede al EMPLEADO sobre la consulta de incidencias (acreditado en las pruebas de TSK-003), y
    es el valor que `PermisoOperacion` publica en el contexto antes de que la vista acote.
    """

    return ContextoSesion(
        user_id=empleado_solicitante.user_id,
        role_code=empleado_solicitante.role_code_id,
        session_id=uuid4().hex,
        data_scope=AlcanceDatos.OWN.value,
        display_name=empleado_solicitante.full_name,
    )


@pytest.fixture
def incidencias_ajenas(db, otro_reportante: UsuarioEntity) -> tuple[int, ...]:
    """
    Tres incidencias REALES reportadas por `otro_reportante`; la PRIMERA lleva adjunto.

    Son el caso "ajeno" de la propiedad: EXISTEN en la base -se puede comprobar con SQL- pero
    estan fuera del alcance del solicitante. Que la primera tenga adjunto permite ejercitar el
    recurso ADJUNTO sobre una incidencia ajena que SI lo tiene: es el caso mas exigente, porque
    el dato existe de verdad y solo el alcance impide servirlo.

    Devuelve los `incident_id` en el orden en que se sembraron.
    """

    exigir_oracle_real()
    incidencias = [
        sembrar_incidencia(otro_reportante, f"Incidencia ajena {indice} sembrada para la propiedad PBT-006 de no revelacion")
        for indice in range(1, 4)
    ]
    sembrar_adjunto(incidencias[0], otro_reportante)
    return tuple(incidencia.incident_id for incidencia in incidencias)


@pytest.fixture
def incidencia_propia(db, empleado_solicitante: UsuarioEntity) -> int:
    """
    UNA incidencia REAL reportada por el solicitante: es el CONTROL POSITIVO de la suite.

    Sin ella, una capa de alcance que denegase absolutamente todo pasaria la propiedad de
    indistinguibilidad con nota: tres denegaciones iguales. El control positivo obliga a que la
    uniformidad se demuestre sobre una capa que SI sirve lo que esta en alcance.
    """

    exigir_oracle_real()
    incidencia = sembrar_incidencia(empleado_solicitante, "Incidencia propia del solicitante: control positivo de PBT-006")
    return incidencia.incident_id


@pytest.fixture
def identificadores_inexistentes(db, incidencias_ajenas: tuple[int, ...]) -> tuple[int, ...]:
    """
    Enteros con FORMATO VALIDO que no corresponden a ninguna fila de `incidencia`.

    Se calculan sobre `MAX(incident_id)` DE LA BASE, ya sembradas las incidencias ajenas, nunca
    con constantes magicas: un `999999` escrito a mano podria coincidir con una fila real y
    convertir este caso en un "ajeno" disfrazado, que pasaria en verde por el motivo equivocado.
    Como `incident_id` es NUMBER IDENTITY `GENERATED ALWAYS` y crece monotonamente, cualquier
    valor por encima del maximo esta garantizado como ausente dentro de la transaccion de prueba.
    """

    exigir_oracle_real()
    maximo = IncidenciaEntity.objects.aggregate(maximo=Max("incident_id"))["maximo"] or 0
    identificadores = tuple(maximo + desplazamiento for desplazamiento in DESPLAZAMIENTOS_SOBRE_EL_MAXIMO)

    assert not IncidenciaEntity.objects.filter(pk__in=identificadores).exists(), (
        "los identificadores 'inexistentes' casan con filas reales: el calculo sobre MAX(incident_id) no se sostiene"
    )
    return identificadores


# ---------------------------------------------------------------------------------------------
# LA PROPIEDAD
# ---------------------------------------------------------------------------------------------

#: Los TRES RECURSOS EQUIVALENTES sobre los que se enuncia la propiedad. El historial y el adjunto
#: no tienen alcance propio: heredan el de su incidencia (REQ-023 regla 3, REQ-027 regla 3), de
#: modo que los tres deben denegar exactamente igual. Se recorren como una sola tabla para que
#: nadie pueda cubrir el detalle y olvidarse de los otros dos.
RECURSOS_EQUIVALENTES: tuple[tuple[str, type[APIView]], ...] = (
    ("detalle", _DetalleDePrueba),
    ("historial", _HistorialDePrueba),
    ("adjunto", _AdjuntoDePrueba),
)


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_PBT_006_toda_incidencia_fuera_de_alcance_responde_igual_que_una_inexistente(
    contexto_empleado: ContextoSesion,
    otro_reportante: UsuarioEntity,
    incidencias_ajenas: tuple[int, ...],
    incidencia_propia: int,
    identificadores_inexistentes: tuple[int, ...],
) -> None:
    """
    [PBT-006] Una incidencia ajena responde siempre igual que una inexistente.

    Given un empleado autenticado cuyo alcance son sus propias incidencias
    And cualquier identificador generado: de incidencia ajena, de incidencia inexistente o con
        formato invalido
    When solicita el detalle, el historial y el adjunto de ese identificador
    Then las tres respuestas tienen el mismo codigo, el mismo cuerpo y las mismas cabeceras que
        para un identificador inexistente
    And ninguna respuesta contiene campos de la incidencia ni datos personales de terceros

    ORDEN DE LAS FIXTURES. `incidencia_propia` se pide ANTES que `identificadores_inexistentes` a
    proposito: los inexistentes se calculan sobre `MAX(incident_id)`, asi que si la incidencia del
    control positivo se sembrara despues ocuparia el primer hueco y convertiria el patron de
    referencia en una incidencia viva. La fixture lo detectaria igualmente -comprueba que ninguno
    casa con una fila real-, pero el orden evita el falso rojo de raiz.
    """

    # --- Arrange: el corpus de identificadores generados -------------------------------------
    # Primer eslabon de la propiedad: ningun identificador mal formado normaliza a una clave, y
    # `identificador_normalizado` no lanza ante ninguno. Gracias a eso la capa de alcance puede
    # consultar con `PK_CENTINELA` y hacer el MISMO viaje a Oracle que ante una incidencia ajena,
    # en vez de atajar en memoria y delatarse por el coste de respuesta.
    for mal_formado in IDENTIFICADORES_MAL_FORMADOS:
        assert identificador_normalizado(mal_formado) is None, (
            "un identificador que no es un entero positivo no puede producir clave de consulta: si normalizara, "
            "designaria una fila y el caso mal formado dejaria de recorrer el mismo camino que el ajeno"
        )
    for valido in (*incidencias_ajenas, *identificadores_inexistentes):
        assert identificador_normalizado(valido) == valido, (
            "un identificador con formato valido debe normalizar a su propia clave; si no, el caso 'existente pero ajeno' "
            "no llegaria siquiera a consultarse y la propiedad se verificaria sobre un camino que no es el real"
        )

    # Las tres clases de equivalencia del escenario en una sola secuencia: ajenos (existen en el
    # Oracle real, el primero CON adjunto), inexistentes (formato valido, ninguna fila) y mal
    # formados (ni siquiera son identificadores).
    corpus: tuple[object, ...] = (*incidencias_ajenas, *identificadores_inexistentes, *IDENTIFICADORES_MAL_FORMADOS)

    # --- Referencia: la respuesta ante un identificador INEXISTENTE --------------------------
    # Es el patron con el que el escenario compara todo lo demas ("...que para un identificador
    # inexistente"). Se calcula una sola vez, recurso a recurso.
    patron_inexistente = identificadores_inexistentes[0]
    referencia: dict[str, dict[str, Any]] = {
        nombre: huella(invocar(vista, patron_inexistente, contexto_empleado)) for nombre, vista in RECURSOS_EQUIVALENTES
    }

    # Los tres recursos son indistinguibles tambien ENTRE SI. La cabecera `Allow` NO se silencia:
    # las tres vistas exponen el mismo juego de metodos (`GET`, `HEAD`, `OPTIONS`), de modo que
    # entra en la comparacion y coincide sin excepciones. Si algun dia dejara de coincidir seria
    # una diferencia real entre recursos y esta prueba debe ponerse en rojo, no taparla.
    huella_patron = referencia["detalle"]
    for nombre, firma in referencia.items():
        assert firma == huella_patron, (
            f"el recurso {nombre} deniega de forma distinta a los demas: el detalle, el historial y el adjunto deben "
            "responder lo mismo, o el cliente sabra por cual de ellos preguntar para distinguir"
        )

    # --- Act + Assert: la propiedad sobre TODO el corpus -------------------------------------
    consultas_por_recurso: dict[str, set[int]] = {nombre: set() for nombre, _ in RECURSOS_EQUIVALENTES}
    correladores: list[str] = []
    cuerpos_denegados: list[str] = []

    for identificador in corpus:
        huellas_del_identificador: list[dict[str, Any]] = []
        for nombre, vista in RECURSOS_EQUIVALENTES:
            # COSTE DE RESPUESTA MEDIDO EN CONSULTAS, NO CON RELOJ DE PARED. Un cronometro en CI
            # es inestable -contencion de la maquina, arranque en frio del contenedor, GC- y
            # produciria rojos que no son fugas. La variable que de verdad hace OBSERVABLE la
            # existencia del recurso es el numero de viajes a Oracle: si el identificador mal
            # formado se atajase en memoria haria cero consultas y el ajeno una, y ESA diferencia
            # si es medible desde fuera. Es justo lo que la capa cierra con `PK_CENTINELA`.
            with CaptureQueriesContext(connections["default"]) as consultas:
                respuesta = invocar(vista, identificador, contexto_empleado)
            consultas_por_recurso[nombre].add(len(consultas.captured_queries))

            assert respuesta.status_code == 404, (
                f"el recurso {nombre} fuera de alcance debe responder el mismo 404 que uno inexistente; cualquier otro "
                "codigo (403, 400) confirmaria que el identificador significa algo para el servidor"
            )
            firma = huella(respuesta)
            assert firma == referencia[nombre], (
                f"el recurso {nombre} responde distinto segun el identificador: codigo, cuerpo o cabeceras difieren "
                "respecto de la denegacion por identificador inexistente, y esa diferencia es un oraculo de enumeracion"
            )
            huellas_del_identificador.append(firma)

            correlador = trace_id_de(respuesta)
            assert correlador, (
                f"toda denegacion del recurso {nombre} debe traer su traceId: ausente en unos casos y presente en otros "
                "volveria a distinguirlos, y ademas dejaria sin correlador una denegacion real en produccion"
            )
            correladores.append(correlador)

            cuerpo_sin_correlador = cuerpo_de(respuesta)
            cuerpo_sin_correlador.pop(CLAVE_TRACE_ID, None)
            cuerpos_denegados.append(json.dumps(cuerpo_sin_correlador, ensure_ascii=False, sort_keys=True))

        assert all(firma == huellas_del_identificador[0] for firma in huellas_del_identificador), (
            "para un mismo identificador, el detalle, el historial y el adjunto deben responder lo mismo: si uno de los "
            "tres se desvia, basta preguntar por ese para saber si el recurso existe"
        )

    assert len(set(correladores)) == len(correladores), (
        "el traceId es un correlador NUEVO por peticion: dos respuestas no pueden compartirlo. Por ser aleatorio y no "
        "depender del caso es lo unico que queda legitimamente fuera de la huella comparada"
    )

    # --- Assert de no filtracion: ni campos de la incidencia ni datos de terceros -------------
    # Los valores prohibidos se toman de las FILAS REALES sembradas en el Oracle, nunca de
    # literales: un literal escrito a mano podria no coincidir con lo que de verdad hay en base y
    # dejar la comprobacion vacia. El `traceId` ya se ha retirado de los cuerpos: es hexadecimal
    # aleatorio y contendria por azar cualquier digito suelto, lo que daria rojos que no son fugas.
    filas_ajenas = list(IncidenciaEntity.objects.filter(pk__in=incidencias_ajenas))
    assert len(filas_ajenas) == len(incidencias_ajenas), (
        "las incidencias ajenas deben seguir existiendo en el Oracle real: si no existieran, el caso 'ajeno' no se "
        "estaria probando y toda la propiedad se reduciria al caso 'inexistente'"
    )

    datos_prohibidos: list[str] = [otro_reportante.full_name, otro_reportante.corporate_email, str(otro_reportante.user_id)]
    for fila in filas_ajenas:
        datos_prohibidos.extend(
            [
                fila.reference_code,
                fila.description,
                fila.room_name_snapshot,
                fila.office_name_snapshot,
                fila.category_name_snapshot,
                str(fila.incident_id),
            ]
        )

    for cuerpo_serializado in cuerpos_denegados:
        for dato in datos_prohibidos:
            assert dato not in cuerpo_serializado, (
                "el cuerpo de una denegacion no puede transportar ningun campo de la incidencia ajena ni ningun dato "
                "personal de su reportante, ni siquiera como eco del identificador solicitado"
            )

    # --- Coste de respuesta: las mismas consultas para los tres casos -------------------------
    for nombre, recuentos in consultas_por_recurso.items():
        assert len(recuentos) == 1, (
            f"el recurso {nombre} ejecuta distinto numero de consultas segun el identificador sea ajeno, inexistente o "
            "mal formado: esa diferencia de coste es medible por tiempo desde fuera y revela si el recurso existe"
        )
        assert next(iter(recuentos)) >= 1, (
            f"la denegacion del recurso {nombre} debe viajar a la base tambien con el identificador mal formado "
            "(consulta con PK_CENTINELA); un atajo en memoria responderia antes y se delataria por el tiempo"
        )

    # --- Control positivo: la capa SI sirve lo que esta en alcance ---------------------------
    # No puede faltar. Sin el, una capa que denegase absolutamente todo pasaria la propiedad de
    # indistinguibilidad con nota -tres denegaciones iguales- y esta prueba no valdria nada.
    respuesta_propia = invocar(_DetalleDePrueba, incidencia_propia, contexto_empleado)
    assert respuesta_propia.status_code == 200, (
        "la incidencia reportada por el propio solicitante SI esta en su alcance: si tambien se denegara, la uniformidad "
        "verificada arriba seria la de una capa que no sirve nada"
    )
    assert cuerpo_de(respuesta_propia) == {"incidentId": incidencia_propia}, (
        "el camino de exito debe devolver la incidencia pedida y no otra: es lo que acredita que el predicado de alcance "
        "recorta sin romper la lectura legitima"
    )
