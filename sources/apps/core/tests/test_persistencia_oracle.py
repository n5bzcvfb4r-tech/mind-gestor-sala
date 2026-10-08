"""
Prueba de persistencia del esquema T.5 contra Oracle Database 23ai Free REAL.

El motor se levanta con Testcontainers usando la imagen oficial del proyecto
(`gvenzl/oracle-free:23-slim`), la misma que usa el resto del servicio. El oraculo
de estas pruebas es el dato efectivamente escrito y releido en ese Oracle.

PROHIBIDO sustituir el motor por H2, SQLite o cualquier doble en memoria: un `dict`
en memoria no es evidencia de persistencia, y un test verde contra un sustituto del
motor no acredita que el mapeo ORM case con el DDL que gobierna Liquibase (ARC-016).
Si no hay engine Docker alcanzable, las pruebas que tocan Oracle se SALTAN
(`SALTAR_SIN_DOCKER`); nunca se degradan a otro backend.

El esquema que ven estas pruebas es el DDL REAL de produccion: lo aplica Liquibase
(`liquibase/liquibase:4.31-alpine`) sobre el changelog `sources/facilities/master.xml`,
no se recrea desde los modelos de Django.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connections

import apps.core
from apps.core.contexto import ContextoSesion, contexto_de_sesion, utc_now
from apps.core.models import (
    MODELOS_POR_TABLA,
    AuditoriaAccesoEntity,
    AvisoCorreoEntity,
    AvisoCorreoIntentoEntity,
    CategoriaIncidenciaEntity,
    ConfiguracionSmtpEntity,
    EstadoIncidenciaEntity,
    IncidenciaAdjuntoEntity,
    IncidenciaEntity,
    IncidenciaHistoricoEntity,
    MotivoDesactivacionEntity,
    OficinaEntity,
    OperacionEntity,
    PermisoRolOperacionEntity,
    ResolucionDestinatarioLogEntity,
    RolEntity,
    SalaEntity,
    SesionUsuarioEntity,
    TransicionIncidenciaEntity,
    UsuarioEntity,
    UsuarioHistoricoEntity,
    UsuarioPasswordHistoricoEntity,
)

try:  # El conftest hermano es importable como modulo del paquete de pruebas.
    from apps.core.tests.conftest import MOTIVO_SIN_DOCKER
except ImportError:  # pragma: no cover - red de seguridad si cambia el layout
    MOTIVO_SIN_DOCKER = "Requiere un engine Docker alcanzable para levantar Oracle 23ai con Testcontainers."


def _hay_docker() -> bool:
    """Indica si hay un engine Docker alcanzable; se evalua en tiempo de coleccion."""

    try:
        import docker  # type: ignore[import-not-found]

        docker.from_env().ping()
        return True
    except Exception:
        return False


#: Marca lista para decorar los tests que SI necesitan el contenedor Oracle real.
SALTAR_SIN_DOCKER = pytest.mark.skipif(not _hay_docker(), reason=MOTIVO_SIN_DOCKER)

pytestmark = [pytest.mark.integration]

#: Numero de tablas del inventario T.5.
TABLAS_ESPERADAS_T5 = 21


def _resolver_raiz_repo() -> Path:
    """Resuelve la raiz del repositorio subiendo directorios desde `apps.core`.

    Nunca se codifica una ruta absoluta: se parte del fichero del paquete
    (`apps/core/__init__.py`) y se sube por sus `parents` hasta el directorio que
    contiene el `project-manifest.json` del repo. Si no apareciese (layout movido),
    se cae al cuarto ancestro, que es la raiz en el layout actual
    (`<raiz>/sources/apps/core/__init__.py`).
    """

    paquete = Path(apps.core.__file__).resolve()
    for ancestro in paquete.parents:
        if (ancestro / "project-manifest.json").is_file():
            return ancestro
    return paquete.parents[3]


#: Raiz del repositorio montada dentro del contenedor de Liquibase.
RAIZ_REPO = _resolver_raiz_repo()

#: Imagenes y credenciales: las MISMAS que `local/docker-compose.yml` y el manifiesto.
IMAGEN_ORACLE = "gvenzl/oracle-free:23-slim"
IMAGEN_LIQUIBASE = "liquibase/liquibase:4.31-alpine"
ALIAS_RED_ORACLE = "oracle-facilities"
SERVICIO_ORACLE = "facilities"
USUARIO_APP = "liquibase"
CLAVE_APP = "entornodev"
CLAVE_ADMIN = "entornodev"
PUERTO_ORACLE = 1521
#: Oracle 23ai tarda en arrancar la primera vez; margen amplio a proposito.
ESPERA_ARRANQUE_ORACLE = 300
LOG_ORACLE_LISTO = "DATABASE IS READY TO USE!"


@pytest.fixture(scope="session")
def red_docker() -> Iterator[object]:
    """Red Docker efimera para que Oracle y Liquibase se vean por alias de red."""

    if not _hay_docker():
        pytest.skip(MOTIVO_SIN_DOCKER)

    from testcontainers.core.network import Network

    with Network() as red:
        yield red


@pytest.fixture(scope="session")
def oracle_contenedor(red_docker: object) -> Iterator[object]:
    """Levanta Oracle 23ai Free REAL con la imagen y las credenciales del proyecto.

    Se une a `red_docker` con el alias `oracle-facilities`, el mismo nombre de host
    que `local/facilities.properties` usa en la URL JDBC, de modo que el contenedor
    de Liquibase pueda alcanzarlo sin tocar la configuracion real. El puerto 1521
    queda expuesto al anfitrion para que Django se conecte desde el proceso de test.
    """

    from testcontainers.core.waiting_utils import wait_for_logs
    from testcontainers.oracle import OracleDbContainer

    contenedor = OracleDbContainer(
        image=IMAGEN_ORACLE,
        oracle_password=CLAVE_ADMIN,
        username=USUARIO_APP,
        password=CLAVE_APP,
        dbname=SERVICIO_ORACLE,
        port=PUERTO_ORACLE,
        network=red_docker,
        network_aliases=[ALIAS_RED_ORACLE],
    )
    contenedor.with_exposed_ports(PUERTO_ORACLE)

    with contenedor as oracle:
        # Espera explicita por el log de arranque de la imagen: hasta que no aparece
        # `DATABASE IS READY TO USE!` el listener acepta conexiones a medias.
        wait_for_logs(oracle, LOG_ORACLE_LISTO, timeout=ESPERA_ARRANQUE_ORACLE)
        yield oracle


@pytest.fixture(scope="session")
def esquema_aplicado(oracle_contenedor: object, red_docker: object) -> Iterator[bool]:
    """Aplica el changelog REAL de Liquibase sobre el Oracle del contenedor.

    Ejecuta `liquibase/liquibase:4.31-alpine` (su imagen ya trae el driver ojdbc)
    montando la raiz del repo en `/liquibase/project` y usando el mismo
    `local/facilities.properties` y `sources/facilities/master.xml` que el entorno
    local. Asi el esquema bajo prueba es el DDL de produccion y no una recreacion
    desde los modelos. Si `update` no termina con codigo 0, la prueba FALLA con los
    logs del contenedor: un error de Liquibase nunca se ignora en silencio.
    """

    from testcontainers.core.container import DockerContainer

    comando = [
        "--defaultsFile=local/facilities.properties",
        f"--url=jdbc:oracle:thin:@//{ALIAS_RED_ORACLE}:{PUERTO_ORACLE}/{SERVICIO_ORACLE}",
        f"--username={USUARIO_APP}",
        f"--password={CLAVE_APP}",
        "update",
    ]

    liquibase = DockerContainer(
        IMAGEN_LIQUIBASE,
        command=comando,
        network=red_docker,
        volumes=[(str(RAIZ_REPO), "/liquibase/project", "rw")],
        working_dir="/liquibase/project",
    )

    with liquibase as ejecucion:
        interno = ejecucion.get_wrapped_container()
        resultado = interno.wait(timeout=ESPERA_ARRANQUE_ORACLE)
        codigo_salida = resultado.get("StatusCode", 1) if isinstance(resultado, dict) else resultado
        salida = interno.logs().decode("utf-8", errors="replace")

        if codigo_salida != 0:
            pytest.fail(
                f"Liquibase `update` fallo con codigo {codigo_salida} sobre "
                f"{IMAGEN_LIQUIBASE} (changelog sources/facilities/master.xml).\n"
                f"--- logs del contenedor ---\n{salida}"
            )

        yield True


@pytest.fixture(scope="session")
def django_db_setup(oracle_contenedor: object, esquema_aplicado: bool, django_db_blocker) -> Iterator[None]:
    """Sobreescribe la fixture de pytest-django para apuntar al Oracle del contenedor.

    Todos los modelos del inventario T.5 son `managed = False` (el DDL lo gobierna
    Liquibase, ARC-016), asi que esta fixture NO ejecuta `migrate` ni crea ninguna
    tabla desde Django: el esquema ya esta puesto por `esquema_aplicado`. Tampoco se
    crea una base de pruebas (`TEST["CREATE_DB"] = False`); se trabaja contra el
    esquema real del contenedor.

    El host y el puerto se leen del contenedor YA ARRANCADO (los mapeados al
    anfitrion), nunca se codifican: Testcontainers asigna un puerto efimero.
    """

    anfitrion = oracle_contenedor.get_container_host_ip()
    puerto = oracle_contenedor.get_exposed_port(PUERTO_ORACLE)
    #: Formato `host:puerto/service_name` que entiende el backend Oracle de Django.
    nombre = f"{anfitrion}:{puerto}/{SERVICIO_ORACLE}"

    # Se cierran las conexiones heredadas antes de reapuntar el `settings`.
    connections.close_all()

    settings.DATABASES["default"] = {
        "ENGINE": "django.db.backends.oracle",
        "NAME": nombre,
        "USER": USUARIO_APP,
        "PASSWORD": CLAVE_APP,
        "HOST": "",
        "PORT": "",
        "ATOMIC_REQUESTS": False,
        "AUTOCOMMIT": True,
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": False,
        "OPTIONS": {},
        "TIME_ZONE": None,
        "TEST": {
            "CREATE_DB": False,
            "NAME": nombre,
            "USER": USUARIO_APP,
            "PASSWORD": CLAVE_APP,
            "CHARSET": None,
            "COLLATION": None,
            "MIGRATE": False,
            "MIRROR": None,
        },
    }

    connections.close_all()

    # Comprobacion temprana: si Django no puede hablar con el Oracle real, que el
    # fallo salga aqui y no disfrazado dentro del primer test.
    with django_db_blocker.unblock():
        connections["default"].ensure_connection()

    yield

    connections.close_all()


@pytest.fixture
def conexion_oracle(db) -> object:
    """Conexion Django viva contra el Oracle real del contenedor (alias comodo)."""

    return connections["default"]


def test_el_inventario_T5_declara_las_21_tablas_del_esquema() -> None:
    """Las 21 tablas del inventario T.5 estan mapeadas a un modelo ORM."""

    assert len(MODELOS_POR_TABLA) == TABLAS_ESPERADAS_T5, (
        f"El inventario T.5 debe declarar {TABLAS_ESPERADAS_T5} tablas, "
        f"y declara {len(MODELOS_POR_TABLA)}: {sorted(MODELOS_POR_TABLA)}"
    )

    no_normalizadas = [tabla for tabla in MODELOS_POR_TABLA if tabla != tabla.lower()]
    assert not no_normalizadas, f"Los nombres fisicos deben ir en minuscula: {no_normalizadas}"

    for tabla, modelo in MODELOS_POR_TABLA.items():
        assert modelo._meta.managed is False, f"El DDL de `{tabla}` lo gobierna Liquibase: `managed` debe ser False"
        assert modelo._meta.db_table == tabla, f"`{modelo.__name__}.db_table` es `{modelo._meta.db_table}` y deberia ser `{tabla}`"


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_lee_y_escribe_una_fila_en_cada_tabla_de_catalogo(sesion_activa: ContextoSesion) -> None:
    """
    DoD: «test de persistencia con Testcontainers Oracle 23ai que lee y escribe una fila
    por tabla (dict en memoria NO vale como evidencia)».

    Cubre las 10 tablas de catalogo (ARC-100 .. ARC-109) contra el Oracle 23ai REAL del
    contenedor, con el esquema aplicado por Liquibase. Para cada tabla se hacen DOS cosas:

    * LECTURA: se consulta por el ORM y debe devolver al menos una fila, con al menos una
      columna fisica no clave informada. Las filas las siembra el changelog `dml` (ARC-016);
      si un catalogo viniese vacio, este test FALLA a proposito: es exactamente el defecto
      que persigue el chequeo de arranque de semillas.
    * ESCRITURA: se persiste de verdad y se vuelve a LEER DESDE LA BASE (`refresh_from_db()`
      o un `objects.get(...)` nuevo) comprobando el valor. Dos estrategias segun el dominio:
      UPDATE de una columna descriptiva inocua cuando el dominio esta cerrado por un CHECK y
      agotado por las semillas (un INSERT nuevo es imposible por diseno), e INSERT de una fila
      nueva cuando el dominio es abierto, comprobando ademas que la PK IDENTITY la genera
      Oracle y no el test.

    La marca `django_db` va SIN `transaction=True` a proposito: todo queda envuelto en una
    transaccion que se revierte al terminar, de modo que las semillas del changelog no se
    ensucian. Los campos de atribucion (`created_at`/`created_by`, `updated_at`/`updated_by`)
    NO se informan a mano en ningun punto: los rellena el `AtribucionMixin` desde el contexto
    de sesion que publica la fixture `sesion_activa`.
    """

    actor = sesion_activa.user_id

    # --- 1. cat_rol (ARC-100) -------------------------------------------------------
    # Escritura por UPDATE: `ck_cat_rol_role_code` cierra el dominio a tres codigos y las
    # semillas ya insertan los tres, asi que un INSERT nuevo es imposible por diseno.
    rol = RolEntity.objects.first()
    assert rol is not None, "cat_rol esta vacio: el changelog dml de semillas no ha sembrado los roles"
    assert rol.role_name, "cat_rol.role_name se lee vacio y es una columna NOT NULL del DDL"

    rol.role_description = "Descripcion reescrita por la prueba de persistencia T.5"
    rol.save(update_fields=["role_description"])
    rol_releido = RolEntity.objects.get(pk=rol.pk)
    assert rol_releido.role_description == "Descripcion reescrita por la prueba de persistencia T.5"

    # --- 2. cat_operacion (ARC-101) -------------------------------------------------
    # Escritura por UPDATE: `ck_cat_operacion_code` cierra el dominio a nueve codigos y las
    # semillas insertan los nueve; no queda ningun codigo libre que insertar.
    operacion = OperacionEntity.objects.first()
    assert operacion is not None, "cat_operacion esta vacio: faltan las semillas de operaciones"
    assert operacion.operation_name, "cat_operacion.operation_name se lee vacio y es NOT NULL"

    operacion.operation_name = "Nombre reescrito por la prueba de persistencia T.5"
    operacion.save(update_fields=["operation_name"])
    operacion_releida = OperacionEntity.objects.get(pk=operacion.pk)
    assert operacion_releida.operation_name == "Nombre reescrito por la prueba de persistencia T.5"

    # --- 3. permiso_rol_operacion (ARC-102) -----------------------------------------
    # Escritura por INSERT: el dominio es abierto (cualquier par rol x operacion valido). Se
    # elige un par que las semillas NO conceden, para no chocar con la PK compuesta.
    permiso = PermisoRolOperacionEntity.objects.first()
    assert permiso is not None, "permiso_rol_operacion esta vacio: sin matriz de permisos todo seria denegado"
    assert permiso.data_scope in {"OWN", "ALL"}, f"data_scope fuera del CHECK del DDL: {permiso.data_scope!r}"

    permiso_nuevo = PermisoRolOperacionEntity(
        role_code_id="ADMINISTRADOR",
        operation_code_id="INCIDENT_LIST_ALL",
        data_scope="ALL",
    )
    permiso_nuevo.save()
    assert permiso_nuevo.permiso_id is not None, "permiso_id es IDENTITY: lo genera Oracle, no el test"
    permiso_releido = PermisoRolOperacionEntity.objects.get(pk=permiso_nuevo.pk)
    assert permiso_releido.role_code_id == "ADMINISTRADOR"
    assert permiso_releido.operation_code_id == "INCIDENT_LIST_ALL"
    assert permiso_releido.data_scope == "ALL"

    # --- 4. cat_oficina (ARC-103) ---------------------------------------------------
    # Escritura por INSERT: dominio abierto, solo `office_code` es unico. `office_id` es
    # IDENTITY y NO se informa: debe generarlo Oracle.
    oficina_semilla = OficinaEntity.objects.first()
    assert oficina_semilla is not None, "cat_oficina esta vacio: faltan las semillas de oficinas"
    assert oficina_semilla.office_name, "cat_oficina.office_name se lee vacio y es NOT NULL"

    oficina_nueva = OficinaEntity(
        office_code="TSTP",
        office_name="Oficina de prueba de persistencia",
        city="Madrid",
        is_active="Y",
    )
    oficina_nueva.save()
    assert oficina_nueva.office_id is not None, "office_id es IDENTITY: lo genera Oracle, no el test"
    oficina_releida = OficinaEntity.objects.get(pk=oficina_nueva.pk)
    assert oficina_releida.office_code == "TSTP"
    assert oficina_releida.office_name == "Oficina de prueba de persistencia"
    assert oficina_releida.city == "Madrid"

    # --- 5. cat_sala (ARC-104) ------------------------------------------------------
    # Escritura por INSERT: dominio abierto. Cuelga de la oficina recien insertada (activa),
    # que es lo que exige el trigger `trg_cat_sala_oficina_activa`, y respeta la unicidad de
    # `room_code` y del par (office_id, UPPER(room_name)). `created_at`/`created_by` NO se
    # informan: los pone el `AtribucionMixin` desde el contexto de sesion. El DDL vigente
    # (ddl-0.0.1-08) NO declara FK de `created_by` a `usuario`, por lo que basta el user_id
    # del contexto sin crear antes ninguna cuenta.
    sala_semilla = SalaEntity.objects.first()
    assert sala_semilla is not None, "cat_sala esta vacio: falta el inventario semilla de salas"
    assert sala_semilla.room_name, "cat_sala.room_name se lee vacio y es NOT NULL"

    sala_nueva = SalaEntity(
        room_code="TSTP-S01",
        room_name="Sala de prueba de persistencia",
        office=oficina_nueva,
        is_active="Y",
    )
    sala_nueva.save()
    assert sala_nueva.room_id is not None, "room_id es IDENTITY: lo genera Oracle, no el test"
    sala_releida = SalaEntity.objects.get(pk=sala_nueva.pk)
    assert sala_releida.room_code == "TSTP-S01"
    assert sala_releida.room_name == "Sala de prueba de persistencia"
    assert sala_releida.office_id == oficina_nueva.office_id
    assert sala_releida.created_by == actor, "created_by debe salir del contexto de sesion, no del payload"
    assert sala_releida.created_at is not None, "created_at debe quedar persistido por el AtribucionMixin"

    # --- 6. cat_categoria_incidencia (ARC-105) --------------------------------------
    # Escritura por UPDATE: confirmado en el DDL (`ck_cat_categoria_code`) que el dominio es
    # cerrado a cinco codigos y que las semillas insertan los cinco; no cabe un INSERT nuevo.
    # El nuevo `category_name` debe seguir siendo unico sin distinguir mayusculas
    # (`uk_cat_categoria_name_ci`) y medir entre 3 y 60 caracteres.
    categoria = CategoriaIncidenciaEntity.objects.first()
    assert categoria is not None, "cat_categoria_incidencia esta vacio: faltan las semillas de categorias"
    assert categoria.category_name, "cat_categoria_incidencia.category_name se lee vacio y es NOT NULL"

    categoria.category_name = "Categoria reescrita en persistencia"
    categoria.save()
    categoria.refresh_from_db()
    assert categoria.category_name == "Categoria reescrita en persistencia"
    assert categoria.updated_by == actor, "updated_by debe salir del contexto de sesion"
    assert categoria.updated_at is not None, "updated_at debe quedar persistido por el AtribucionMixin"

    # --- 7. cat_estado_incidencia (ARC-106) -----------------------------------------
    # Escritura por UPDATE: `ck_cat_estado_code` cierra el dominio a cuatro codigos y las
    # semillas insertan los cuatro; el INSERT es imposible por diseno.
    estado = EstadoIncidenciaEntity.objects.first()
    assert estado is not None, "cat_estado_incidencia esta vacio: faltan las semillas del ciclo de vida"
    assert estado.status_name, "cat_estado_incidencia.status_name se lee vacio y es NOT NULL"

    estado.status_name = "Estado reescrito T.5"
    estado.save(update_fields=["status_name"])
    estado.refresh_from_db()
    assert estado.status_name == "Estado reescrito T.5"

    # --- 8. cat_transicion_incidencia (ARC-107) -------------------------------------
    # Escritura por INSERT: dominio abierto (cualquier par origen-destino distinto y no
    # declarado todavia). Se inserta con `is_active = 'N'` para no introducir una transicion
    # de retroceso habilitada ni siquiera dentro de la transaccion de la prueba.
    transicion = TransicionIncidenciaEntity.objects.first()
    assert transicion is not None, "cat_transicion_incidencia esta vacio: sin grafo toda transicion seria denegada"
    assert transicion.requires_comment in {"Y", "N"}, "requires_comment fuera del CHECK del DDL"

    transicion_nueva = TransicionIncidenciaEntity(
        from_status_id="EN_CURSO",
        to_status_id="ABIERTA",
        allowed_role_id="ADMINISTRADOR",
        requires_assignee="N",
        requires_comment="Y",
        is_active="N",
    )
    transicion_nueva.save()
    assert transicion_nueva.transition_id is not None, "transition_id es IDENTITY: lo genera Oracle, no el test"
    transicion_releida = TransicionIncidenciaEntity.objects.get(pk=transicion_nueva.pk)
    assert transicion_releida.from_status_id == "EN_CURSO"
    assert transicion_releida.to_status_id == "ABIERTA"
    assert transicion_releida.allowed_role_id == "ADMINISTRADOR"
    assert transicion_releida.requires_comment == "Y"
    assert transicion_releida.is_active == "N"

    # --- 9. cat_motivo_desactivacion (ARC-108) --------------------------------------
    # Escritura por UPDATE sobre `reason_name`, la estrategia asignada para este catalogo.
    # (Discrepancia reportada al equipo: el DDL vigente NO declara ningun CHECK sobre
    # `reason_code`, por lo que su dominio es de hecho abierto, a diferencia del resto de
    # catalogos cerrados. El UPDATE sigue siendo escritura real y se mantiene.)
    motivo = MotivoDesactivacionEntity.objects.first()
    assert motivo is not None, "cat_motivo_desactivacion esta vacio: faltan las semillas de motivos"
    assert motivo.reason_name, "cat_motivo_desactivacion.reason_name se lee vacio y es NOT NULL"

    motivo.reason_name = "Motivo reescrito por la prueba de persistencia T.5"
    motivo.save(update_fields=["reason_name"])
    motivo.refresh_from_db()
    assert motivo.reason_name == "Motivo reescrito por la prueba de persistencia T.5"

    # --- 10. configuracion_smtp (ARC-109) -------------------------------------------
    # Escritura por INSERT: dominio abierto. La fila nace INACTIVA porque el indice unico
    # `ux_configuracion_smtp_una_activa` solo admite una configuracion activa. La columna del
    # secreto es `secreto_ref` (nombre del DDL real) y guarda una REFERENCIA externa, nunca la
    # credencial. `updated_at`/`updated_by` NO se informan: son responsabilidad del
    # `AtribucionMixin` a partir del contexto de sesion.
    smtp_semilla = ConfiguracionSmtpEntity.objects.first()
    assert smtp_semilla is not None, "configuracion_smtp esta vacia: falta la configuracion semilla de correo"
    assert smtp_semilla.smtp_host, "configuracion_smtp.smtp_host se lee vacio y es NOT NULL"

    smtp_nueva = ConfiguracionSmtpEntity(
        smtp_host="smtp.persistencia.local",
        smtp_port=587,
        use_tls="Y",
        smtp_username="buzon-de-pruebas",
        secreto_ref="vault://facilities/smtp#persistencia-t5",
        sender_address="no-reply@persistencia.local",
        sender_display_name="Prueba de persistencia T.5",
        max_attempts=3,
        is_active="N",
    )
    smtp_nueva.save()
    assert smtp_nueva.config_id is not None, "config_id es IDENTITY: lo genera Oracle, no el test"
    smtp_releida = ConfiguracionSmtpEntity.objects.get(pk=smtp_nueva.pk)
    assert smtp_releida.smtp_host == "smtp.persistencia.local"
    assert smtp_releida.smtp_port == 587
    assert smtp_releida.secreto_ref == "vault://facilities/smtp#persistencia-t5"
    assert smtp_releida.sender_address == "no-reply@persistencia.local"
    assert smtp_releida.updated_by == actor, "updated_by debe salir del contexto de sesion, no del payload"
    assert smtp_releida.updated_at is not None, "updated_at debe quedar persistido por el AtribucionMixin"


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_lee_y_escribe_una_fila_en_cada_tabla_transaccional_y_de_evento() -> None:
    """
    DoD: «test de persistencia con Testcontainers Oracle 23ai que lee y escribe una fila
    por tabla (dict en memoria NO vale como evidencia)».

    Completa el inventario T.5 con las 11 tablas que no son de catalogo: las 6 transaccionales
    (`usuario`, `usuario_password_historico`, `sesion_usuario`, `incidencia`,
    `incidencia_adjunto`, `aviso_correo`) y las 5 de evento append-only (`usuario_historico`,
    `auditoria_acceso`, `incidencia_historico`, `aviso_correo_intento`,
    `resolucion_destinatario_log`). Se recorren en ORDEN DE DEPENDENCIA de claves ajenas, y en
    cada una se hace ESCRITURA real (INSERT por el ORM contra el Oracle 23ai del contenedor) y
    LECTURA posterior DESDE LA BASE (`objects.get(...)` nuevo o `refresh_from_db()`),
    comprobando los valores escritos y que la PK la genera la IDENTITY de Oracle donde procede.

    Las tablas de evento heredan `RegistroInmutableMixin`: admiten INSERT y PROHIBEN UPDATE, de
    modo que su verificacion de lectura se hace releyendo la fila, nunca reescribiendola.

    La marca `django_db` va SIN `transaction=True` a proposito: todo queda envuelto en una
    transaccion que se revierte al terminar, de modo que ni las semillas del changelog ni el
    censo quedan ensuciados por la prueba.

    Los campos de atribucion (`created_by`/`created_at`, `changed_by`/`changed_at`,
    `reported_by`, `actor_user_id`) NO se informan a mano en ningun punto: los rellena el
    `AtribucionMixin` desde el contexto de sesion que se publica mas abajo (REQ-064).
    """

    # Catalogos SEMBRADOS por el changelog dml (ARC-016): se LEEN de la base, no se inventan.
    rol_administrador = RolEntity.objects.get(pk="ADMINISTRADOR")
    rol_empleado = RolEntity.objects.get(pk="EMPLEADO")
    estado_abierta = EstadoIncidenciaEntity.objects.get(pk="ABIERTA")
    categoria = CategoriaIncidenciaEntity.objects.filter(is_active="Y").first()
    assert categoria is not None, "cat_categoria_incidencia no tiene ninguna categoria activa sembrada"
    sala = SalaEntity.objects.filter(is_active="Y").select_related("office").first()
    assert sala is not None, "cat_sala no tiene ninguna sala activa sembrada"

    # --- usuario (cuenta SEMILLA) ---------------------------------------------------
    # La cuenta semilla es la UNICA fila de `usuario` sin actor previo (T.5 ARC-110:
    # `created_by` «nulo solo en la cuenta semilla»). Se crea con `bulk_create`, que NO pasa
    # por `save()` y por tanto NO invoca al `AtribucionMixin`: asi se puede escribir sin que
    # exista todavia ningun contexto de sesion ni ningun usuario al que atribuir el alta.
    UsuarioEntity.objects.bulk_create(
        [
            UsuarioEntity(
                full_name="Semilla de persistencia T.5",
                corporate_email="semilla.persistencia.t5@mind.local",
                username="semilla.persistencia.t5",
                role_code=rol_administrador,
                status="ACTIVO",
                password_hash="hash-argon2id-semilla-persistencia-t5",
                password_salt="sal-semilla-persistencia-t5",
                password_algorithm="argon2id",
                must_change_password="Y",
            )
        ]
    )

    # LECTURA de `usuario`: en Oracle `bulk_create` NO devuelve las PK generadas, asi que el
    # user_id de la IDENTITY solo se conoce releyendo la fila de la base por su correo.
    semilla = UsuarioEntity.objects.get(corporate_email="semilla.persistencia.t5@mind.local")
    assert semilla.user_id is not None, "user_id es IDENTITY: lo genera Oracle, no el test"
    assert semilla.username == "semilla.persistencia.t5"
    assert semilla.created_by_id is None, "la cuenta semilla es la unica con created_by nulo (T.5 ARC-110)"

    contexto = ContextoSesion(
        user_id=semilla.user_id,
        role_code="ADMINISTRADOR",
        session_id="22222222-2222-4222-8222-222222222222",
        data_scope="ALL",
        display_name="Semilla de persistencia T.5",
    )

    with contexto_de_sesion(contexto):
        # --- usuario (ESCRITURA con atribucion de sesion) ---------------------------
        # Segunda cuenta, ya creada con `save()` dentro del contexto: `created_by` NO se informa
        # y debe quedar sellado con el id de la semilla por el `AtribucionMixin`.
        usuario = UsuarioEntity.objects.create(
            full_name="Tecnico de persistencia T.5",
            corporate_email="tecnico.persistencia.t5@mind.local",
            username="tecnico.persistencia.t5",
            role_code=rol_empleado,
            password_hash="hash-argon2id-tecnico-persistencia-t5",
            password_salt="sal-tecnico-persistencia-t5",
            password_algorithm="argon2id",
        )
        assert usuario.user_id is not None, "user_id es IDENTITY: lo genera Oracle, no el test"

        usuario_releido = UsuarioEntity.objects.get(pk=usuario.pk)
        assert usuario_releido.corporate_email == "tecnico.persistencia.t5@mind.local"
        assert usuario_releido.role_code_id == "EMPLEADO"
        assert usuario_releido.status == "ACTIVO"
        assert usuario_releido.created_by_id == semilla.user_id, (
            "created_by debe salir del contexto de sesion, no del payload"
        )
        assert usuario_releido.created_at is not None, "created_at debe quedar persistido por el AtribucionMixin"

        # --- usuario_password_historico ---------------------------------------------
        # Append-only (REQ-069): guarda el hash de la contrasenia que DEJA de ser vigente.
        historico_password = UsuarioPasswordHistoricoEntity(
            user=usuario,
            password_hash="hash-argon2id-anterior-persistencia-t5",
        )
        historico_password.save()
        assert historico_password.password_history_id is not None, (
            "password_history_id es IDENTITY: lo genera Oracle, no el test"
        )
        password_releida = UsuarioPasswordHistoricoEntity.objects.get(pk=historico_password.pk)
        assert password_releida.user_id == usuario.user_id
        assert password_releida.password_hash == "hash-argon2id-anterior-persistencia-t5"
        assert password_releida.created_at is not None

        # --- sesion_usuario -----------------------------------------------------------
        # `session_id` es un uuid generado en PYTHON por el `default` del modelo: NO se informa.
        emitida = utc_now()
        sesion = SesionUsuarioEntity(
            user=usuario,
            role_code=rol_empleado,
            issued_at=emitida,
            expires_at=emitida + timedelta(hours=8),
            last_activity_at=emitida,
        )
        sesion.save()
        assert sesion.session_id, "session_id lo genera el default del modelo en Python, no la base"

        sesion_releida = SesionUsuarioEntity.objects.get(pk=sesion.pk)
        assert sesion_releida.user_id == usuario.user_id
        assert sesion_releida.role_code_id == "EMPLEADO"
        assert sesion_releida.revoked_at is None and sesion_releida.revocation_reason is None
        assert sesion_releida.expires_at > sesion_releida.issued_at

        # --- usuario_historico (evento append-only) -----------------------------------
        # Asiento de alta de rol: `change_type = 'ROL'` con `previous_role_code` nulo y
        # `valid_to` nulo, la unica combinacion que admite `ck_usuario_hist_coherencia`.
        historico_usuario = UsuarioHistoricoEntity(
            user=usuario,
            change_type="ROL",
            new_role_code=rol_empleado,
            note="Alta de rol registrada por la prueba de persistencia T.5",
        )
        historico_usuario.save()
        assert historico_usuario.history_id is not None, "history_id es IDENTITY: lo genera Oracle, no el test"

        historico_usuario_releido = UsuarioHistoricoEntity.objects.get(pk=historico_usuario.pk)
        assert historico_usuario_releido.user_id == usuario.user_id
        assert historico_usuario_releido.change_type == "ROL"
        assert historico_usuario_releido.new_role_code_id == "EMPLEADO"
        assert historico_usuario_releido.previous_role_code_id is None
        assert historico_usuario_releido.valid_to is None
        assert historico_usuario_releido.changed_by_id == semilla.user_id, (
            "changed_by debe salir del contexto de sesion, no del payload"
        )
        assert historico_usuario_releido.changed_at is not None
        # Columnas VIRTUALES del DDL: se leen, nunca se escriben.
        assert historico_usuario_releido.event_type == "ROL"
        assert historico_usuario_releido.new_value == "EMPLEADO"

        # --- auditoria_acceso (evento append-only) ------------------------------------
        # `audit_id` es un uuid con `default` en el modelo y `occurred_at` la sella el servidor
        # en `save()`: ninguno de los dos se informa desde el test.
        auditoria = AuditoriaAccesoEntity(
            user=usuario,
            username_attempted=usuario.username,
            event_type="login_ok",
            operation="AUTH_LOGIN",
            outcome="OK",
            ip_address="10.0.0.9",
            user_agent="pytest/persistencia-t5",
            session_id=sesion.session_id,
        )
        auditoria.save()
        assert auditoria.audit_id, "audit_id lo genera el default del modelo en Python, no la base"

        auditoria_releida = AuditoriaAccesoEntity.objects.get(pk=auditoria.pk)
        assert auditoria_releida.user_id == usuario.user_id
        assert auditoria_releida.event_type == "login_ok"
        assert auditoria_releida.outcome == "OK"
        assert auditoria_releida.session_id == sesion.session_id
        assert auditoria_releida.occurred_at is not None, "occurred_at lo sella el servidor, nunca el cliente"
        # Columna VIRTUAL del DDL: occurred_at + 24 meses (retencion de 2 anios).
        assert auditoria_releida.retention_until is not None

        # --- incidencia ---------------------------------------------------------------
        # `reported_by` es el actor de alta del `AtribucionMixin`: sale del contexto de sesion.
        # Las denominaciones `*_snapshot` congelan el literal VIGENTE del catalogo releido.
        incidencia = IncidenciaEntity(
            reference_code="INC-PERSIST-T5-0001",
            room=sala,
            category=categoria,
            room_name_snapshot=sala.room_name,
            office_name_snapshot=sala.office.office_name,
            category_name_snapshot=categoria.category_name,
            description="Proyector de la sala sin senal durante la prueba de persistencia T.5",
            status=estado_abierta,
        )
        incidencia.save()
        assert incidencia.incident_id is not None, "incident_id es IDENTITY: lo genera Oracle, no el test"

        incidencia_releida = IncidenciaEntity.objects.get(pk=incidencia.pk)
        assert incidencia_releida.reference_code == "INC-PERSIST-T5-0001"
        assert incidencia_releida.room_id == sala.room_id
        assert incidencia_releida.category_id == categoria.category_id
        assert incidencia_releida.status_id == "ABIERTA"
        assert incidencia_releida.room_name_snapshot == sala.room_name
        assert incidencia_releida.category_name_snapshot == categoria.category_name
        assert incidencia_releida.version == 0
        assert incidencia_releida.reported_by_id == semilla.user_id, (
            "reported_by debe salir del contexto de sesion, no del payload"
        )
        assert incidencia_releida.created_at is not None

        # --- incidencia_adjunto --------------------------------------------------------
        # El binario NO vive en la base: solo metadatos y la clave opaca del almacen.
        checksum = hashlib.sha256(b"adjunto-de-la-prueba-de-persistencia-t5").hexdigest()
        adjunto = IncidenciaAdjuntoEntity(
            incident=incidencia,
            file_name="evidencia-persistencia.png",
            mime_type="image/png",
            file_size_bytes=20480,
            file_checksum=checksum,
            storage_key="almacen/persistencia-t5/evidencia-persistencia-0001.png",
            uploaded_by=usuario,
        )
        adjunto.save()
        assert adjunto.attachment_id is not None, "attachment_id es IDENTITY: lo genera Oracle, no el test"

        adjunto_releido = IncidenciaAdjuntoEntity.objects.get(pk=adjunto.pk)
        assert adjunto_releido.incident_id == incidencia.incident_id
        assert adjunto_releido.file_name == "evidencia-persistencia.png"
        assert adjunto_releido.mime_type == "image/png"
        assert adjunto_releido.file_size_bytes == 20480
        assert adjunto_releido.file_checksum == checksum
        assert adjunto_releido.uploaded_by_id == usuario.user_id
        assert adjunto_releido.uploaded_at is not None

        # --- incidencia_historico (evento append-only) ---------------------------------
        # Asiento de CREACION: sin estado de origen, destino ABIERTA y sin huella previa; la
        # huella propia es SHA-256 en HEXADECIMAL MAYUSCULA (`ck_inc_hist_entry_hash`).
        huella = hashlib.sha256(b"asiento-creacion-persistencia-t5").hexdigest().upper()
        historico_incidencia = IncidenciaHistoricoEntity(
            incident=incidencia,
            entry_type="CREACION",
            to_status=estado_abierta,
            actor_display_name="Semilla de persistencia T.5",
            entry_comment="Alta registrada por la prueba de persistencia T.5",
            entry_hash=huella,
        )
        historico_incidencia.save()
        assert historico_incidencia.history_id is not None, "history_id es IDENTITY: lo genera Oracle, no el test"

        historico_incidencia_releido = IncidenciaHistoricoEntity.objects.get(pk=historico_incidencia.pk)
        assert historico_incidencia_releido.incident_id == incidencia.incident_id
        assert historico_incidencia_releido.entry_type == "CREACION"
        assert historico_incidencia_releido.from_status_id is None
        assert historico_incidencia_releido.to_status_id == "ABIERTA"
        assert historico_incidencia_releido.previous_entry_hash is None
        assert historico_incidencia_releido.entry_hash == huella
        assert historico_incidencia_releido.actor_user_id == semilla.user_id, (
            "actor_user_id debe salir del contexto de sesion, no del payload"
        )
        assert historico_incidencia_releido.changed_at is not None
        # Columnas VIRTUALES del DDL: se leen, nunca se escriben.
        assert historico_incidencia_releido.value_before is None
        assert historico_incidencia_releido.value_after == "ABIERTA"

        # --- aviso_correo ---------------------------------------------------------------
        # Aviso de alta de incidencia: `ck_aviso_correo_vinculo` exige incidencia informada y
        # entrada de historial vacia para `NEW_INCIDENT_ALERT`. `notification_id` es un uuid con
        # `default` en el modelo: no se informa.
        aviso = AvisoCorreoEntity(
            notification_key="NEW_INCIDENT_ALERT:INC-PERSIST-T5-0001",
            notification_type="NEW_INCIDENT_ALERT",
            incident=incidencia,
            recipient_user=usuario,
            recipient_email=usuario.corporate_email,
            subject="Nueva incidencia INC-PERSIST-T5-0001",
            body_text="Se ha registrado una incidencia durante la prueba de persistencia T.5.",
            status="PENDIENTE",
            max_attempts=3,
        )
        aviso.save()
        assert aviso.notification_id, "notification_id lo genera el default del modelo en Python, no la base"

        aviso_releido = AvisoCorreoEntity.objects.get(pk=aviso.pk)
        assert aviso_releido.notification_key == "NEW_INCIDENT_ALERT:INC-PERSIST-T5-0001"
        assert aviso_releido.notification_type == "NEW_INCIDENT_ALERT"
        assert aviso_releido.incident_id == incidencia.incident_id
        assert aviso_releido.recipient_user_id == usuario.user_id
        assert aviso_releido.status == "PENDIENTE"
        assert aviso_releido.attempt_count == 0
        assert aviso_releido.created_at is not None

        # --- aviso_correo_intento (evento append-only) ------------------------------------
        # Primer intento fallido de forma transitoria: `ck_aviso_intento_sin_dest` exige al menos
        # un destinatario salvo en `NO_RECIPIENTS`, y el snapshot debe ser JSON valido.
        instantanea = json.dumps(
            [{"nombre": usuario.full_name, "correo": usuario.corporate_email}],
            ensure_ascii=False,
        )
        intento = AvisoCorreoIntentoEntity(
            notification=aviso,
            incident=incidencia,
            attempt_number=1,
            result_code="TRANSIENT_ERROR",
            smtp_response_code="451",
            error_code="SMTP_TEMPORARY_FAILURE",
            error_message="El servidor SMTP respondio 451 durante la prueba de persistencia T.5",
            recipients_snapshot=instantanea,
            recipient_count=1,
        )
        intento.save()
        assert intento.attempt_id, "attempt_id lo genera el default del modelo en Python, no la base"

        intento_releido = AvisoCorreoIntentoEntity.objects.get(pk=intento.pk)
        assert intento_releido.notification_id == aviso.notification_id
        assert intento_releido.incident_id == incidencia.incident_id
        assert intento_releido.attempt_number == 1
        assert intento_releido.result_code == "TRANSIENT_ERROR"
        assert intento_releido.smtp_response_code == "451"
        assert intento_releido.recipient_count == 1
        assert json.loads(intento_releido.recipients_snapshot)[0]["correo"] == usuario.corporate_email
        assert intento_releido.attempted_at is not None
        # Columnas VIRTUALES del DDL: alias publicados sobre notification_id y result_code.
        assert intento_releido.dispatch_id == aviso.notification_id
        assert intento_releido.result == "TRANSIENT_ERROR"

        # --- resolucion_destinatario_log (evento append-only) -----------------------------
        # Resolucion INDIVIDUAL: exige sujeto informado, y `ck_res_dest_cardinalidad` obliga a que
        # `recipient_count` coincida con el tamanio de la lista JSON de identificadores. Se
        # guardan IDENTIFICADORES, nunca correos en claro.
        identificadores = json.dumps([usuario.user_id])
        resolucion = ResolucionDestinatarioLogEntity(
            request_type="INDIVIDUAL",
            requested_by_module="prueba-persistencia-t5",
            subject_user=usuario,
            resolved_user_ids=identificadores,
            recipient_count=1,
            is_fallback_used="N",
            outcome="OK",
            resolved_by_user=semilla,
        )
        resolucion.save()
        assert resolucion.resolution_id is not None, "resolution_id es IDENTITY: lo genera Oracle, no el test"

        resolucion_releida = ResolucionDestinatarioLogEntity.objects.get(pk=resolucion.pk)
        assert resolucion_releida.request_type == "INDIVIDUAL"
        assert resolucion_releida.requested_by_module == "prueba-persistencia-t5"
        assert resolucion_releida.subject_user_id == usuario.user_id
        assert json.loads(resolucion_releida.resolved_user_ids) == [usuario.user_id]
        assert resolucion_releida.recipient_count == 1
        assert resolucion_releida.outcome == "OK"
        assert resolucion_releida.resolved_by_user_id == semilla.user_id
        assert resolucion_releida.resolved_at is not None
        # Columnas VIRTUALES del DDL que sostienen la coherencia de la lista de identificadores.
        assert resolucion_releida.resolved_ids_cardinalidad == 1
        assert resolucion_releida.resolved_ids_solo_numeros == 1
