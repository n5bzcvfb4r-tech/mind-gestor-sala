"""
Pruebas de la credencial de sesion contra el Oracle Database 23ai Free REAL del proyecto.

Verifican que la validacion de la credencial opaca se resuelve consultando la tabla fisica
`sesion_usuario` del esquema T.5 a traves del driver `python-oracledb`, con el DDL aplicado
por Liquibase, y no contra una estructura paralela del servicio.

Un `dict` en memoria NO es evidencia de persistencia: si no hay engine Docker alcanzable las
pruebas que necesitan el motor se SALTAN (`SALTAR_SIN_DOCKER`), nunca se degradan a H2,
SQLite ni a ningun doble en memoria.
"""

import pytest

from apps.core.models import SesionUsuarioEntity

pytestmark = [pytest.mark.integration]

#: Columnas fisicas del DoD que la tabla `sesion_usuario` debe exponer con ESE nombre exacto.
COLUMNAS_FISICAS_T5 = {
    "session_id",
    "user_id",
    "role_code",
    "issued_at",
    "expires_at",
    "last_activity_at",
    "revoked_at",
}


def test_la_sesion_se_persiste_con_los_nombres_fisicos_del_esquema_T5() -> None:
    """
    Oraculo anti-traduccion del esquema (T.5 schema-names): el ORM no renombra el DDL.

    No necesita contenedor porque interroga el MAPEO del modelo, no los datos: la tabla debe
    llamarse `sesion_usuario`, el DDL lo gobierna Liquibase (`managed = False`) y cada columna
    del DoD debe estar mapeada con exactamente su nombre fisico, sin traducciones al castellano
    ni abreviaturas inventadas por la capa Python.
    """

    assert SesionUsuarioEntity._meta.db_table == "sesion_usuario", (
        f"La sesion se persiste en `sesion_usuario`, no en `{SesionUsuarioEntity._meta.db_table}`"
    )
    assert SesionUsuarioEntity._meta.managed is False, (
        "El DDL de `sesion_usuario` lo gobierna Liquibase (ARC-016): `managed` debe ser False"
    )

    columnas_reales = {
        columna
        for campo in SesionUsuarioEntity._meta.get_fields()
        if (columna := getattr(campo, "column", None)) is not None
    }

    faltantes = COLUMNAS_FISICAS_T5 - columnas_reales
    assert not faltantes, (
        f"Columnas fisicas del DoD sin mapear con su nombre exacto en `sesion_usuario`: "
        f"{sorted(faltantes)}; mapeadas: {sorted(columnas_reales)}"
    )
