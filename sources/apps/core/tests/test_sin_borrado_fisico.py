"""
Pruebas del invariante SIN BORRADO FISICO del nucleo (REQ-047).

REQ-047: «Ningun registro de usuario o de incidencia se elimina fisicamente: las bajas son
logicas y los datos se conservan 2 anios». Escenario de error asociado: «La eliminacion
definitiva de un registro no esta permitida; solo procede la baja logica».

Las tres pruebas verifican que NO existe ruta de `DELETE` sobre `usuario` ni `incidencia`:
por contrato de los repositorios, por comportamiento del ORM y por inspeccion del fuente.
Ninguna necesita base de datos: no hay `django_db`, de modo que la suite corre en un clon
limpio sin Oracle. Si alguna de ellas intentara abrir una conexion, seria un defecto.
"""

import re
from pathlib import Path

import pytest

import apps.core
from apps.core.errores import BorradoFisicoNoPermitidoError
from apps.core.models import IncidenciaEntity, UsuarioEntity
from apps.core.repositorios import (
    OPERACIONES_PROHIBIDAS,
    RepositorioBase,
    RepositorioIncidencia,
    RepositorioUsuario,
    expone_borrado_fisico,
)


#: Directorio raiz del paquete `apps.core`, resuelto desde el propio modulo importado
#: (nunca una ruta absoluta del entorno de desarrollo).
RAIZ_NUCLEO = Path(apps.core.__file__).resolve().parent

#: Invocaciones de borrado real que no pueden aparecer en el codigo del nucleo.
PATRONES_BORRADO_REAL: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("invocacion .delete(", re.compile(r"\.delete\s*\(")),
    ("sql crudo con DELETE", re.compile(r"\.raw\s*\([^)]*\bDELETE\b", re.IGNORECASE)),
    ("TRUNCATE", re.compile(r"\bTRUNCATE\b", re.IGNORECASE)),
    ("DROP", re.compile(r"\bDROP\s", re.IGNORECASE)),
)

#: Lineas que FORMAN PARTE de la prohibicion y, por tanto, estan permitidas: la definicion
#: o anulacion de `delete` que rechaza el borrado y el `raise` de la excepcion de dominio.
PATRONES_PERMITIDOS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(async\s+)?def\s+delete\s*\("),
    re.compile(r"^\s*raise\s+BorradoFisicoNoPermitidoError"),
)


def _ficheros_del_nucleo() -> list[Path]:
    """Ficheros `.py` de `apps/core/`, excluido el propio paquete de pruebas."""

    return sorted(
        ruta
        for ruta in RAIZ_NUCLEO.rglob("*.py")
        if "tests" not in ruta.relative_to(RAIZ_NUCLEO).parts
    )


def _es_linea_exenta(linea: str) -> bool:
    """Indica si la linea es un comentario o una de las anulaciones que prohiben el borrado."""

    desnuda = linea.strip()
    if not desnuda or desnuda.startswith("#"):
        return True
    return any(patron.search(linea) for patron in PATRONES_PERMITIDOS)


@pytest.mark.parametrize(
    "clase",
    [RepositorioBase, RepositorioUsuario, RepositorioIncidencia],
    ids=["RepositorioBase", "RepositorioUsuario", "RepositorioIncidencia"],
)
def test_REQ_047_los_repositorios_de_usuario_e_incidencia_no_exponen_borrado_fisico(
    clase: type,
) -> None:
    """
    REQ-047: ningun repositorio publica una operacion de borrado fisico.

    Se comprueba por el oraculo `expone_borrado_fisico` y, de forma INDEPENDIENTE de ese
    helper, inspeccionando `dir(clase)`: asi la prueba no se apoya solo en el mismo codigo
    que pretende verificar.
    """

    assert expone_borrado_fisico(clase) is False, (
        f"{clase.__name__} expone una operacion de borrado fisico (REQ-047)."
    )

    publicados = set(dir(clase))
    encontrados = sorted(publicados.intersection(OPERACIONES_PROHIBIDAS))
    assert encontrados == [], (
        f"{clase.__name__} publica operaciones de borrado fisico {encontrados}; "
        "la baja debe ser LOGICA (REQ-047)."
    )


@pytest.mark.parametrize(
    "modelo",
    [UsuarioEntity, IncidenciaEntity],
    ids=["usuario", "incidencia"],
)
def test_REQ_047_la_entidad_y_su_queryset_rechazan_el_delete(modelo: type) -> None:
    """
    REQ-047: el ORM rechaza el `DELETE` por instancia y por queryset, sin tocar la base.

    La excepcion se lanza ANTES de generar SQL, por eso no hace falta `django_db`: si la
    llamada llegara a conectar, la prueba fallaria por error de conexion y seria un defecto.
    """

    instancia = modelo()
    with pytest.raises(BorradoFisicoNoPermitidoError) as borrado_instancia:
        instancia.delete()

    with pytest.raises(BorradoFisicoNoPermitidoError) as borrado_masivo:
        modelo.objects.all().delete()

    for fallo in (borrado_instancia, borrado_masivo):
        mensaje = str(fallo.value).lower()
        assert mensaje == fallo.value.mensaje.lower(), (
            "La excepcion de dominio debe exponer su texto en `mensaje`."
        )
        assert "borrado fisico" in mensaje or "bajas logicas" in mensaje, (
            f"El mensaje de error debe estar en espanol y explicar la baja logica: {mensaje!r}"
        )


def test_REQ_047_el_codigo_del_nucleo_no_invoca_delete_sobre_usuario_ni_incidencia() -> None:
    """
    REQ-047: ninguna linea del nucleo invoca un borrado real (inspeccion estatica del fuente).

    Se recorren los `.py` de `apps/core/` salvo `tests/`, ignorando comentarios y admitiendo
    unicamente las lineas que PROHIBEN el borrado (`def delete(` y el `raise` asociado).
    """

    ficheros = _ficheros_del_nucleo()
    assert ficheros, f"No se ha encontrado codigo fuente bajo {RAIZ_NUCLEO}."

    hallazgos: list[str] = []
    for ruta in ficheros:
        for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), start=1):
            if _es_linea_exenta(linea):
                continue
            for descripcion, patron in PATRONES_BORRADO_REAL:
                if patron.search(linea):
                    relativa = ruta.relative_to(RAIZ_NUCLEO)
                    hallazgos.append(f"{relativa}:{numero}: {descripcion} -> {linea.strip()}")

    assert hallazgos == [], (
        "El nucleo no puede contener rutas de borrado fisico (REQ-047); encontradas:\n"
        + "\n".join(hallazgos)
    )
