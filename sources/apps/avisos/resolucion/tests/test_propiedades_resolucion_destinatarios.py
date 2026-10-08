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

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from hypothesis import given, settings
from hypothesis import strategies as st

from apps.avisos.resolucion.resultados import ESTADO_USUARIO_ACTIVO, LONGITUD_MAXIMA_CORREO, ROL_EQUIPO_MANTENIMIENTO
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
