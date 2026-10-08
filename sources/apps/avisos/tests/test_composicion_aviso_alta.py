"""
Composicion del contenido del aviso de alta de incidencia (REQ-131, AC-AVI-03 y AC-AVI-04).

Son pruebas UNITARIAS de la capa de render de `apps.avisos.alta.composicion`: se construye a mano un
`DatosAvisoAlta` y se invoca `componer_contenido`. NO hay base de datos, ni `django_db`, ni dobles
del ORM, porque esa capa es pura por diseño: todo lo que necesita viaja en el argumento.

Las etiquetas en espanol se IMPORTAN del modulo en vez de copiarse como literales: asi la prueba
acredita que el correo lleva el texto que el modulo publica, y no que dos copias del mismo literal
coinciden entre si. Lo mismo con los limites (`LONGITUD_MAXIMA_ASUNTO`, `LONGITUD_MAXIMA_DESCRIPCION`)
y con `ELIPSIS`: se verifica contra la constante, no contra el numero escrito en el enunciado.
"""

from dataclasses import fields, replace
from datetime import datetime

import pytest

from apps.avisos.alta.composicion import (
    AVISO_FOTO_NO_ADJUNTA,
    ELIPSIS,
    ENCABEZADO_CUERPO,
    ETIQUETA_CATEGORIA,
    ETIQUETA_CORREO_REPORTANTE,
    ETIQUETA_DESCRIPCION,
    ETIQUETA_DETALLE,
    ETIQUETA_OFICINA,
    ETIQUETA_REPORTANTE,
    ETIQUETA_SALA,
    LONGITUD_MAXIMA_ASUNTO,
    LONGITUD_MAXIMA_DESCRIPCION,
    ContenidoAviso,
    DatosAvisoAlta,
    componer_contenido,
)

#: Marca que cierra la descripcion larga. Es el FINAL del texto original, de modo que su ausencia en
#: el correo demuestra que el recorte se ha aplicado de verdad y no que el cuerpo lo ha reproducido.
MARCA_FINAL = "AQUI-TERMINA-LA-DESCRIPCION-ORIGINAL"

#: Frases con las que se compone una descripcion realista. Se usa texto VARIADO en espanol y no un
#: caracter repetido 900 veces: con un relleno uniforme la prueba pasaria aunque el recorte cortase
#: por una posicion equivocada, porque cualquier trozo seria indistinguible de cualquier otro.
FRASES = (
    "El proyector de la sala no enciende y el mando no responde pese a tener pilas nuevas. ",
    "El aire acondicionado emite un zumbido constante desde primera hora de la manana. ",
    "Faltan dos sillas y la mesa de reuniones tiene una pata floja que la desequilibra. ",
    "La toma de red de la pared derecha no da enlace y el cable de repuesto tampoco funciona. ",
    "La persiana se ha quedado atascada a media altura y deslumbra a quien ocupa la cabecera. ",
)


def descripcion_de_900_caracteres() -> str:
    """Compone una descripcion de EXACTAMENTE 900 caracteres, variada y terminada en `MARCA_FINAL`."""

    hueco = 900 - len(MARCA_FINAL)
    texto = ""
    indice = 0
    while len(texto) < hueco:
        texto += f"{indice + 1}) {FRASES[indice % len(FRASES)]}"
        indice += 1
    return texto[:hueco] + MARCA_FINAL


@pytest.fixture
def datos_base() -> DatosAvisoAlta:
    """Incidencia tipica ya resuelta: descripcion corta, sin fotografia y con todos los campos informados."""

    return DatosAvisoAlta(
        incident_id=4821,
        reference_code="INC-2026-004821",
        room_name="Sala Guadalquivir",
        office_name="Oficina Central de Sevilla",
        category_name="Climatización",
        description="El aire acondicionado de la sala no enfría y gotea sobre la mesa de reuniones.",
        reporter_name="Lucía Fernández Prieto",
        reporter_email="lucia.fernandez@empresa.es",
        created_at=datetime(2026, 3, 11, 9, 45, 0),  # noqa: DTZ001 - naive en UTC, como el resto del motor
        has_photo=False,
        incident_detail_url="http://localhost:4200/incidencias/4821",
    )


def test_AC_AVI_03_el_correo_lleva_identificador_y_sala_en_el_asunto_y_los_datos_de_la_incidencia_en_el_cuerpo(  # noqa: N802
    datos_base: DatosAvisoAlta,
) -> None:
    """
    AC-AVI-03: dada una incidencia con sala, oficina, categoria, descripcion y reportante, al componer
    el correo de aviso el asunto contiene el identificador de la incidencia y el nombre de la sala
    (<= 200 caracteres) y el cuerpo contiene sala, oficina, categoria, descripcion y nombre y correo
    del reportante, integramente en espanol.
    """

    contenido = componer_contenido(datos_base)

    assert len(contenido.asunto) <= LONGITUD_MAXIMA_ASUNTO, (
        f"el asunto ocupa {len(contenido.asunto)} caracteres y excede el maximo de {LONGITUD_MAXIMA_ASUNTO}: «{contenido.asunto}»"
    )
    assert datos_base.reference_code in contenido.asunto, (
        f"el asunto no lleva el identificador '{datos_base.reference_code}' de la incidencia: «{contenido.asunto}»"
    )
    assert datos_base.room_name in contenido.asunto, (
        f"el asunto no lleva el nombre de la sala '{datos_base.room_name}': «{contenido.asunto}»"
    )

    cuerpo = contenido.cuerpo_texto
    datos_obligatorios = (
        ("sala", datos_base.room_name),
        ("oficina", datos_base.office_name),
        ("categoria", datos_base.category_name),
        ("descripcion", datos_base.description),
        ("nombre del reportante", datos_base.reporter_name),
        ("correo del reportante", datos_base.reporter_email),
    )
    for concepto, valor in datos_obligatorios:
        assert valor in cuerpo, f"el cuerpo de texto no publica la {concepto} ('{valor}'), que AC-AVI-03 exige en el aviso"

    etiquetas_en_espanol = (
        ENCABEZADO_CUERPO,
        ETIQUETA_SALA,
        ETIQUETA_OFICINA,
        ETIQUETA_CATEGORIA,
        ETIQUETA_DESCRIPCION,
        ETIQUETA_REPORTANTE,
        ETIQUETA_CORREO_REPORTANTE,
        ETIQUETA_DETALLE,
    )
    for etiqueta in etiquetas_en_espanol:
        assert etiqueta in cuerpo, f"el cuerpo no esta en espanol: falta la etiqueta '{etiqueta}' que publica el modulo"


def test_AC_AVI_04_con_foto_y_900_caracteres_la_descripcion_se_recorta_a_500_con_elipsis_y_la_imagen_no_viaja(  # noqa: N802
    datos_base: DatosAvisoAlta,
) -> None:
    """
    AC-AVI-04: dada una incidencia con foto adjunta y descripcion de 900 caracteres, al componer el
    correo el mensaje NO incluye la imagen como adjunto pero si el enlace al detalle en la SPA, y la
    descripcion se muestra truncada a 500 caracteres con elipsis.
    """

    descripcion = descripcion_de_900_caracteres()
    assert len(descripcion) == 900, f"la descripcion de la prueba mide {len(descripcion)} caracteres y debe medir 900"

    datos = replace(datos_base, description=descripcion, has_photo=True)
    contenido = componer_contenido(datos)
    extracto = datos.description_excerpt

    assert len(extracto) == LONGITUD_MAXIMA_DESCRIPCION, (
        f"el extracto mide {len(extracto)} caracteres y debe medir exactamente {LONGITUD_MAXIMA_DESCRIPCION} "
        f"(la elipsis cuenta dentro del limite)"
    )
    assert extracto.endswith(ELIPSIS), f"el extracto recortado no termina en la elipsis '{ELIPSIS}': «...{extracto[-20:]}»"

    cuerpo = contenido.cuerpo_texto
    assert extracto in cuerpo, "el cuerpo de texto no publica la descripcion recortada que exige AC-AVI-04"
    assert descripcion not in cuerpo, "el cuerpo publica la descripcion integra de 900 caracteres en vez de la recortada"
    assert MARCA_FINAL not in cuerpo, f"el final de la descripcion original ('{MARCA_FINAL}') ha llegado al cuerpo de texto"
    assert MARCA_FINAL not in (contenido.cuerpo_html or ""), (
        f"el final de la descripcion original ('{MARCA_FINAL}') ha llegado al cuerpo HTML"
    )

    campos_del_contenido = {campo.name for campo in fields(ContenidoAviso)}
    assert campos_del_contenido == {"asunto", "cuerpo_texto", "cuerpo_html"}, (
        f"ContenidoAviso expone campos inesperados {campos_del_contenido}: el correo no debe poder transportar adjuntos"
    )
    assert AVISO_FOTO_NO_ADJUNTA in cuerpo, "el cuerpo no advierte de que la fotografia asociada no se adjunta al correo"

    assert datos.incident_detail_url in cuerpo, (
        f"el cuerpo de texto no lleva el enlace al detalle en la SPA '{datos.incident_detail_url}'"
    )
    assert f'href="{datos.incident_detail_url}"' in (contenido.cuerpo_html or ""), (
        f"el cuerpo HTML no enlaza al detalle en la SPA con href='{datos.incident_detail_url}'"
    )
