"""
Fixtures compartidas de las pruebas de `apps.core_security`.

El motor de prueba de esta app es el Oracle Database 23ai Free REAL del proyecto
(`gvenzl/oracle-free:23-slim`), con el esquema aplicado por Liquibase
(`liquibase/liquibase:4.31-alpine`) sobre el changelog `sources/facilities/master.xml`.
Esta PROHIBIDO sustituirlo por H2, SQLite o cualquier doble en memoria: un `dict` en
memoria no es evidencia de persistencia, y un test verde contra un sustituto del motor
no acredita que el mapeo ORM case con el DDL que gobierna Liquibase (ARC-016).

La cadena de fixtures (`red_docker` -> `oracle_contenedor` -> `esquema_aplicado` ->
`django_db_setup`) NO se duplica aqui: se REUTILIZA del modulo hermano
`apps.core.tests.test_persistencia_oracle`, de modo que no pueda desincronizarse del DDL
ni de las credenciales reales. Se reexportan tambien `SALTAR_SIN_DOCKER` y
`MOTIVO_SIN_DOCKER` para que las pruebas de esta app los importen de un solo sitio: si no
hay engine Docker alcanzable, las pruebas que tocan Oracle se SALTAN, nunca se degradan a
otro backend.

SEMILLA DE DATOS. Las fixtures `usuario_activo` y `sesion_vigente` siembran filas REALES en
el Oracle del contenedor (tablas `usuario` y `sesion_usuario`), dentro de la transaccion que
`db` revierte al terminar cada prueba. Los codigos de catalogo se LEEN de `cat_rol`, nunca se
inventan: si el changelog dml no hubiese sembrado los roles, la fixture falla a proposito.
"""

from contextlib import AbstractContextManager
from uuid import uuid4

import pytest
from django.contrib.auth.hashers import make_password
from rest_framework.test import APIClient

from apps.core.contexto import ContextoSesion, contexto_de_sesion, utc_now
from apps.core.models.catalogos import RolEntity
from apps.core.models.transaccional import SesionUsuarioEntity, UsuarioEntity
from apps.core.tests.conftest import MOTIVO_SIN_DOCKER  # noqa: F401
from apps.core.tests.test_persistencia_oracle import (  # noqa: F401
    SALTAR_SIN_DOCKER,
    django_db_setup,
    esquema_aplicado,
    oracle_contenedor,
    red_docker,
)
from apps.core_security.servicios.sesiones import ServicioSesiones


#: Contrasenia en claro de la cuenta de prueba. Solo vive aqui: a la base va su hash (REQ-063).
CLAVE_DE_PRUEBA = "Clave-de-prueba-2026"

#: Rol preferido para la cuenta de prueba; si el catalogo no lo trae, se toma el primero activo.
ROL_PREFERIDO = "EMPLEADO"


def cabecera_bearer(credencial: str) -> dict[str, str]:
    """Cabecera `Authorization: Bearer <credencial>` en el formato que espera el cliente de pruebas."""

    return {"HTTP_AUTHORIZATION": f"Bearer {credencial}"}


def contexto_de_sesion_del_usuario(
    usuario: UsuarioEntity, session_id: str | None = None
) -> AbstractContextManager[ContextoSesion]:
    """
    Gestor de contexto con la identidad efectiva del usuario indicado (REQ-064).

    Publica el mismo `ContextoSesion` que arma `ServicioSesiones.autenticar` y lo retira al
    salir, para que las escrituras que pasan por `AtribucionMixin.save()` tengan actor sin
    dejar identidad colgando entre pruebas.
    """

    return contexto_de_sesion(
        ContextoSesion(
            user_id=usuario.user_id,
            role_code=usuario.role_code_id,
            session_id=session_id,
            display_name=usuario.full_name,
        )
    )


def _rol_vigente_de_catalogo() -> RolEntity:
    """
    Devuelve un rol REAL de `cat_rol`, leido de la base y nunca hardcodeado.

    Se prefiere `EMPLEADO` por ser el rol de menor privilegio; si el changelog dml de semillas
    no lo trajese, se toma el primer rol activo por orden de presentacion. Un catalogo vacio es
    un defecto de la tarea de datos y aqui se denuncia fallando, no se tapa con un valor por defecto.
    """

    rol = RolEntity.objects.filter(pk=ROL_PREFERIDO).first()
    if rol is None:
        rol = RolEntity.objects.filter(is_active="Y").order_by("display_order").first()
    assert rol is not None, "cat_rol esta vacio: el changelog dml de semillas no ha sembrado ningun rol activo"
    return rol


@pytest.fixture
def usuario_activo(db) -> UsuarioEntity:
    """
    Usuario ACTIVO sembrado en el Oracle real, con rol leido del catalogo y credencial autentica.

    El alta se hace con `bulk_create`, que NO pasa por `save()` y por tanto no invoca al
    `AtribucionMixin`: es la misma via que usa `apps.core.tests.test_persistencia_oracle` para la
    cuenta semilla, y la UNICA posible aqui, porque la CHECK `ck_usuario_created_by_self` del DDL
    prohibe que un usuario sea su propio `created_by` y porque el `user_id` lo genera la IDENTITY
    de Oracle, de modo que no existe antes del INSERT. El contexto de sesion NO se deja publicado
    al ceder el control a la prueba: hacerlo daria identidad efectiva a peticiones que viajan sin
    credencial y convertiria en verde un caso de AC-SES-04 que deberia ser 401. Quien necesite
    atribucion para escribir usa el gestor `contexto_de_sesion_del_usuario`.

    `password_hash` es un hash REAL de `CLAVE_DE_PRUEBA` generado con `make_password` (Argon2id,
    el primer hasher de `PASSWORD_HASHERS`), no un literal de relleno: asi `check_password` del
    servicio de autenticacion opera sobre material criptografico verdadero. La contrasenia en
    claro no se persiste en ninguna columna (REQ-063).

    El correo y el `username` llevan un sufijo aleatorio para no colisionar con los indices unicos
    insensibles a mayusculas `ux_usuario_email_ci` y `ux_usuario_username_ci` (REQ-045).
    """

    rol = _rol_vigente_de_catalogo()
    sufijo = uuid4().hex[:8]
    correo = f"sesion.activa.{sufijo}@mind.local"

    UsuarioEntity.objects.bulk_create(
        [
            UsuarioEntity(
                full_name=f"Usuario activo de prueba {sufijo}",
                corporate_email=correo,
                username=f"sesion.activa.{sufijo}",
                role_code=rol,
                status="ACTIVO",
                password_hash=make_password(CLAVE_DE_PRUEBA),
                password_algorithm="argon2id",
                must_change_password="N",
                failed_password_attempts=0,
                created_at=utc_now(),
            )
        ]
    )

    # En Oracle `bulk_create` NO devuelve la PK generada: el user_id solo se conoce releyendo.
    return UsuarioEntity.objects.select_related("role_code").get(corporate_email=correo)


@pytest.fixture
def sesion_vigente(db, usuario_activo: UsuarioEntity) -> SesionUsuarioEntity:
    """
    Sesion vigente emitida por el CODIGO PRODUCTIVO (`ServicioSesiones.emitir`).

    La fila no se construye a mano a proposito: la prueba debe ejercitar el camino real de
    emision, con el `session_id` opaco, el rol congelado en el instante de la emision y la
    vigencia absoluta que parametriza el entorno (REQ-055).
    """

    return ServicioSesiones().emitir(usuario_activo)


@pytest.fixture
def cliente_api() -> APIClient:
    """Cliente HTTP de DRF SIN autenticar: cada prueba decide que credencial envia, si envia alguna."""

    return APIClient()
