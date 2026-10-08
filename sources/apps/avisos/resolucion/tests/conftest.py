"""
Fixtures de las pruebas de `apps.avisos.resolucion`.

Aqui NO se reimplementa la infraestructura de contenedores: las fixtures de Oracle
23ai Free (`red_docker`, `oracle_contenedor`, `esquema_aplicado`, `django_db_setup`)
y el actor de sesion se IMPORTAN de `apps.core.tests`. Asi el Oracle de la sesion de
pytest es UNO solo (una unica imagen levantada, compartida por todos los paquetes de
pruebas) y el esquema lo sigue aplicando Liquibase sobre el changelog REAL del repo
(`sources/facilities/master.xml`, ARC-016), nunca una recreacion desde los modelos.

pytest registra como fixture cualquier objeto-fixture presente en el espacio de
nombres de un conftest, de modo que el simple import basta para que esten
disponibles en este paquete.

Las fixtures de siembra de este fichero escriben en el Oracle REAL: no hay dobles en
memoria ni motores alternativos, porque lo que se quiere ejercitar es precisamente el
comportamiento del esquema (CHECKs de coherencia, IDENTITY, indices de unicidad) que un
sustituto en memoria no reproduce.
"""

from collections.abc import Callable, Iterator

import pytest

from apps.avisos.resolucion import ServicioResolucionDestinatarios
from apps.core.contexto import ContextoSesion, contexto_de_sesion, utc_now
from apps.core.models import MotivoDesactivacionEntity, RolEntity, UsuarioEntity
from apps.core.tests.conftest import actor_sesion, sesion_activa  # noqa: F401
from apps.core.tests.test_persistencia_oracle import (  # noqa: F401
    SALTAR_SIN_DOCKER,
    django_db_setup,
    esquema_aplicado,
    oracle_contenedor,
    red_docker,
)


CORREO_SEMILLA: str = "semilla.tsk009@mind.local"
SESION_SEMILLA: str = "9a9a9a9a-0909-4009-8009-090909090909"


@pytest.fixture
def semilla_administrador(db) -> UsuarioEntity:  # noqa: ANN001
    """
    Cuenta SEMILLA del paquete: el unico usuario que se puede escribir sin actor previo.

    Se crea con `bulk_create` a proposito: `bulk_create` NO pasa por `save()` y por tanto NO
    invoca al `AtribucionMixin`, que es la unica manera de insertar la primera fila cuando
    todavia no hay contexto de sesion publicado ni ningun usuario al que atribuir el alta
    (T.5 ARC-110: `created_by` es nulo SOLO en la cuenta semilla).

    En Oracle `bulk_create` no devuelve la PK generada por la IDENTITY, asi que el `user_id`
    solo se conoce releyendo la fila de la base por su correo corporativo.
    """

    rol_administrador: RolEntity = RolEntity.objects.get(pk="ADMINISTRADOR")
    UsuarioEntity.objects.bulk_create(
        [
            UsuarioEntity(
                full_name="Semilla TSK-009",
                corporate_email=CORREO_SEMILLA,
                username="semilla.tsk009",
                role_code=rol_administrador,
                status="ACTIVO",
                password_hash="hash-argon2id-semilla-tsk009",
                password_salt="sal-semilla-tsk009",
                password_algorithm="argon2id",
                must_change_password="Y",
            )
        ]
    )
    return UsuarioEntity.objects.get(corporate_email=CORREO_SEMILLA)


@pytest.fixture
def sesion_administrador(semilla_administrador: UsuarioEntity) -> Iterator[ContextoSesion]:
    """
    Publica el contexto de sesion de la cuenta semilla mientras dura la prueba.

    La atribucion (`created_by`, `changed_by`, `actor_user_id`) de TODO lo que se siembre
    despues sale de este contexto y nunca de un payload (REQ-064): por eso las fabricas no
    informan esos campos a mano. Al salir del `with` el contexto se retira, de modo que una
    prueba no hereda la identidad de otra.
    """

    contexto = ContextoSesion(
        user_id=semilla_administrador.user_id,
        role_code="ADMINISTRADOR",
        session_id=SESION_SEMILLA,
        data_scope="ALL",
        display_name=semilla_administrador.full_name,
    )
    with contexto_de_sesion(contexto) as publicado:
        yield publicado


@pytest.fixture
def crear_usuario(
    semilla_administrador: UsuarioEntity,
    sesion_administrador: ContextoSesion,  # noqa: ARG001
) -> Callable[..., UsuarioEntity]:
    """
    Fabrica de usuarios del directorio, ya persistidos en Oracle.

    Es una FABRICA y no una fixture de dato fijo porque cada escenario de resolucion necesita
    un censo distinto (varios tecnicos, un reportante de baja, un correo ausente) y montarlo
    con fixtures estaticas obligaria a una combinatoria inmanejable.

    Las altas se hacen con `.save()` dentro del contexto de sesion de la semilla, para que sea
    el `AtribucionMixin` quien selle `created_at`/`created_by` igual que en produccion.
    """

    def _crear(*, nombre: str, correo: str, role_code: str, activo: bool = True) -> UsuarioEntity:
        """Da de alta un usuario con el rol indicado; si `activo` es False lo deja de baja logica coherente."""

        # `username` se deriva de la parte local del correo: la CHECK `ck_usuario_username_len`
        # exige entre 3 y 100 caracteres, de modo que se recorta por arriba y se rellena por abajo.
        local: str = correo.split("@")[0][:100]
        username: str = local.ljust(3, "x")

        usuario = UsuarioEntity(
            full_name=nombre,
            corporate_email=correo,
            username=username,
            role_code=RolEntity.objects.get(pk=role_code),
            status="ACTIVO" if activo else "INACTIVO",
            password_hash=f"hash-argon2id-{username}",
            password_salt=f"sal-{username}",
            password_algorithm="argon2id",
            must_change_password="N",
        )

        if not activo:
            # La baja logica NO es solo `status = INACTIVO`: la CHECK `ck_usuario_coherencia_baja`
            # obliga a informar a la vez el cuando, el quien y el porque. Quien rechaza una baja
            # incompleta es ORACLE, no la aplicacion, asi que la fixture tiene que cumplirlo igual
            # que lo cumpliria el caso de uso real.
            motivo = MotivoDesactivacionEntity.objects.filter(is_active="Y").first()
            assert motivo is not None, "cat_motivo_desactivacion no tiene ningun motivo activo sembrado"
            usuario.deactivated_at = utc_now()
            # `ck_usuario_deactivated_by_self` prohibe que el actor de la baja sea el propio
            # usuario dado de baja: se atribuye a la cuenta semilla, que es quien administra.
            usuario.deactivated_by = semilla_administrador
            usuario.deactivation_reason_code = motivo

        usuario.save()
        return usuario

    return _crear


@pytest.fixture
def servicio_resolucion() -> ServicioResolucionDestinatarios:
    """
    Servicio bajo prueba, con sus repositorios REALES.

    No se inyecta ningun doble: estas pruebas son de integracion contra el Oracle de las
    fixtures reexportadas, y sustituir el directorio por un repositorio en memoria dejaria sin
    verificar justo lo que se quiere verificar (que la consulta al censo y el registro del
    intento funcionan contra el esquema de verdad).
    """

    return ServicioResolucionDestinatarios()
