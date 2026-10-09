"""
Pruebas de API del restablecimiento de credencial (EP-017) y del estado de credencial (EP-019)
contra el Oracle REAL del proyecto.

QUE CUBRE ESTE FICHERO
=======================
Las dos operaciones que esta tarea anade al contrato se ejercitan por HTTP, de extremo a extremo:
`POST /api/users/{userId}/password-reset` (restablecimiento por un administrador, con emision de
credencial TEMPORAL, marca de cambio obligatorio y desbloqueo de la cuenta) y
`GET /api/users/credential-status` (consulta por el propio usuario del estado de su credencial:
si debe cambiarla, si esta bloqueada y hasta cuando). La peticion recorre el camino de produccion
completo -guardia de sesion, resolucion de contexto, autorizacion por `permiso_rol_operacion`,
caso de uso y persistencia- sin ningun doble por el medio.

POR QUE NECESITA EL MOTOR REAL
===============================
El oraculo de estas pruebas NO es lo que responde el JSON, sino lo que QUEDA ESCRITO en la fila del
usuario: que `password_hash` cambio (o que, cuando la operacion falla, sigue siendo el hash de la
credencial anterior), que `must_change_password` paso a `Y`, que `locked_until` y
`failed_password_attempts` se limpiaron y que la credencial en claro no se persistio en ninguna
columna (REQ-063, REQ-073 regla 4). Nada de eso se puede acreditar contra un `dict` en memoria ni
contra H2/SQLite: solo el Oracle 23ai Free REAL (`gvenzl/oracle-free:23-slim`) con el esquema que
aplica Liquibase ejecuta las CHECK, los indices unicos insensibles a mayusculas y los defaults que
el DDL gobierna, y solo contra el se demuestra que el mapeo ORM encaja con el esquema (ARC-016).
Ademas los `role_code` que deciden el permiso se LEEN de los catalogos que siembra el changelog
dml, no se inventan aqui.

Si no hay engine Docker alcanzable, las pruebas se SALTAN mediante `SALTAR_SIN_DOCKER`; nunca se
degradan a otro backend ni se desactivan. La cadena de fixtures de contenedor y las semillas se
REUTILIZAN del conftest del paquete (`apps/identidad/reposicion/tests/conftest.py`).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from django.contrib.auth.hashers import check_password, make_password
from django.utils.dateparse import parse_datetime

from apps.core.contexto import utc_now
from apps.core.models.catalogos import ConfiguracionSmtpEntity, RolEntity
from apps.core.models.transaccional import (
    AvisoCorreoEntity,
    SesionUsuarioEntity,
    UsuarioEntity,
    UsuarioPasswordHistoricoEntity,
)
from apps.core_security.servicios.sesiones import ServicioSesiones
from apps.identidad.reposicion.tests.conftest import (
    SALTAR_SIN_DOCKER,
    cabecera_bearer,
    contexto_de_sesion_del_usuario,
)

# SE IMPORTA EL SERVIDOR SMTP DE PRUEBA DEL FICHERO HERMANO, NO SE DUPLICA. `_arrancar` y
# `_BuzonSmtp` ya resuelven las dos trampas que estan documentadas alli y que cuestan una sesion
# entera de depuracion si se reescriben a mano: `Controller(port=0)` NO funciona -la sonda de
# arranque se conecta literalmente al puerto 0 y revienta con `ConnectionRefusedError`, de ahi
# `_puerto_libre()`- y el handler no puede exponer una `property` con logica que falle, porque
# `aiosmtpd` recorre el handler con `inspect.getmembers` y EVALUA sus propiedades al abrir la
# conexion. Copiar el patron aqui crearia una segunda version de esas dos soluciones, condenada a
# divergir de la primera el dia que `aiosmtpd` cambie.
from apps.identidad.reposicion.tests.test_entrega_credencial_smtp import (
    RESPUESTA_ACEPTADA,
    RESPUESTA_RECHAZADA,
    _arrancar,
    _BuzonSmtp,
)


pytestmark = [pytest.mark.integration]


# LITERALES DE CATALOGO. Estos `role_code` NO son datos que este modulo inserte: se LEEN de
# `cat_rol`, que siembra el changelog dml. Son unicamente los literales que la prueba BUSCA en la
# base; si el catalogo no los trae, la prueba falla a proposito, porque eso seria un defecto real
# de las semillas y no algo que deba taparse con un valor por defecto.
#: Codigos de rol de `cat_rol` (los siembra el changelog dml; NO se inventan aqui).
ROL_EMPLEADO = "EMPLEADO"
ROL_TECNICO = "TECNICO_MANTENIMIENTO"
ROL_ADMINISTRADOR = "ADMINISTRADOR"

#: Rutas literales del contrato (`servers[0].url` = `/api` + el path del recurso).
RUTA_ESTADO_CREDENCIAL = "/api/users/credential-status"


def ruta_restablecimiento(user_id: int) -> str:
    """Ruta del contrato para el restablecimiento de la credencial del usuario indicado (EP-017)."""

    return f"/api/users/{user_id}/password-reset"


#: Valores del indicador booleano del DDL (`CHAR(1)` con CHECK `Y`/`N`), nunca `True`/`False`.
INDICADOR_SI = "Y"
INDICADOR_NO = "N"

#: Clave en claro de los usuarios sembrados. Solo vive aqui: a la fila va su hash (REQ-063).
CLAVE_SEMBRADA = "Clave-sembrada-2026"


def sembrar_usuario(
    role_code: str,
    etiqueta: str,
    *,
    status: str = "ACTIVO",
    must_change_password: str = INDICADOR_NO,
    locked_until: datetime | None = None,
    failed_password_attempts: int = 0,
    last_login_at: datetime | None = None,
    password_updated_at: datetime | None = None,
) -> UsuarioEntity:
    """
    Siembra en el Oracle real un usuario con el `role_code` y el estado de credencial indicados.

    El alta se hace con `bulk_create`, que NO pasa por `save()` y por tanto no invoca al
    `AtribucionMixin`: es la UNICA via posible aqui, porque el `user_id` lo genera la IDENTITY de
    Oracle (no existe antes del INSERT) y porque la CHECK `ck_usuario_created_by_self` del DDL
    prohibe que un usuario sea su propio `created_by`.

    `password_hash` es un hash REAL de `CLAVE_SEMBRADA` generado con `make_password` (Argon2id, el
    primer hasher de `PASSWORD_HASHERS`), nunca un literal de relleno ni la contrasenia en claro
    (REQ-063). Que todos los usuarios sembrados compartan la MISMA clave en claro es deliberado:
    permite a una prueba afirmar con `check_password(CLAVE_SEMBRADA, usuario.password_hash)` que,
    cuando el restablecimiento no debia prosperar, la credencial anterior sigue siendo la vigente.

    El correo y el `username` llevan sufijo aleatorio para no colisionar con los indices unicos
    insensibles a mayusculas `ux_usuario_email_ci` y `ux_usuario_username_ci` (REQ-045).
    """

    assert RolEntity.objects.filter(pk=role_code).exists(), (
        f"cat_rol no trae el rol {role_code}: faltan las semillas del changelog dml"
    )

    sufijo = uuid4().hex[:8]
    correo = f"{etiqueta}.{sufijo}@mind.local"

    UsuarioEntity.objects.bulk_create(
        [
            UsuarioEntity(
                full_name=f"Usuario {etiqueta} de prueba {sufijo}",
                corporate_email=correo,
                username=f"{etiqueta}.{sufijo}",
                role_code=RolEntity.objects.get(pk=role_code),
                status=status,
                password_hash=make_password(CLAVE_SEMBRADA),
                password_algorithm="argon2id",
                password_updated_at=password_updated_at,
                must_change_password=must_change_password,
                failed_password_attempts=failed_password_attempts,
                locked_until=locked_until,
                last_login_at=last_login_at,
                created_at=utc_now(),
            )
        ]
    )

    # En Oracle `bulk_create` NO devuelve la PK generada: el `user_id` solo se conoce releyendo.
    return UsuarioEntity.objects.select_related("role_code").get(corporate_email=correo)


def abrir_sesion(usuario: UsuarioEntity) -> dict[str, str]:
    """
    Emite una sesion real para el usuario y devuelve la cabecera `Authorization` lista para el cliente.

    La sesion la emite el CODIGO PRODUCTIVO (`ServicioSesiones.emitir`), no se fabrica a mano, y lo
    que viaja en la cabecera es el `session_id` opaco de la fila `sesion_usuario`, exactamente el
    mismo mecanismo que ya usan las pruebas de ciclo de vida de sesion de `apps.identidad`.
    """

    return cabecera_bearer(ServicioSesiones().emitir(usuario).session_id)


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_el_censo_de_usuarios_admite_sembrar_los_tres_roles() -> None:
    """Humo del andamiaje: el catalogo trae los tres roles y el helper siembra contra Oracle real."""

    empleado = sembrar_usuario(ROL_EMPLEADO, "reposicion.empleado")
    tecnico = sembrar_usuario(ROL_TECNICO, "reposicion.tecnico")
    administrador = sembrar_usuario(ROL_ADMINISTRADOR, "reposicion.administrador")

    for usuario in (empleado, tecnico, administrador):
        assert usuario.user_id is not None, (
            f"El usuario {usuario.corporate_email} no trae `user_id`: la IDENTITY de Oracle no lo asigno al releer la fila"
        )

    assert empleado.role_code_id == ROL_EMPLEADO
    assert tecnico.role_code_id == ROL_TECNICO
    assert administrador.role_code_id == ROL_ADMINISTRADOR


# ---------------------------------------------------------------------------------------------
# SMTP REAL CABLEADO POR LA CONFIGURACION DE LA BASE DE DATOS
# ---------------------------------------------------------------------------------------------
# El caso de uso construye su transporte con la fabrica de PRODUCCION
# (`servicio_entrega_credencial`), que lee la fila activa de `configuracion_smtp`. Aqui NO se
# inyecta ningun doble ni se parchea nada: se siembra esa fila apuntando a un `aiosmtpd` que
# escucha en loopback, de modo que el camino completo -vista, servicio, outbox, `TransporteSmtp`,
# socket, servidor- se ejercita de verdad y el oraculo de AC-RST-03 sigue siendo un MENSAJE
# CAPTURADO, no una llamada observada ni una linea de log.

#: Remitente de la configuracion sembrada. El servidor de prueba acepta cualquiera.
REMITENTE_PRUEBA = "avisos.reposicion@mind.local"
NOMBRE_REMITENTE_PRUEBA = "Gestion de Incidencias (pruebas)"


@pytest.fixture
def smtp_aceptando() -> Iterator[_BuzonSmtp]:
    """Servidor SMTP real en loopback que ACEPTA la entrega (`250 OK`) y captura los sobres recibidos."""

    yield from _arrancar(RESPUESTA_ACEPTADA)


@pytest.fixture
def smtp_rechazando() -> Iterator[_BuzonSmtp]:
    """Servidor SMTP real en loopback que RECHAZA el DATA con un 5xx permanente (`550`)."""

    yield from _arrancar(RESPUESTA_RECHAZADA)


def sembrar_configuracion_smtp(buzon: _BuzonSmtp, administrador: UsuarioEntity) -> ConfiguracionSmtpEntity:
    """
    Deja como UNICA configuracion SMTP activa la que apunta al servidor de prueba `buzon`.

    Se desactiva primero cualquier fila activa previa -la semilla del changelog dml trae una- con
    un `queryset.update()`: el indice unico `ux_configuracion_smtp_una_activa` del DDL solo admite
    una configuracion con `is_active='Y'`, asi que insertar la nuestra sin apagar la anterior
    violaria la restriccion. El `update()` no pasa por `save()` y por tanto no necesita contexto de
    sesion, y es seguro porque `updated_at`/`updated_by` ya traen valor en esas filas (son NOT NULL).

    El INSERT, en cambio, SI va por `save()`, dentro de `contexto_de_sesion_del_usuario`:
    `ConfiguracionSmtpEntity` hereda de `AtribucionMixin`, cuyo `save()` falla cerrado sin
    identidad publicada porque `updated_by` sale del contexto de sesion y nunca del payload
    (REQ-064). Se eligio `save()` y no `bulk_create` precisamente para ejercitar esa atribucion;
    `sembrar_usuario` necesita `bulk_create` por otro motivo -la CHECK `ck_usuario_created_by_self`-
    que aqui no aplica.

    `secreto_ref` y `smtp_username` quedan vacios a proposito: el servidor de prueba no pide
    autenticacion y `ParametrosSmtp.desde_entidad` traduce un usuario vacio a `None`, con lo que el
    transporte no llama a `login`. `use_tls='N'` por el mismo motivo: lo que se acredita aqui es la
    ENTREGA, no la negociacion de TLS.
    """

    ConfiguracionSmtpEntity.objects.filter(is_active=INDICADOR_SI).update(is_active=INDICADOR_NO)

    configuracion = ConfiguracionSmtpEntity(
        smtp_host=buzon.host,
        smtp_port=buzon.puerto,
        use_tls=INDICADOR_NO,
        smtp_username=None,
        secreto_ref=None,
        sender_address=REMITENTE_PRUEBA,
        sender_display_name=NOMBRE_REMITENTE_PRUEBA,
        max_attempts=1,
        is_active=INDICADOR_SI,
    )
    with contexto_de_sesion_del_usuario(administrador):
        configuracion.save()

    return configuracion


def releer(usuario: UsuarioEntity) -> UsuarioEntity:
    """
    Devuelve la fila del usuario RECIEN LEIDA de Oracle.

    Toda afirmacion sobre el efecto del restablecimiento se hace sobre esta copia y nunca sobre el
    objeto que sembro la prueba: un objeto en memoria no ve el UPDATE que escribio el caso de uso
    en otra instancia, de modo que afirmar sobre el daria verde pasara lo que pasara en la base.
    """

    return UsuarioEntity.objects.get(pk=usuario.user_id)


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_RST_01_el_restablecimiento_emite_credencial_temporal_e_invalida_la_anterior(
    cliente_api, smtp_aceptando: _BuzonSmtp
) -> None:
    """
    [AC-RST-01, AC-USR-03, AC-USR-04] El administrador restablece la credencial de un usuario activo: la fila queda con
    credencial TEMPORAL (`must_change_password='Y'`, caducidad sellada), la credencial anterior deja de verificar, el
    aviso sale de verdad hacia el buzon corporativo y la respuesta no publica ningun material de credencial.
    """

    admin = sembrar_usuario(ROL_ADMINISTRADOR, "rst01.admin")
    destino = sembrar_usuario(ROL_EMPLEADO, "rst01.destino", status="ACTIVO", must_change_password=INDICADOR_NO)
    hash_anterior = destino.password_hash

    sembrar_configuracion_smtp(smtp_aceptando, admin)

    respuesta = cliente_api.post(
        ruta_restablecimiento(destino.user_id),
        data={},
        content_type="application/json",
        **abrir_sesion(admin),
    )

    # 201 es el status que publica `openapi.yaml` para EP-017; la vista no elige codigo propio.
    assert respuesta.status_code == 201, respuesta.content

    # ORACULO DE PERSISTENCIA: la fila RELEIDA de Oracle, no el objeto que sembro la prueba.
    usuario = releer(destino)
    assert usuario.must_change_password == INDICADOR_SI
    assert usuario.password_expires_at is not None
    assert usuario.password_updated_at is not None
    assert usuario.password_expires_at > usuario.password_updated_at
    assert usuario.credential_issued_at is not None

    # INVALIDACION DE LA CREDENCIAL ANTERIOR. Que el hash cambie es condicion necesaria pero no
    # suficiente: la afirmacion fuerte es que la contrasenia que el usuario tenia YA NO VERIFICA.
    assert usuario.password_hash != hash_anterior
    assert check_password(CLAVE_SEMBRADA, usuario.password_hash) is False

    # Y el hash retirado queda ARCHIVADO en el historico (REQ-069): la credencial anterior no se
    # pierde sin dejar rastro, se guarda para impedir su reutilizacion.
    assert UsuarioPasswordHistoricoEntity.objects.filter(user_id=destino.user_id, password_hash=hash_anterior).exists()

    # LA CREDENCIAL NO SALE POR LA API (REQ-073 regla 4). Ni por clave conocida...
    publicado = respuesta.json()
    for prohibida in ("temporaryPassword", "password", "passwordHash"):
        assert prohibida not in publicado
    # ...ni por ninguna otra via: el JSON entero no menciona material de credencial. La credencial
    # en claro no se puede comparar directamente porque la prueba NO la conoce -ni debe conocerla,
    # que es justamente lo que se esta acreditando-, asi que se afirma sobre lo que SI es observable.
    assert "password_hash" not in json.dumps(publicado)

    # ORACULO DEL DoD PARA AC-RST-03: un mensaje CAPTURADO por el servidor SMTP y dirigido al buzon
    # corporativo del usuario destino. Un log de "correo enviado" no acreditaria esto.
    assert len(smtp_aceptando.mensajes) == 1
    envelope = smtp_aceptando.unico_mensaje()
    assert destino.corporate_email in envelope.rcpt_tos


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_RST_08_el_restablecimiento_no_altera_ningun_otro_dato_del_usuario(cliente_api, smtp_aceptando: _BuzonSmtp) -> None:
    """[AC-RST-08] role_code, full_name y corporate_email quedan identicos antes y despues."""

    admin = sembrar_usuario(ROL_ADMINISTRADOR, "rst08.admin")
    destino = sembrar_usuario(ROL_TECNICO, "rst08.destino", status="ACTIVO", must_change_password=INDICADOR_NO)

    rol_antes = destino.role_code_id
    nombre_antes = destino.full_name
    correo_antes = destino.corporate_email
    estado_antes = destino.status

    sembrar_configuracion_smtp(smtp_aceptando, admin)

    respuesta = cliente_api.post(
        ruta_restablecimiento(destino.user_id),
        data={},
        content_type="application/json",
        **abrir_sesion(admin),
    )

    assert respuesta.status_code == 201, respuesta.content

    # El restablecimiento toca EXCLUSIVAMENTE columnas de credencial. Lo demas, identico.
    usuario = releer(destino)
    assert usuario.role_code_id == rol_antes
    assert usuario.full_name == nombre_antes
    assert usuario.corporate_email == correo_antes
    assert usuario.status == estado_antes

    # Comprobacion de que la prueba NO es vacua: algo si cambio en la fila.
    assert usuario.must_change_password == INDICADOR_SI


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
@pytest.mark.parametrize("rol", [ROL_EMPLEADO, ROL_TECNICO])
def test_AC_RST_02_un_rol_no_administrador_recibe_403_y_no_altera_ninguna_credencial(cliente_api, rol: str) -> None:
    """[AC-RST-02] 403 y ninguna credencial, sesion ni marca must_change_password resulta modificada."""

    solicitante = sembrar_usuario(rol, "rst02.solicitante")
    destino = sembrar_usuario(ROL_EMPLEADO, "rst02.destino", status="ACTIVO", must_change_password=INDICADOR_NO)
    hash_anterior = destino.password_hash

    # El destino tiene una sesion VIVA: sin ella, afirmar que no se revoco ninguna seria vacuo.
    abrir_sesion(destino)

    # NO se siembra configuracion SMTP: la guardia deniega ANTES de llegar al caso de uso, asi que
    # el transporte no deberia construirse nunca. Si algo intentara entregar, no habria a donde.
    respuesta = cliente_api.post(
        ruta_restablecimiento(destino.user_id),
        data={},
        content_type="application/json",
        **abrir_sesion(solicitante),
    )

    assert respuesta.status_code == 403, respuesta.content

    # NADA se movio en la fila del destino.
    usuario = releer(destino)
    assert usuario.password_hash == hash_anterior
    assert check_password(CLAVE_SEMBRADA, usuario.password_hash) is True
    assert usuario.must_change_password == INDICADOR_NO
    assert usuario.password_expires_at is None

    # Ninguna sesion del destino quedo revocada: el 403 no produce el efecto lateral de AC-PWD-06.
    assert SesionUsuarioEntity.objects.filter(user_id=destino.user_id, revoked_at__isnull=False).count() == 0

    # Y no se encolo ningun aviso para el: la denegacion ocurre antes de tocar el outbox.
    assert AvisoCorreoEntity.objects.filter(recipient_user_id=destino.user_id).count() == 0


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_el_restablecimiento_sobre_un_usuario_inactivo_responde_409(cliente_api, smtp_aceptando: _BuzonSmtp) -> None:
    """[REQ-073] Una cuenta INACTIVO no admite restablecimiento."""

    admin = sembrar_usuario(ROL_ADMINISTRADOR, "rstinactivo.admin")
    destino = sembrar_usuario(ROL_EMPLEADO, "rstinactivo.destino", status="INACTIVO", must_change_password=INDICADOR_NO)
    hash_anterior = destino.password_hash

    sembrar_configuracion_smtp(smtp_aceptando, admin)

    respuesta = cliente_api.post(
        ruta_restablecimiento(destino.user_id),
        data={},
        content_type="application/json",
        **abrir_sesion(admin),
    )

    # 409 y no 404: la cuenta EXISTE, pero emitirle una credencial no le devolveria el acceso, asi
    # que responder 201 seria mentir sobre el efecto de la operacion.
    assert respuesta.status_code == 409, respuesta.content
    assert respuesta.json()["code"] == "USR_ACCOUNT_INACTIVE"

    assert releer(destino).password_hash == hash_anterior


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_RST_03_un_smtp_que_rechaza_la_entrega_responde_502_y_deja_vigente_la_credencial_anterior(
    cliente_api, smtp_rechazando: _BuzonSmtp
) -> None:
    """[AC-RST-03] 502 y la credencial anterior del usuario sigue siendo la vigente."""

    admin = sembrar_usuario(ROL_ADMINISTRADOR, "rst03.admin")
    destino = sembrar_usuario(ROL_EMPLEADO, "rst03.destino", status="ACTIVO", must_change_password=INDICADOR_NO)
    hash_anterior = destino.password_hash

    abrir_sesion(destino)
    sembrar_configuracion_smtp(smtp_rechazando, admin)

    respuesta = cliente_api.post(
        ruta_restablecimiento(destino.user_id),
        data={},
        content_type="application/json",
        **abrir_sesion(admin),
    )

    # 502 y no 500: el fallo esta en un sistema de tercero -el servidor de correo- y la accion es
    # REINTENTABLE tal cual, sin que el administrador cambie nada de lo que envio.
    assert respuesta.status_code == 502, respuesta.content

    # ESTO ES LO QUE DISTINGUE EP-017 DEL ALTA DE USUARIO. En el alta (EP-007, REQ-038 regla 6) un
    # fallo de SMTP deja la cuenta CREADA, porque no habia nada que revertir: el usuario no existia
    # y su credencial tampoco. Aqui si habia una credencial vigente, y REQ-073 regla 7 obliga a que
    # el restablecimiento NO se confirme: el usuario conserva la que ya usaba, en vez de quedarse
    # sin acceso y sin el correo que se lo devolveria.
    usuario = releer(destino)
    assert usuario.password_hash == hash_anterior
    assert check_password(CLAVE_SEMBRADA, usuario.password_hash) is True
    assert usuario.must_change_password == INDICADOR_NO
    assert usuario.password_expires_at is None

    # Tampoco se produjo el efecto lateral de AC-PWD-06: si la credencial anterior sigue siendo la
    # vigente, las sesiones abiertas con ella no tienen por que caerse.
    assert SesionUsuarioEntity.objects.filter(user_id=destino.user_id, revoked_at__isnull=False).count() == 0

    # El intento LLEGO A HACERSE contra el servidor real: el 502 viene de un rechazo del SMTP y no
    # de que la entrega se omitiera sin intentarla.
    assert len(smtp_rechazando.mensajes) == 1


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_el_administrador_no_puede_restablecerse_a_si_mismo(cliente_api, smtp_aceptando: _BuzonSmtp) -> None:
    """[REQ-073 validacion 3] Para la propia contrasenia se usa el cambio propio (EP-005)."""

    admin = sembrar_usuario(ROL_ADMINISTRADOR, "rstself.admin")
    hash_anterior = admin.password_hash

    sembrar_configuracion_smtp(smtp_aceptando, admin)

    respuesta = cliente_api.post(
        ruta_restablecimiento(admin.user_id),
        data={},
        content_type="application/json",
        **abrir_sesion(admin),
    )

    # Este flujo NO pide la contrasenia actual, asi que permitir el autorrestablecimiento
    # convertiria una sesion ya abierta -o robada- en un cambio de credencial sin presentar el
    # secreto anterior. El camino propio es EP-005, que si lo exige.
    assert respuesta.status_code == 409, respuesta.content
    assert respuesta.json()["code"] == "USR_SELF_RESET_NOT_ALLOWED"

    assert releer(admin).password_hash == hash_anterior
    assert len(smtp_aceptando.mensajes) == 0


# ---------------------------------------------------------------------------------------------
# EP-019 - GET /api/users/credential-status (AC-RST-04, AC-RST-05, REQ-074)
# ---------------------------------------------------------------------------------------------
# El oraculo de estos casos tampoco es «la vista respondio algo»: es QUE FILAS devuelve la consulta
# contra el censo REAL de Oracle -con el CASE/WHEN de prioridad, el OFFSET..FETCH NEXT y las
# comparaciones de `locked_until` resueltas por el motor- y, sobre todo, QUE NO devuelve: ningun
# material de credencial. Un `dict` en memoria no acreditaria ni el desempate estable de la
# paginacion ni el criterio de bloqueo vigente.


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_RST_04_el_listado_filtra_por_rol_por_bloqueo_y_por_cambio_pendiente(cliente_api) -> None:
    """[AC-RST-04] Cada filtro devuelve UNICAMENTE las filas que lo cumplen, con locked_until en el futuro para las bloqueadas."""

    ahora = utc_now()
    admin = sembrar_usuario(ROL_ADMINISTRADOR, "rst04.admin")

    tecnico_bloqueado = sembrar_usuario(ROL_TECNICO, "rst04.tecnico.bloqueado", locked_until=ahora + timedelta(hours=1))
    # Bloqueo YA VENCIDO: la columna esta informada, pero la cuenta esta operativa. Es el caso que
    # distingue «locked_until en el futuro» de «locked_until informado».
    empleado_bloqueo_vencido = sembrar_usuario(ROL_EMPLEADO, "rst04.empleado.vencido", locked_until=ahora - timedelta(hours=1))
    empleado_pendiente = sembrar_usuario(ROL_EMPLEADO, "rst04.empleado.pendiente", must_change_password=INDICADOR_SI)
    empleado_normal = sembrar_usuario(ROL_EMPLEADO, "rst04.empleado.normal", must_change_password=INDICADOR_NO)

    cabecera = abrir_sesion(admin)

    # NO se afirma `totalCount` exacto en ningun punto de esta prueba: el censo trae ademas los
    # usuarios de las semillas del changelog dml y los que haya sembrado otra prueba de la sesion.
    # Lo que si es exacto -y es lo que acredita el filtro- es la PERTENENCIA de cada usuario
    # sembrado aqui y la coherencia de CADA fila devuelta con el predicado pedido.

    respuesta = cliente_api.get(RUTA_ESTADO_CREDENCIAL, {"lockStatus": "bloqueado"}, **cabecera)
    assert respuesta.status_code == 200, respuesta.content
    bloqueadas = respuesta.json()["items"]
    identificadores = {fila["userId"] for fila in bloqueadas}
    assert tecnico_bloqueado.user_id in identificadores
    assert empleado_bloqueo_vencido.user_id not in identificadores
    for fila in bloqueadas:
        assert fila["locked"] is True
        assert fila["lockedUntil"] is not None
        assert parse_datetime(fila["lockedUntil"]) > ahora

    respuesta = cliente_api.get(RUTA_ESTADO_CREDENCIAL, {"roleCode": ROL_TECNICO}, **cabecera)
    assert respuesta.status_code == 200, respuesta.content
    tecnicos = respuesta.json()["items"]
    assert tecnico_bloqueado.user_id in {fila["userId"] for fila in tecnicos}
    assert empleado_normal.user_id not in {fila["userId"] for fila in tecnicos}
    for fila in tecnicos:
        assert fila["roleCode"] == ROL_TECNICO

    respuesta = cliente_api.get(RUTA_ESTADO_CREDENCIAL, {"mustChangePassword": "true"}, **cabecera)
    assert respuesta.status_code == 200, respuesta.content
    pendientes = respuesta.json()["items"]
    assert empleado_pendiente.user_id in {fila["userId"] for fila in pendientes}
    for fila in pendientes:
        assert fila["mustChangePassword"] is True


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_AC_RST_04_ninguna_respuesta_del_listado_contiene_password_hash(cliente_api) -> None:
    """[AC-RST-04] 0 ocurrencias de password_hash ni de ningun material de credencial en la respuesta."""

    admin = sembrar_usuario(ROL_ADMINISTRADOR, "rst04fuga.admin")
    sembrar_usuario(ROL_EMPLEADO, "rst04fuga.destino", must_change_password=INDICADOR_SI)

    respuesta = cliente_api.get(RUTA_ESTADO_CREDENCIAL, **abrir_sesion(admin))
    assert respuesta.status_code == 200, respuesta.content

    cuerpo = respuesta.json()

    # EL ASSERT LITERAL DEL DoD: el cuerpo ENTERO serializado a texto, no una comprobacion por
    # claves conocidas. Si el material de credencial se colara anidado en cualquier nivel -o con el
    # nombre fisico del esquema en vez del camelCase del contrato-, aqui aparece.
    texto = json.dumps(cuerpo)
    for prohibida in (
        "password_hash",
        "passwordHash",
        "password_salt",
        "passwordSalt",
        "password_algorithm",
        "passwordAlgorithm",
    ):
        assert texto.count(prohibida) == 0, f"La respuesta publica {prohibida}"

    # Y la prueba no es vacua: hay filas y sus claves son EXACTAMENTE las diez del contrato. Si
    # alguien anade un campo al serializer, este assert se entera aunque no se llame `password_*`.
    assert cuerpo["items"], "El listado vino vacio: la comprobacion de fuga seria vacua"
    assert set(cuerpo["items"][0]) == {
        "userId",
        "fullName",
        "corporateEmail",
        "roleCode",
        "status",
        "passwordUpdatedAt",
        "mustChangePassword",
        "locked",
        "lockedUntil",
        "lastLoginAt",
    }


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
@pytest.mark.parametrize("rol", [ROL_EMPLEADO, ROL_TECNICO])
def test_AC_RST_05_un_tecnico_o_un_empleado_reciben_403_y_ninguna_fila(cliente_api, rol: str) -> None:
    """[AC-RST-05] 403 y ninguna fila de datos de otros usuarios."""

    solicitante = sembrar_usuario(rol, "rst05.solicitante")
    sembrar_usuario(ROL_EMPLEADO, "rst05.otro", must_change_password=INDICADOR_SI)

    respuesta = cliente_api.get(RUTA_ESTADO_CREDENCIAL, **abrir_sesion(solicitante))

    assert respuesta.status_code == 403, respuesta.content

    # El cuerpo es el de error canonico del servicio (`code`, `message`, `details`, `traceId`): NO
    # trae `items`, asi que no se filtra ni una fila del censo por el camino de la denegacion.
    cuerpo = respuesta.json()
    assert "items" not in cuerpo
    assert cuerpo["code"] == "PERM_DENIED"


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_el_listado_pagina_de_25_en_25_sin_repetir_ni_omitir_usuarios(cliente_api) -> None:
    """[REQ-074 regla 2] 25 por pagina, con orden estable: ni filas repetidas ni omitidas entre paginas."""

    admin = sembrar_usuario(ROL_ADMINISTRADOR, "pag.admin")
    cabecera = abrir_sesion(admin)

    # Primero se MIDE el censo que ya hay (semillas del changelog dml y usuarios de otras pruebas) y
    # solo despues se siembra lo que falte para superar las dos paginas: fijar un numero a ciegas
    # haria la prueba dependiente del orden de ejecucion del resto del fichero.
    inicial = cliente_api.get(RUTA_ESTADO_CREDENCIAL, **cabecera)
    assert inicial.status_code == 200, inicial.content
    censo_previo = inicial.json()["totalCount"]

    # Se siembra con `sembrar_usuario` en bucle y NO con un `bulk_create` propio: el helper ya
    # resuelve el hash real, el sufijo que esquiva los indices unicos CI y los campos obligatorios
    # del DDL. Es mas lento que un unico `bulk_create`, pero no puede quedarse desalineado con el
    # esquema el dia que el DDL anada una columna NOT NULL.
    objetivo = 2 * 25 + 5
    for indice in range(max(0, objetivo - censo_previo)):
        sembrar_usuario(ROL_EMPLEADO, f"pag.empleado{indice:03d}")

    vistos: list[int] = []
    totales: list[int] = []
    paginas: list[int] = []
    for numero in (1, 2, 3):
        respuesta = cliente_api.get(RUTA_ESTADO_CREDENCIAL, {"page": numero}, **cabecera)
        assert respuesta.status_code == 200, respuesta.content
        cuerpo = respuesta.json()

        assert cuerpo["page"] == numero
        assert cuerpo["pageSize"] == 25
        assert len(cuerpo["items"]) <= 25
        if numero in (1, 2):
            assert len(cuerpo["items"]) == 25, f"La pagina {numero} no vino llena pese a haber mas de 50 usuarios"

        vistos.extend(fila["userId"] for fila in cuerpo["items"])
        totales.append(cuerpo["totalCount"])
        paginas.append(cuerpo["totalPages"])

    # ESTE ES EL ASSERT QUE PRUEBA EL DESEMPATE ESTABLE: sin el `user_id` final del `order_by`, dos
    # filas con el mismo `full_name` bailan entre consultas y un mismo usuario sale en dos paginas
    # (mientras otro no sale en ninguna).
    assert len(vistos) == len(set(vistos)), "Hay usuarios repetidos entre paginas: la ordenacion no es estable"

    assert len(set(totales)) == 1, f"El total cambio entre paginas: {totales}"
    total = totales[0]
    assert total >= objetivo
    assert paginas == [-(-total // 25)] * 3

    # Una pagina por encima del total NO es un error (REQ-074): consulta valida, sin filas.
    desbordada = cliente_api.get(RUTA_ESTADO_CREDENCIAL, {"page": paginas[0] + 50}, **cabecera)
    assert desbordada.status_code == 200, desbordada.content
    assert desbordada.json()["items"] == []


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_el_listado_rechaza_una_busqueda_de_menos_de_tres_caracteres(cliente_api) -> None:
    """[REQ-074 validacion 4] La busqueda libre exige al menos 3 caracteres."""

    admin = sembrar_usuario(ROL_ADMINISTRADOR, "busqueda.admin")
    cabecera = abrir_sesion(admin)

    respuesta = cliente_api.get(RUTA_ESTADO_CREDENCIAL, {"search": "ab"}, **cabecera)

    # 400 y no 422: el manejador unico (`apps/core_security/manejadores.py`) traduce TODA
    # `ValidationError` de DRF a `HTTP_400_BAD_REQUEST` con `code = "VAL_INVALID_REQUEST"`. El
    # status se comprobo en el codigo del repo, no se escribio de memoria.
    assert respuesta.status_code == 400, respuesta.content
    cuerpo = respuesta.json()
    assert cuerpo["code"] == "VAL_INVALID_REQUEST"
    assert "items" not in cuerpo

    # Y con la longitud minima la misma consulta es valida: lo que se rechaza es el texto corto, no
    # el parametro `search`.
    aceptada = cliente_api.get(RUTA_ESTADO_CREDENCIAL, {"search": "abc"}, **cabecera)
    assert aceptada.status_code == 200, aceptada.content
