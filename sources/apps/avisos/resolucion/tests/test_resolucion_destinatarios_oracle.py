"""
Pruebas de integracion de la resolucion de destinatarios (REQ-089, REQ-140).

Se ejecutan contra Oracle Database 23ai Free REAL, levantado con Testcontainers a
partir de la imagen oficial del proyecto (`gvenzl/oracle-free:23-slim`), con el
esquema aplicado por Liquibase desde el changelog real del repositorio.

PROHIBIDO sustituir el motor por H2, SQLite o cualquier doble en memoria: un `dict`
en memoria NO es evidencia de persistencia sobre `resolucion_destinatario_log`, y un
test verde contra un sustituto del motor no acredita que la traza de resolucion quede
escrita en la tabla que gobierna el DDL. Si no hay engine Docker alcanzable, estas
pruebas se SALTAN (`SALTAR_SIN_DOCKER`); nunca se degradan a otro backend.
"""

from __future__ import annotations

import json

import pytest

from apps.avisos.resolucion import ConsultaNoAutorizadaError, ResultadoResolucion, ServicioResolucionDestinatarios
from apps.avisos.resolucion.resultados import MotivoNoNotificable
from apps.core.contexto import ContextoSesion, contexto_de_sesion, utc_now
from apps.core.models import MotivoDesactivacionEntity, ResolucionDestinatarioLogEntity, UsuarioEntity
from apps.core.tests.test_persistencia_oracle import SALTAR_SIN_DOCKER  # noqa: F401

pytestmark = [pytest.mark.integration]


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_REQ_089_dos_tecnicos_activos_y_uno_inactivo_resuelven_exactamente_dos_direcciones(  # noqa: N802
    crear_usuario,  # noqa: ANN001
    servicio_resolucion: ServicioResolucionDestinatarios,
) -> None:
    """[REQ-089] Dos tecnicos activos y uno inactivo: la resolucion devuelve exactamente 2 direcciones, sin duplicados."""

    # El ORACULO es el dato LEIDO del Oracle real: el censo se siembra en la base de la fixture y la
    # traza se reelee de `resolucion_destinatario_log` por su PK, nunca de una estructura en memoria.
    activo_uno = crear_usuario(
        nombre="Tecnico Activo Uno",
        correo="tecnico.activo.uno@mind.local",
        role_code="TECNICO_MANTENIMIENTO",
        activo=True,
    )
    activo_dos = crear_usuario(
        nombre="Tecnico Activo Dos",
        correo="tecnico.activo.dos@mind.local",
        role_code="TECNICO_MANTENIMIENTO",
        activo=True,
    )
    inactivo = crear_usuario(
        nombre="Tecnico Inactivo",
        correo="tecnico.inactivo@mind.local",
        role_code="TECNICO_MANTENIMIENTO",
        activo=False,
    )
    # Empleado ACTIVO ajeno al colectivo: comprueba que el equipo se acota por ROL y que la
    # resolucion no devuelve a todo el censo.
    empleado = crear_usuario(
        nombre="Empleado Ajeno",
        correo="empleado.ajeno@mind.local",
        role_code="EMPLEADO",
        activo=True,
    )

    resolucion = servicio_resolucion.resolver_equipo_mantenimiento()

    esperadas = {activo_uno.corporate_email, activo_dos.corporate_email}
    assert len(resolucion.direcciones) == 2, f"se esperaban 2 direcciones (los tecnicos activos), se obtuvieron {len(resolucion.direcciones)}"
    assert set(resolucion.direcciones) == esperadas, f"las direcciones resueltas no son las de los dos tecnicos activos: {resolucion.direcciones}"
    assert len(set(resolucion.direcciones)) == 2, f"la resolucion ha devuelto direcciones duplicadas: {resolucion.direcciones}"

    assert inactivo.corporate_email not in resolucion.direcciones, "el tecnico INACTIVO no puede figurar como destinatario del aviso"
    assert inactivo.user_id not in resolucion.user_ids, "el user_id del tecnico INACTIVO no puede figurar entre los destinatarios resueltos"
    assert empleado.corporate_email not in resolucion.direcciones, "el colectivo se acota por ROL: un EMPLEADO no es del equipo de mantenimiento"

    assert resolucion.outcome is ResultadoResolucion.OK, f"con destinatarios notificables el desenlace debe ser OK, fue {resolucion.outcome}"
    assert resolucion.debe_enviarse is True, "con 2 destinatarios resueltos la resolucion debe originar el envio del aviso"
    assert resolucion.degradado is False, "el directorio ha respondido: la resolucion no puede marcarse como degradada"

    # PERSISTENCIA REAL: la fila del log se relee DESDE LA BASE por su clave primaria.
    assert resolucion.resolution_id is not None, "la resolucion debe dejar asiento en resolucion_destinatario_log y devolver su resolution_id"
    fila = ResolucionDestinatarioLogEntity.objects.get(pk=resolucion.resolution_id)
    assert fila.request_type == "COLECTIVO", f"el asiento de un colectivo debe registrarse como COLECTIVO, se registro {fila.request_type}"
    assert fila.recipient_count == 2, f"recipient_count debe reflejar los 2 destinatarios resueltos, fue {fila.recipient_count}"
    assert fila.outcome == "OK", f"el outcome persistido debe ser OK, fue {fila.outcome}"
    assert fila.subject_user_id is None, "una resolucion de COLECTIVO no tiene sujeto individual: subject_user_id debe quedar nulo"
    assert fila.is_fallback_used == "N", "no hay buzon de respaldo en esta resolucion: is_fallback_used debe quedar a N"
    assert fila.resolved_at is not None, "el asiento debe quedar sellado con el instante de la resolucion"

    identificadores = json.loads(fila.resolved_user_ids)
    assert identificadores == [activo_uno.user_id, activo_dos.user_id], (
        f"resolved_user_ids debe contener SOLO los identificadores de los dos tecnicos activos, contiene {identificadores}"
    )
    assert all(isinstance(identificador, int) for identificador in identificadores), (
        f"resolved_user_ids solo admite numeros (CHECK ck_res_dest_solo_identificadores), jamas correos en claro: {identificadores}"
    )


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_REQ_140_el_reportante_desactivado_no_es_notificable_con_motivo_recipient_inactive(  # noqa: N802
    crear_usuario,  # noqa: ANN001
    semilla_administrador: UsuarioEntity,
    servicio_resolucion: ServicioResolucionDestinatarios,
) -> None:
    """[REQ-140] Reportante desactivado: no notificable, motivo RECIPIENT_INACTIVE y supresion USUARIO_DESACTIVADO.

    El cambio de estado de la incidencia NO se bloquea por esto: la resolucion INFORMA de que no hay
    a quien avisar, no aborta la operacion de negocio.
    """

    # El ORACULO es el dato LEIDO del Oracle real: el reportante se siembra con una baja logica
    # coherente (la CHECK `ck_usuario_coherencia_baja` la impone la base, no la aplicacion) y la traza
    # se reelee de `resolucion_destinatario_log` por su PK, nunca de una estructura en memoria.
    reportante = crear_usuario(
        nombre="Empleado Reportante De Baja",
        correo="empleado.reportante.baja@mind.local",
        role_code="EMPLEADO",
        activo=False,
    )

    notificabilidad = servicio_resolucion.resolver_notificabilidad_reportante(reportante.user_id)

    assert notificabilidad.es_notificable is False, "un reportante DESACTIVADO no puede ser notificable"
    assert notificabilidad.motivo is MotivoNoNotificable.RECIPIENT_INACTIVE, (
        f"el motivo funcional de una cuenta de baja debe ser RECIPIENT_INACTIVE, fue {notificabilidad.motivo}"
    )
    assert notificabilidad.suppression_reason_code == "USUARIO_DESACTIVADO", (
        f"el codigo del catalogo CERRADO aviso_correo.suppression_reason_code debe ser USUARIO_DESACTIVADO, "
        f"fue {notificabilidad.suppression_reason_code}"
    )
    assert notificabilidad.outcome is ResultadoResolucion.NO_NOTIFICABLE, (
        f"el desenlace de un destinatario no notificable debe ser NO_NOTIFICABLE, fue {notificabilidad.outcome}"
    )

    # REQ-140: no se sustituye el destinatario por NINGUN otro buzon. Si el reportante no es
    # notificable no hay ficha ni direccion alternativa a la que redirigir el aviso.
    assert notificabilidad.destinatario is None, "no hay destinatario que devolver: el aviso no se redirige a ningun otro buzon"
    assert notificabilidad.direccion is None, f"no puede resolverse direccion alguna para un reportante de baja: {notificabilidad.direccion}"
    assert notificabilidad.degradado is False, "el directorio ha respondido: la resolucion no puede marcarse como degradada"

    # PERSISTENCIA REAL: la fila del log se relee DESDE LA BASE por su clave primaria.
    assert notificabilidad.resolution_id is not None, "la resolucion debe dejar asiento en resolucion_destinatario_log y devolver su resolution_id"
    fila = ResolucionDestinatarioLogEntity.objects.get(pk=notificabilidad.resolution_id)
    assert fila.request_type == "INDIVIDUAL", f"el asiento de un reportante concreto debe registrarse como INDIVIDUAL, se registro {fila.request_type}"
    assert fila.subject_user_id == reportante.user_id, f"subject_user_id debe identificar al reportante consultado, fue {fila.subject_user_id}"
    assert fila.recipient_count == 0, f"no se ha resuelto ningun destinatario: recipient_count debe ser 0, fue {fila.recipient_count}"
    assert fila.outcome == "NO_NOTIFICABLE", f"el outcome persistido debe ser NO_NOTIFICABLE, fue {fila.outcome}"
    assert fila.is_fallback_used == "N", "no hay buzon de respaldo en esta resolucion: is_fallback_used debe quedar a N (REQ-140, RN-02)"
    assert json.loads(fila.resolved_user_ids) == [], f"sin destinatarios notificables resolved_user_ids debe quedar vacio, contiene {fila.resolved_user_ids}"

    # --- SIN CACHE: la decision se toma con el dato VIGENTE (REQ-089 RN-03, REQ-140) -----------
    # Un segundo empleado se resuelve ACTIVO, se le da de baja POR EL CAMINO REAL del proyecto
    # (escritura en la base) y se vuelve a resolver: la segunda resolucion debe ver ya la baja.
    vigente = crear_usuario(
        nombre="Empleado Reportante Vigente",
        correo="empleado.reportante.vigente@mind.local",
        role_code="EMPLEADO",
        activo=True,
    )

    antes = servicio_resolucion.resolver_notificabilidad_reportante(vigente.user_id)
    assert antes.es_notificable is True, "mientras la cuenta esta ACTIVA el reportante si es notificable"
    assert antes.outcome is ResultadoResolucion.OK, f"con un reportante activo y con correo el desenlace debe ser OK, fue {antes.outcome}"
    assert antes.direccion == vigente.corporate_email, f"la direccion resuelta debe ser el correo corporativo vigente, fue {antes.direccion}"

    motivo_baja = MotivoDesactivacionEntity.objects.filter(is_active="Y").first()
    assert motivo_baja is not None, "cat_motivo_desactivacion no tiene ningun motivo activo sembrado"
    # La baja se escribe DIRECTAMENTE sobre la entidad, igual que hace la fabrica `crear_usuario`.
    # NO se usa `RepositorioUsuario.desactivar`: ese metodo asigna escalares (un `int` a
    # `deactivated_by` y un `str` a `deactivation_reason_code`) a campos que son ForeignKey, de modo
    # que Django lanza `ValueError` en ejecucion. El defecto es del nucleo
    # (`apps/core/repositorios.py`), esta fuera del alcance de esta tarea, ya queda reportado y no se
    # corrige desde aqui: lo que esta prueba tiene que ejercitar es la ausencia de cache, no el repositorio.
    vigente.status = "INACTIVO"
    vigente.deactivated_at = utc_now()
    # `deactivated_by` es FK a "self": se asigna la INSTANCIA del administrador, nunca su identificador.
    vigente.deactivated_by = semilla_administrador
    # `deactivation_reason_code` es FK al catalogo: se asigna la INSTANCIA del motivo, nunca su codigo.
    vigente.deactivation_reason_code = motivo_baja
    vigente.save()

    despues = servicio_resolucion.resolver_notificabilidad_reportante(vigente.user_id)
    assert despues.es_notificable is False, (
        "tras la baja logica la resolucion debe leer el estado vigente de la base, nunca un valor cacheado"
    )
    assert despues.motivo is MotivoNoNotificable.RECIPIENT_INACTIVE, (
        f"la segunda resolucion debe motivarse como RECIPIENT_INACTIVE, fue {despues.motivo}"
    )


@SALTAR_SIN_DOCKER
@pytest.mark.django_db
def test_REQ_089_el_colectivo_vacio_no_origina_envio_y_queda_en_auditoria_para_administrador(  # noqa: N802
    crear_usuario,  # noqa: ANN001
    servicio_resolucion: ServicioResolucionDestinatarios,
    sesion_administrador: ContextoSesion,  # noqa: ARG001
) -> None:
    """[REQ-089] Sin tecnicos activos: no se origina envio, queda registrado y solo el ADMINISTRADOR puede consultarlo."""

    # El censo NO esta vacio: hay un tecnico DE BAJA y un empleado ACTIVO. Asi el resultado vacio es
    # una decision del filtro (rol + estado) y no el efecto de una base sin usuarios, que no probaria nada.
    tecnico_de_baja = crear_usuario(
        nombre="Tecnico De Baja",
        correo="tecnico.de.baja@mind.local",
        role_code="TECNICO_MANTENIMIENTO",
        activo=False,
    )
    empleado = crear_usuario(
        nombre="Empleado Activo Ajeno",
        correo="empleado.activo.ajeno@mind.local",
        role_code="EMPLEADO",
        activo=True,
    )

    resolucion = servicio_resolucion.resolver_equipo_mantenimiento()

    assert resolucion.direcciones == (), f"sin tecnicos activos no puede resolverse direccion alguna, se obtuvo {resolucion.direcciones}"
    assert resolucion.hay_destinatarios is False, "sin tecnicos activos la resolucion no tiene destinatarios"
    assert resolucion.debe_enviarse is False, "con el colectivo vacio NO se origina ningun envio de aviso (REQ-089)"
    assert resolucion.outcome is ResultadoResolucion.SIN_DESTINATARIOS, (
        f"el desenlace de un colectivo vacio debe ser SIN_DESTINATARIOS, fue {resolucion.outcome}"
    )
    assert resolucion.degradado is False, "el directorio ha respondido con una lista vacia: eso no es una degradacion"

    # PERSISTENCIA REAL: la fila del log se relee DESDE LA BASE por su clave primaria.
    assert resolucion.resolution_id is not None, "aun sin destinatarios la resolucion debe dejar asiento y devolver su resolution_id"
    fila = ResolucionDestinatarioLogEntity.objects.get(pk=resolucion.resolution_id)
    assert fila.request_type == "COLECTIVO", f"el asiento de un colectivo debe registrarse como COLECTIVO, se registro {fila.request_type}"
    assert fila.recipient_count == 0, f"no se ha resuelto ningun destinatario: recipient_count debe ser 0, fue {fila.recipient_count}"
    assert fila.outcome == "SIN_DESTINATARIOS", f"el outcome persistido debe ser SIN_DESTINATARIOS, fue {fila.outcome}"
    assert json.loads(fila.resolved_user_ids) == [], (
        f"sin destinatarios resolved_user_ids debe quedar vacio, contiene {fila.resolved_user_ids}"
    )
    # REQ-140: no se recurre a NINGUN buzon de respaldo; esta prohibido sustituir al destinatario.
    assert fila.is_fallback_used == "N", "no se recurre a ningun buzon de respaldo: is_fallback_used debe quedar a N (REQ-140)"
    assert fila.resolved_at is not None, "el asiento debe quedar sellado con el instante de la resolucion"

    # VISIBLE PARA ADMINISTRADOR: la fixture `sesion_administrador` ya publica su contexto de sesion.
    recientes = servicio_resolucion.consultar_resoluciones(limite=10)
    identificadores_auditados = [registro.resolution_id for registro in recientes]
    assert fila.resolution_id in identificadores_auditados, (
        f"el ADMINISTRADOR debe ver la resolucion {fila.resolution_id} en la auditoria, se listaron {identificadores_auditados}"
    )

    # Y SOLO PARA EL: el mismo historico consultado por un TECNICO_MANTENIMIENTO debe rechazarse.
    contexto_tecnico = ContextoSesion(
        user_id=empleado.user_id,
        role_code="TECNICO_MANTENIMIENTO",
        data_scope="OWN",
        display_name=tecnico_de_baja.full_name,
    )
    with contexto_de_sesion(contexto_tecnico), pytest.raises(ConsultaNoAutorizadaError):
        servicio_resolucion.consultar_resoluciones()
