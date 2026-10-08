"""
Pruebas basadas en propiedades de la resolucion de destinatarios (PBT-011).

Cuantifican sobre un ESPACIO de padrones generado por Hypothesis, no sobre ejemplos escogidos a
mano: lo que se afirma es una invariante de conservacion del directorio, y una invariante solo se
acredita probandola contra entradas que el autor del test no ha elegido.

Estas pruebas NO tocan la base de datos y es deliberado: el servicio admite inyeccion de sus dos
repositorios por constructor, y lo que se ejercita aqui es su POLITICA (quien es notificable, como
se deduplica, que se registra), que vive integra en `servicio.py` y no en el SQL. Los dobles de
este fichero replican el contrato observable de los repositorios reales -el directorio reproduce
literalmente el predicado de `listar_equipo_mantenimiento` (rol + estado ACTIVO, ordenado por
`user_id`) y el registro calcula `recipient_count` igual que `registrar`-, de modo que la
propiedad se mide contra la misma semantica que impone Oracle.

El reparto entre pruebas queda asi: la evidencia de PERSISTENCIA sobre `resolucion_destinatario_log`
la da `test_resolucion_destinatarios_oracle.py` contra el motor real; aqui se cubre la invariante
algebraica, que exige cientos de ejecuciones y seria inviable contra un contenedor.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from hypothesis import given, settings
from hypothesis import strategies as st

from apps.avisos.resolucion.repositorio import INDICADOR_NO, INDICADOR_SI, RepositorioResolucionDestinatarioLog
from apps.avisos.resolucion.resultados import (
    ESTADO_USUARIO_ACTIVO,
    LONGITUD_MAXIMA_CORREO,
    ROL_EQUIPO_MANTENIMIENTO,
    ResultadoResolucion,
    TipoResolucion,
)
from apps.avisos.resolucion.servicio import ServicioResolucionDestinatarios


# --- Catalogos cerrados del padron generado ------------------------------
# Son los mismos valores que acotan los CHECK del DDL: generar fuera de catalogo probaria un
# sistema que no existe, porque Oracle rechazaria esas filas antes de llegar al servicio.
ROLES: tuple[str, ...] = ("EMPLEADO", ROL_EQUIPO_MANTENIMIENTO, "ADMINISTRADOR")
ESTADOS: tuple[str, ...] = (ESTADO_USUARIO_ACTIVO, "INACTIVO")

# Conjunto PEQUENO de direcciones validas y ya normalizadas (minusculas, sin espacios al borde),
# como exige la precondicion del escenario. Que sea pequeno es parte del diseno: con un pool corto
# Hypothesis genera con frecuencia pares de fichas que resuelven a la misma direccion normalizada,
# que es justo lo que ejercita la rama de deduplicacion y la invariante de "sin repetidos".
CORREOS_VALIDOS: tuple[str, ...] = (
    "ana.perez@mind.local",
    "luis.gomez@mind.local",
    "eva.ruiz@mind.local",
)

# Correos que el servicio debe descartar por formato. El ultimo mide 261 caracteres y cubre el
# descarte por longitud (> 254, el tope de `corporate_email` en el DDL).
CORREOS_INVALIDOS: tuple[str, ...] = (
    "sin-arroba",
    "@mind.local",
    "dos@@mind.local",
    "espacio interior@mind.local",
    "{}@mind.local".format("a" * 250),
)

# Ausencia de correo: columna nula o cadena que al normalizar no deja nada.
CORREOS_AUSENTES: tuple[str | None, ...] = (None, "", "   ")


@dataclass(frozen=True, slots=True)
class FichaUsuario:
    """Doble de `UsuarioEntity` con los unicos campos que lee el servicio (los de `CAMPOS_DESTINATARIO`)."""

    user_id: int
    full_name: str
    corporate_email: str | None
    role_code_id: str
    status: str


class DirectorioFalso:
    """
    Doble del `RepositorioDirectorioDestinatarios` que sirve el padron desde memoria.

    Replica EXACTAMENTE el predicado de `listar_equipo_mantenimiento`: filtra por rol de
    mantenimiento Y estado ACTIVO, y devuelve el resultado ordenado por `user_id` ascendente. Si el
    doble filtrase distinto que el SQL real, la propiedad no estaria midiendo el sistema que se
    despliega.
    """

    def __init__(self, padron: list[FichaUsuario]) -> None:
        self.padron = padron

    def listar_equipo_mantenimiento(self) -> list[FichaUsuario]:
        """Tecnicos de mantenimiento ACTIVOS del padron, ordenados por `user_id`."""

        seleccion = [
            ficha for ficha in self.padron if ficha.role_code_id == ROL_EQUIPO_MANTENIMIENTO and ficha.status == ESTADO_USUARIO_ACTIVO
        ]
        return sorted(seleccion, key=lambda ficha: ficha.user_id)


@dataclass(frozen=True, slots=True)
class AsientoFalso:
    """Lo que devuelve el registro falso: solo necesita exponer el `resolution_id` que lee el servicio."""

    resolution_id: int


class RegistroFalso:
    """
    Doble del `RepositorioResolucionDestinatarioLog` que captura los asientos en vez de insertarlos.

    `recipient_count` NO lo pasa el servicio: lo deriva el repositorio real de `resolved_user_ids`,
    y aqui se deriva igual para poder comprobar que el recuento declarado en la traza coincide con
    los destinatarios devueltos.
    """

    def __init__(self) -> None:
        self.asientos: list[dict[str, Any]] = []

    def registrar(self, **kwargs: Any) -> AsientoFalso:
        """Captura la llamada y devuelve un asiento con identificador correlativo."""

        identificadores = [int(user_id) for user_id in kwargs["resolved_user_ids"]]
        asiento = dict(kwargs)
        asiento["resolved_user_ids"] = identificadores
        asiento["recipient_count"] = len(identificadores)
        self.asientos.append(asiento)
        return AsientoFalso(resolution_id=len(self.asientos))


def _es_correo_ausente(valor: str | None) -> bool:
    """Indica si la ficha no aporta direccion alguna: columna nula o en blanco."""

    return valor is None or not valor.strip()


def _es_correo_invalido(valor: str | None) -> bool:
    """
    Indica si la direccion existe pero no sirve para enviar.

    Usa `validate_email` y el tope de longitud, que es el MISMO criterio de
    `ServicioResolucionDestinatarios._es_correo_notificable`: si la clasificacion usara otra regla,
    el test discreparia del sistema bajo prueba en vez de verificarlo.
    """

    if _es_correo_ausente(valor):
        return False
    direccion = str(valor).strip()
    if len(direccion) > LONGITUD_MAXIMA_CORREO:
        return True
    try:
        validate_email(direccion)
    except ValidationError:
        return True
    return False


_correos = st.one_of(
    st.sampled_from(CORREOS_VALIDOS),
    st.sampled_from(CORREOS_INVALIDOS),
    st.sampled_from(CORREOS_AUSENTES),
)

_padrones = st.lists(
    st.tuples(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=1, max_size=20),
        _correos,
        st.sampled_from(ROLES),
        st.sampled_from(ESTADOS),
    ),
    min_size=0,
    max_size=50,
).map(
    lambda filas: [
        FichaUsuario(
            user_id=posicion + 1,
            full_name=nombre,
            corporate_email=correo,
            role_code_id=rol,
            status=estado,
        )
        for posicion, (nombre, correo, rol, estado) in enumerate(filas)
    ]
)


@settings(max_examples=100, deadline=None)
@given(padron=_padrones, respaldo_configurado=st.booleans())
def test_PBT_011_el_padron_de_tecnicos_se_reparte_sin_perdida_entre_destinatarios_y_excluidos(
    padron: list[FichaUsuario],
    respaldo_configurado: bool,
) -> None:
    """
    [PBT-011] Para todo padron, los tecnicos se reparten sin perdida entre destinatarios y excluidos con motivo.

    Se comprueban las cuatro afirmaciones del resultado esperado: (1) destinatarios + excluidos = tecnicos,
    con conjuntos disjuntos y union exacta; (2) todo excluido cae en exactamente un motivo del catalogo
    cerrado; (3) el recuento declarado en la traza coincide con los destinatarios devueltos; (4) ninguna
    direccion se repite; (5) el buzon de respaldo solo interviene si no queda ningun destinatario.

    Sobre `respaldo_configurado`: los datos de prueba del catalogo piden generar la configuracion del buzon
    de respaldo presente o ausente, pero el servicio NO admite hoy ningun parametro de configuracion de
    respaldo -`is_fallback_used` se fija a False por decision documentada en `servicio.py` (REQ-140, RN-02)-.
    No se inventa aqui un parametro que el producto no expone: el booleano se genera y alimenta la
    asercion de implicacion, que debe sostenerse este configurado el respaldo o no.
    """

    directorio = DirectorioFalso(padron)
    registro = RegistroFalso()
    servicio = ServicioResolucionDestinatarios(directorio=directorio, registro=registro)

    resolucion = servicio.resolver_equipo_mantenimiento(modulo="PBT-011")

    # El universo de la propiedad son TODOS los tecnicos del padron, incluidos los INACTIVOS: el
    # enunciado reparte "los usuarios con rol de tecnico de mantenimiento", sin filtrar por estado.
    tecnicos = [ficha for ficha in padron if ficha.role_code_id == ROL_EQUIPO_MANTENIMIENTO]
    ids_tecnicos = {ficha.user_id for ficha in tecnicos}
    ids_destinatarios = set(resolucion.user_ids)
    excluidos = [ficha for ficha in tecnicos if ficha.user_id not in ids_destinatarios]
    ids_excluidos = {ficha.user_id for ficha in excluidos}

    # (1) Reparto sin perdida ni solape.
    assert ids_destinatarios <= ids_tecnicos, "se ha resuelto como destinatario alguien que no es tecnico"
    assert ids_destinatarios.isdisjoint(ids_excluidos), "un mismo usuario figura como destinatario y como excluido"
    assert ids_destinatarios | ids_excluidos == ids_tecnicos, "la union de destinatarios y excluidos no cubre el padron de tecnicos"
    assert len(resolucion.destinatarios) + len(excluidos) == len(tecnicos)

    # (2) Todo excluido tiene exactamente un motivo del catalogo cerrado.
    direcciones_ya_aportadas: set[str] = set()
    for ficha in sorted(tecnicos, key=lambda tecnico: tecnico.user_id):
        motivos = []
        if ficha.status != ESTADO_USUARIO_ACTIVO:
            motivos.append("CUENTA_INACTIVA")
        elif _es_correo_ausente(ficha.corporate_email):
            motivos.append("CORREO_AUSENTE")
        elif _es_correo_invalido(ficha.corporate_email):
            motivos.append("CORREO_INVALIDO")
        else:
            direccion = str(ficha.corporate_email).strip().lower()
            if direccion in direcciones_ya_aportadas:
                motivos.append("DIRECCION_DUPLICADA")
            else:
                direcciones_ya_aportadas.add(direccion)
        if ficha.user_id in ids_excluidos:
            assert len(motivos) == 1, f"el excluido {ficha.user_id} no encaja en exactamente un motivo del catalogo: {motivos}"
        else:
            assert motivos == [], f"el destinatario {ficha.user_id} tiene motivo de exclusion {motivos} y aun asi se ha resuelto"

    # (3) El recuento declarado en la traza coincide con los destinatarios devueltos.
    assert len(registro.asientos) == 1, "la resolucion de un colectivo debe dejar un unico asiento en la traza"
    asiento = registro.asientos[0]
    assert asiento["recipient_count"] == len(resolucion.destinatarios)
    assert asiento["resolved_user_ids"] == list(resolucion.user_ids)

    # (4) Ninguna direccion aparece dos veces.
    assert len(set(resolucion.direcciones)) == len(resolucion.direcciones), f"direcciones repetidas en {resolucion.direcciones}"

    # (5) El buzon de respaldo solo interviene cuando no queda ningun destinatario; la implicacion
    # debe sostenerse con la configuracion de respaldo presente o ausente.
    assert not asiento["is_fallback_used"] or not resolucion.hay_destinatarios, (
        f"se ha usado el buzon de respaldo habiendo destinatarios (respaldo_configurado={respaldo_configurado})"
    )


# =========================================================================
# PBT-012 - Invariante de trazabilidad del registro de resoluciones
# =========================================================================
# Lo que se ejercita aqui NO es el servicio, sino el REGISTRO: la logica real de
# `RepositorioResolucionDestinatarioLog.registrar` (serializacion del JSON de identificadores,
# derivacion de `recipient_count` sobre esa misma lista ya normalizada y traduccion del indicador
# a 'Y'/'N'). Se instancia el repositorio REAL con un modelo doble, que es para lo que su
# constructor acepta `modelo`: asi no se reimplementa nada del sistema bajo prueba y aun asi no
# hace falta Oracle.

# Padron SEMBRADO CON CORREOS REALES. Es la pieza que impide que la asercion "ningun campo
# contiene un correo legible" sea vacua: los identificadores que se registran salen de fichas que
# SI tienen direccion conocida, de modo que si `registrar` filtrase la direccion en lugar del
# identificador, el test lo veria. Con un padron sin correos, esa asercion pasaria siempre y no
# estaria probando nada.
PADRON_CON_CORREO: tuple[FichaUsuario, ...] = tuple(
    FichaUsuario(
        user_id=posicion + 1,
        full_name=f"Tecnico {posicion + 1:02d}",
        corporate_email=(CORREOS_VALIDOS[posicion] if posicion < len(CORREOS_VALIDOS) else f"tecnico.{posicion + 1:02d}@mind.local"),
        role_code_id=ROL_EQUIPO_MANTENIMIENTO,
        status=ESTADO_USUARIO_ACTIVO,
    )
    for posicion in range(50)
)

CORREOS_SEMBRADOS: frozenset[str] = frozenset(str(ficha.corporate_email) for ficha in PADRON_CON_CORREO)

# Catalogo de modulos solicitantes. Todos caben en el VARCHAR2(50) de `requested_by_module` y
# ninguno tiene forma de direccion, para que un fallo de la asercion 5 solo pueda venir de lo que
# escribe el repositorio y nunca del propio dato de entrada.
MODULOS_SOLICITANTES: tuple[str, ...] = (
    "AVISOS",
    "INCIDENCIAS",
    "PLANIFICADOR_AVISOS",
    "API_NOTIFICATION_GROUPS",
    "ADMINISTRACION_USUARIOS",
)

# Deteccion generica de "algo@algo.dominio". Complementa -no sustituye- la busqueda de las
# direcciones concretas sembradas en el padron.
PATRON_CORREO = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass(frozen=True, slots=True)
class PeticionRegistro:
    """Una resolucion ya concluida tal y como llega al registro: tipo, resultado y usuarios resueltos."""

    request_type: TipoResolucion
    outcome: ResultadoResolucion
    usuarios: tuple[FichaUsuario, ...]
    sujeto: FichaUsuario | None
    modulo: str
    respaldo: bool
    resolved_by_user_id: int | None


class FilaLogFalsa:
    """
    Instancia doble de `ResolucionDestinatarioLogEntity`: guarda los kwargs recibidos y los expone como atributos.

    `save()` la anota en la lista de filas escritas del modelo que la fabrico y le asigna un
    `resolution_id` correlativo, igual que haria la IDENTITY de Oracle.
    """

    def __init__(self, modelo: ModeloLogFalso, **campos: Any) -> None:
        self.campos: dict[str, Any] = dict(campos)
        for nombre, valor in campos.items():
            setattr(self, nombre, valor)
        self.resolution_id: int | None = None
        self._modelo = modelo

    def save(self) -> None:
        """Escribe la fila en la lista compartida del modelo doble."""

        self._modelo.filas_escritas.append(self)
        self.resolution_id = len(self._modelo.filas_escritas)


class ModeloLogFalso:
    """
    Doble de la CLASE de modelo que `RepositorioBase.crear` invoca con `self.modelo(**datos)`.

    Es invocable para encajar en ese contrato sin tocar el repositorio: al llamarlo devuelve una
    `FilaLogFalsa` todavia no escrita, y solo el `.save()` posterior la registra. Asi la cuenta de
    filas distingue de verdad entre "construida" y "persistida".
    """

    def __init__(self) -> None:
        self.filas_escritas: list[FilaLogFalsa] = []

    def __call__(self, **campos: Any) -> FilaLogFalsa:
        """Construye la fila sin persistirla, como el constructor de un modelo del ORM."""

        return FilaLogFalsa(self, **campos)


@st.composite
def _peticiones_de_registro(draw: Any) -> PeticionRegistro:
    """
    Genera resoluciones concluidas coherentes con el catalogo cerrado y con los CHECK del DDL.

    Dos coherencias se respetan AL GENERAR y no al asertar, porque las impone el esquema y un caso
    que las incumpliera probaria un sistema que no existe: `SIN_DESTINATARIOS` exige recuento cero
    (lista vacia) y `ck_res_dest_subject_iff` exige sujeto si y solo si la resolucion es individual.
    """

    request_type: TipoResolucion = draw(st.sampled_from(TipoResolucion))
    outcome: ResultadoResolucion = draw(st.sampled_from(ResultadoResolucion))
    if outcome is ResultadoResolucion.SIN_DESTINATARIOS:
        usuarios: list[FichaUsuario] = []
    else:
        usuarios = draw(st.lists(st.sampled_from(PADRON_CON_CORREO), min_size=0, max_size=50, unique_by=lambda ficha: ficha.user_id))
    sujeto = draw(st.sampled_from(PADRON_CON_CORREO)) if request_type is TipoResolucion.INDIVIDUAL else None
    return PeticionRegistro(
        request_type=request_type,
        outcome=outcome,
        usuarios=tuple(usuarios),
        sujeto=sujeto,
        modulo=draw(st.sampled_from(MODULOS_SOLICITANTES)),
        respaldo=draw(st.booleans()),
        resolved_by_user_id=draw(st.one_of(st.none(), st.integers(min_value=1, max_value=9999))),
    )


@settings(max_examples=100, deadline=None)
@given(peticion=_peticiones_de_registro())
def test_PBT_012_toda_resolucion_concluida_deja_exactamente_una_fila_de_registro_coherente_con_su_resultado(
    peticion: PeticionRegistro,
) -> None:
    """
    [PBT-012] Para toda resolucion que concluye existe exactamente una fila de registro coherente con su resultado.

    Se comprueban las afirmaciones del resultado esperado: (1) existe exactamente una fila por
    resolucion; (2) su recuento es igual a la cardinalidad de la lista de usuarios resueltos, y el
    JSON persistido declara esa misma cardinalidad; (3) el resultado sin destinatarios implica
    recuento cero; (4) el usuario sujeto esta informado si y solo si la resolucion es individual,
    en las dos direcciones; (5) ningun campo de la fila contiene un correo legible.

    La asercion 5 NO es vacua: los identificadores registrados salen de `PADRON_CON_CORREO`, cuyas
    fichas tienen direcciones reales y conocidas, y se contrasta tanto que ninguna de esas
    direcciones aparece en la fila como que ningun campo encaja en el patron generico de correo.
    """

    modelo = ModeloLogFalso()
    repositorio = RepositorioResolucionDestinatarioLog(modelo=modelo)
    resueltos = [ficha.user_id for ficha in peticion.usuarios]

    entidad = repositorio.registrar(
        request_type=peticion.request_type,
        requested_by_module=peticion.modulo,
        resolved_user_ids=resueltos,
        outcome=peticion.outcome,
        subject_user_id=peticion.sujeto.user_id if peticion.sujeto is not None else None,
        is_fallback_used=peticion.respaldo,
        resolved_by_user_id=peticion.resolved_by_user_id,
    )

    # (1) Exactamente una fila de registro: ni cero (traza perdida) ni dos (traza duplicada).
    assert len(modelo.filas_escritas) == 1, f"una resolucion debe dejar una unica fila, se han escrito {len(modelo.filas_escritas)}"
    fila = modelo.filas_escritas[0]
    assert fila is entidad, "el repositorio ha devuelto una entidad distinta de la que ha escrito"
    assert fila.resolution_id is not None, "la fila registrada no ha recibido identificador"

    # (2) El recuento coincide con la cardinalidad de la lista de usuarios resueltos, y el JSON
    # persistido deserializa a una lista de esa misma cardinalidad y de numeros.
    assert fila.recipient_count == len(resueltos), f"recuento {fila.recipient_count} frente a {len(resueltos)} usuarios resueltos"
    identificadores = json.loads(fila.resolved_user_ids)
    assert isinstance(identificadores, list), f"el documento persistido no es una lista JSON: {fila.resolved_user_ids!r}"
    assert len(identificadores) == len(resueltos), f"el JSON persistido declara {len(identificadores)} identificadores"
    assert all(isinstance(elemento, int) for elemento in identificadores), f"el JSON contiene elementos no numericos: {identificadores}"

    # (3) El resultado sin destinatarios implica recuento cero.
    assert fila.outcome != ResultadoResolucion.SIN_DESTINATARIOS.value or fila.recipient_count == 0, (
        f"resultado SIN_DESTINATARIOS con recuento {fila.recipient_count}"
    )

    # (4) El usuario sujeto consta si y solo si la resolucion es individual (doble implicacion).
    es_individual = peticion.request_type is TipoResolucion.INDIVIDUAL
    assert fila.request_type == peticion.request_type.value
    assert (fila.subject_user_id is not None) == es_individual, f"sujeto={fila.subject_user_id!r} en una resolucion {fila.request_type}"

    # El indicador de respaldo viaja como CHAR(1) del catalogo, nunca como booleano de Python.
    assert fila.is_fallback_used == (INDICADOR_SI if peticion.respaldo else INDICADOR_NO)

    # (5) Ningun campo de la fila contiene un correo legible: se recorren TODOS los valores
    # escritos, pasados a texto, y se exige que no aparezca ninguna de las direcciones sembradas
    # ni nada con forma de direccion (ARC-120, REQ-081: jamas correos en claro en la traza).
    for nombre, valor in fila.campos.items():
        texto = str(valor)
        for direccion in CORREOS_SEMBRADOS:
            assert direccion not in texto, f"el campo {nombre} filtra la direccion sembrada {direccion}: {texto!r}"
        assert PATRON_CORREO.search(texto) is None, f"el campo {nombre} contiene algo con forma de correo: {texto!r}"

    # Control de NO VACUIDAD de la asercion anterior: el padron aporta direcciones de verdad y el
    # patron las reconoce, asi que el bucle no esta pasando por falta de material que encontrar.
    assert CORREOS_SEMBRADOS, "el padron sembrado no aporta ninguna direccion contra la que contrastar"
    assert all(PATRON_CORREO.search(direccion) for direccion in CORREOS_SEMBRADOS), "el patron no reconoce las direcciones sembradas"
