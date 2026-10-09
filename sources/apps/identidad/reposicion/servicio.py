"""
Restablecimiento administrativo de credencial (EP-017, REQ-073): orquestacion del caso de uso.

Este modulo coordina a tres colaboradores que ya existen y no reimplementa nada de lo suyo: el
generador de credencial temporal (`apps.identidad.reposicion.generador`), la custodia de
credenciales (`ServicioCustodiaCredenciales`, punto UNICO de hashing y politica, REQ-069) y la
entrega del aviso (`ServicioEntregaCredencial`, ARC-014). Lo propio de aqui es el ORDEN en que se
invocan, la revocacion en bloque de las sesiones del usuario (AC-PWD-06) y el asiento de auditoria.

EL ORDEN ES LA REGLA DE NEGOCIO
--------------------------------
REQ-073 regla 7 y AC-RST-03 son explicitos: «un restablecimiento no queda confirmado si falla la
entrega del correo con el acceso temporal» y, ante un SMTP que rechaza la entrega, «el sistema
responde 502 y la credencial anterior del usuario sigue siendo la vigente». Eso INVIERTE el orden
que documenta `ServicioEntregaCredencial` para el alta de usuario (EP-007, REQ-038 regla 6), donde
el usuario queda creado aunque el correo falle porque alli no hay nada que revertir: la cuenta no
existia antes y su credencial tampoco. Aqui si habia una credencial vigente, y es la que el usuario
seguira usando si el correo no sale. Por eso la credencial nueva NO se persiste hasta que la
entrega ha prosperado.

SECRETOS (REQ-063, REQ-076, REQ-079)
------------------------------------
La credencial temporal existe en claro solo dentro de `restablecer`, viaja a la custodia y a la
entrega, y muere con la llamada. NO se devuelve -`ResultadoRestablecimiento` no tiene donde
guardarla-, NO se registra en ninguna traza y NO se interpola en ningun mensaje. Tampoco se loggean
el `corporate_email`, el `full_name`, el `password_hash` ni el `motivo` que escribio el
administrador. El administrador NO VE en ningun momento la contrasena que se le ha emitido al
usuario (AC-RST-01).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from django.db import transaction

from apps.avisos.credenciales.entrega import (
    MENSAJE_502_RESTABLECIMIENTO,
    DatosCredencial,
    ServicioEntregaCredencial,
    servicio_entrega_credencial,
)
from apps.avisos.motor.outbox import FORMATO_DISCRIMINANTE, TIPO_RESTABLECIMIENTO
from apps.core.contexto import ContextoSesion, utc_now
from apps.identidad.credenciales.servicio import ServicioCustodiaCredenciales
from apps.identidad.reposicion import mensajes
from apps.identidad.reposicion.auditoria import registrar_restablecimiento
from apps.identidad.reposicion.errores import (
    AutorrestablecimientoNoPermitidoError,
    CuentaInactivaError,
    EntregaCredencialFallidaError,
)
from apps.identidad.reposicion.generador import caducidad_credencial_temporal, generar_credencial_temporal

if TYPE_CHECKING:  # pragma: no cover - solo para el tipado, evita importar modelos antes de django.setup()
    from apps.core.models import UsuarioEntity


logger = logging.getLogger(__name__)

#: Valor de `usuario.status` que admite el restablecimiento. Una cuenta inactiva (baja logica,
#: REQ-047) no recupera el acceso por recibir una credencial nueva, asi que emitirsela seria
#: mentir sobre el efecto de la operacion; reactivarla es otra operacion distinta.
ESTADO_USUARIO_ACTIVO = "ACTIVO"

#: Valor del enumerado `ck_sesion_usuario_reason_enum` (minusculas) con el que se revocan las
#: sesiones del usuario al reponerle la credencial (AC-PWD-06). No se inventa un motivo nuevo: el
#: DDL cierra el catalogo y el restablecimiento ES un cambio de contrasena desde el punto de vista
#: de la sesion que deja de valer.
MOTIVO_REVOCACION_RESTABLECIMIENTO = "password_change"

#: Tope del motivo que justifica el restablecimiento (REQ-073 regla 6). Vive aqui como contrato
#: publicado del caso de uso; quien lo HACE CUMPLIR con un 422 de campo es el serializer de la
#: vista, no este servicio (ver `restablecer`).
LONGITUD_MAXIMA_MOTIVO = 250

TRAZA_RESTABLECIMIENTO_CONFIRMADO = "Credencial temporal emitida y entregada: restablecimiento confirmado"


@dataclass(frozen=True, slots=True)
class ResultadoRestablecimiento:
    """
    Desenlace publicable de un restablecimiento ya confirmado (REQ-073 regla 4, AC-RST-01).

    ESTA ESTRUCTURA NO TRANSPORTA LA CREDENCIAL TEMPORAL NI SU HASH, Y NO PUEDE HACERLO. No es una
    omision que haya que recordar al rellenarla: `slots=True` cierra la clase a atributos nuevos,
    de modo que un `resultado.password = ...` anadido mas adelante falla en ejecucion en vez de
    colar el secreto hasta el serializador. La contrasena NUNCA se devuelve en la respuesta de la
    API y el administrador no la ve en ningun momento: el unico que la recibe es el usuario
    destino, en su buzon corporativo.

    `frozen=True` por el mismo motivo que el resto de resultados del proyecto: lo que se devuelve
    es la foto de lo que ya ocurrio, y nadie aguas arriba debe poder retocarla.

    Attributes:
        user_id: identificador del usuario DESTINO del restablecimiento.
        must_change_password: siempre `True`; la credencial emitida es temporal y el usuario debera
            cambiarla en su primer acceso (EP-006).
        password_expires_at: instante de caducidad de la credencial temporal, naive en UTC.
        reset_at: instante en que se sello la operacion, naive en UTC.
        reset_by_user_id: identificador del ADMINISTRADOR que la ejecuto.
        sesiones_revocadas: numero de sesiones vivas que quedaron revocadas (AC-PWD-06).
        notification_id: identificador de la solicitud de aviso entregada, para la traza y el
            soporte posterior. Es un identificador, no el contenido del correo.
    """

    user_id: int
    must_change_password: bool
    password_expires_at: datetime
    reset_at: datetime
    reset_by_user_id: int
    sesiones_revocadas: int
    notification_id: str


class ServicioReposicionCredencial:
    """
    Caso de uso del restablecimiento administrativo de credencial (EP-017, REQ-073).

    Expone UN solo metodo publico, `restablecer`. Todo lo demas -generacion, hashing, envio,
    revocacion y traza- son pasos internos cuyo orden es la propia regla de negocio.

    Lo que este servicio NO hace, a proposito: no resuelve el usuario destino (lo entrega la vista
    desde su repositorio), no comprueba permisos (de eso vive la capa de seguridad), no valida la
    longitud del motivo (la acota el serializer con un 422 de campo) y no traduce nada a HTTP: sus
    errores son `ErrorDominio` y el manejador unico los convierte.
    """

    def __init__(
        self,
        custodia: ServicioCustodiaCredenciales | None = None,
        entrega: ServicioEntregaCredencial | None = None,
    ) -> None:
        # Ambos colaboradores se resuelven de forma PEREZOSA (ver `_custodia` y `_entrega`), igual
        # que `ServicioCustodiaCredenciales` hace con su repositorio: construirlos aqui obligaria a
        # tener el registro de aplicaciones cargado solo para instanciar el servicio.
        #
        # Se inyectan por constructor SOLO para poder verificar el caso de uso sin un SMTP real.
        # La fabrica de produccion (`servicio_entrega_credencial`) NO tiene ningun modo simulado:
        # sin configuracion SMTP activa la entrega se cierra como FALLIDO y aqui acaba en 502.
        self._custodia_inyectada = custodia
        self._entrega_inyectada = entrega

    @property
    def _custodia(self) -> ServicioCustodiaCredenciales:
        """Servicio de custodia de credenciales, instanciado en su primer uso."""

        if self._custodia_inyectada is None:
            self._custodia_inyectada = ServicioCustodiaCredenciales()
        return self._custodia_inyectada

    @property
    def _entrega(self) -> ServicioEntregaCredencial:
        """
        Servicio de entrega del aviso de credencial, fabricado en su primer uso.

        Se construye con `servicio_entrega_credencial()`, el UNICO punto de cableado de produccion
        del aviso de credencial, para no decidir aqui ni el transporte ni el repositorio.
        """

        if self._entrega_inyectada is None:
            self._entrega_inyectada = servicio_entrega_credencial()
        return self._entrega_inyectada

    def restablecer(
        self,
        usuario: UsuarioEntity,
        *,
        actor: ContextoSesion,
        motivo: str | None = None,
    ) -> ResultadoRestablecimiento:
        """
        Emite una credencial temporal al usuario, se la entrega y solo entonces la hace vigente.

        EL ORDEN ES LA REGLA DE NEGOCIO
        ===============================
        REQ-073 regla 7: «un restablecimiento no queda confirmado si falla la entrega del correo
        con el acceso temporal». AC-RST-03: ante un SMTP que rechaza la entrega, el sistema
        responde 502 Y LA CREDENCIAL ANTERIOR DEL USUARIO SIGUE SIENDO LA VIGENTE. De ahi este
        orden, que es el INVERSO del que documenta la entrega para el alta de usuario (REQ-038
        regla 6), donde la cuenta queda creada aunque el correo falle porque no habia nada previo
        que preservar:

        1. validaciones de estado, antes de generar ningun secreto;
        2. generacion de la credencial temporal EN MEMORIA y calculo de su caducidad;
        3. composicion de los datos del aviso, con discriminante propio de esta emision;
        4. fase 1: encolado del aviso en SU PROPIA transaccion, que se confirma;
        5. fase 2: entrega sincrona, ya FUERA de toda transaccion;
        6. si la entrega no prospero se aborta con 502 SIN HABER TOCADO AL USUARIO;
        7. fase 3: en UNA sola transaccion, credencial nueva + revocacion de sesiones + auditoria.

        LA VENTANA QUE SE ACEPTA A CAMBIO
        ---------------------------------
        Si la fase 3 fallara despues de una entrega correcta, el usuario tendria en su buzon una
        credencial que nunca llego a ser vigente: seguiria con la anterior y recibiria un 500 el
        administrador. Es el precio de cumplir REQ-073 regla 7, y es el lado seguro del fallo -el
        usuario conserva acceso-, al reves que confirmar sin haber entregado, que lo dejaria sin
        acceso y sin aviso. El remedio es REEMITIR: una llamada nueva genera un secreto nuevo con
        un discriminante nuevo. La credencial de la emision fallida NO se reutiliza jamas.

        Args:
            usuario: entidad del usuario DESTINO, ya resuelta y verificada como existente por el
                llamante (el 404 de REQ-073 lo produce la busqueda, no este servicio).
            actor: contexto de sesion del ADMINISTRADOR que ejecuta la operacion.
            motivo: justificacion libre del restablecimiento (REQ-073 regla 6), opcional.

        Returns:
            ResultadoRestablecimiento: el desenlace publicable, SIN la credencial.

        Raises:
            CuentaInactivaError: 409, el usuario no esta `ACTIVO`.
            AutorrestablecimientoNoPermitidoError: 409, el actor se lo aplica a si mismo.
            CredencialNoGenerableError: 500, el generador no obtuvo credencial valida.
            PoliticaContraseniaError: 422, la custodia rechazo la credencial generada.
            EntregaCredencialFallidaError: 502, el correo con el acceso temporal no salio; la
                credencial anterior del usuario sigue siendo la vigente.
        """

        # --- 1. Validaciones, antes de generar ningun secreto -----------------
        if usuario.status != ESTADO_USUARIO_ACTIVO:
            # El literal se pasa EXPLICITAMENTE: `CuentaInactivaError` se reexporta desde el modulo
            # de bloqueo y su mensaje por defecto es el de aquel flujo («La cuenta no esta
            # activa»), mientras que REQ-073 publica «El usuario esta inactivo» para este.
            raise CuentaInactivaError(mensajes.CUENTA_INACTIVA)

        if usuario.user_id == actor.user_id:
            # REQ-073 validacion 3: para si mismo existe el cambio propio (EP-005), que SI exige la
            # contrasena actual. Este flujo no la pide, y permitirlo convertiria una sesion abierta
            # -o robada- en un cambio de credencial sin presentar el secreto anterior.
            raise AutorrestablecimientoNoPermitidoError()

        # `motivo` NO se revalida aqui: su longitud (`LONGITUD_MAXIMA_MOTIVO`, REQ-073 regla 6) la
        # acota el serializer de la vista, que es quien puede responder un 422 de CAMPO diciendo
        # cual se paso de largo. Repetir la comprobacion en este punto daria dos errores distintos
        # para el mismo dato segun por donde entrase.
        #
        # Y ademas: `reset_reason` de REQ-073 NO TIENE DESTINO EN EL MODELO DE DATOS T.5. Ni
        # `usuario` ni `auditoria_acceso` tienen columna donde guardarlo, y este modulo no inventa
        # tablas. Por eso el motivo es trazabilidad de la PETICION y no se persiste en ninguna
        # parte; tampoco se loggea, porque es texto libre del administrador (REQ-079).

        # --- 2. Credencial temporal EN MEMORIA --------------------------------
        ahora = utc_now()
        # Se le pasan `username` y `corporate_email` para que la credencial generada no los
        # contenga y, por tanto, no la rechace despues la politica de `establecer`.
        credencial = generar_credencial_temporal(username=usuario.username, corporate_email=usuario.corporate_email)
        expira_en = caducidad_credencial_temporal(ahora)

        # --- 3. Datos del aviso ------------------------------------------------
        datos = DatosCredencial(
            notification_type=TIPO_RESTABLECIMIENTO,
            user_id=usuario.user_id,
            full_name=usuario.full_name,
            corporate_email=usuario.corporate_email,
            credencial_temporal=credencial,
            expires_at=expira_en,
            # El discriminante es OBLIGATORIO y distinto en cada emision: sin el, el segundo
            # restablecimiento del mismo usuario chocaria con la clave de idempotencia del primero,
            # la solicitud se daria por ya encolada y el usuario se quedaria esperando un correo que
            # no llega nunca.
            discriminante=ahora.strftime(FORMATO_DISCRIMINANTE),
        )

        # --- 4. Fase 1: encolar en SU PROPIA transaccion, y confirmarla -------
        # La solicitud se encola y se CONFIRMA antes de abrir la conexion SMTP. Entregar con la
        # transaccion abierta mantendria las filas bloqueadas durante todo el timeout del servidor
        # de correo, que es el anti-patron que el handbook prohibe expresamente.
        with transaction.atomic():
            solicitud = self._entrega.encolar(datos)

        # --- 5. Fase 2: entregar, ya FUERA de toda transaccion ----------------
        resultado_entrega = self._entrega.entregar(solicitud, datos)

        # --- 6. Sin entrega no hay restablecimiento ---------------------------
        if not resultado_entrega.entregado:
            # Se mira `entregado` y NO `debe_responder_502`: una entrega OMITIDA tampoco ha puesto
            # el correo en el buzon del usuario, y confirmar el restablecimiento ahi lo dejaria sin
            # acceso y sin aviso. En este punto NO SE HA ESCRITO NADA SOBRE EL USUARIO: su
            # `password_hash`, su `must_change_password` y sus sesiones siguen exactamente como
            # estaban, que es justo lo que exige AC-RST-03.
            raise EntregaCredencialFallidaError(
                resultado_entrega.mensaje_usuario or MENSAJE_502_RESTABLECIMIENTO,
                codigo_error=resultado_entrega.codigo_error,
            )

        # --- 7. Fase 3: confirmar, en UNA sola transaccion --------------------
        # El UPDATE de la credencial, la revocacion de sesiones y el asiento de auditoria caen
        # juntos o no cae ninguno. Si fallara la revocacion no puede quedar una contrasena nueva con
        # las sesiones viejas todavia vivas (AC-PWD-06), ni un cambio de credencial sin evidencia.
        with transaction.atomic():
            self._custodia.establecer(
                usuario,
                credencial,
                actor=actor,
                pendiente_de_cambio=True,
                expira_en=expira_en,
            )
            revocadas = self._revocar_sesiones(usuario)
            registrar_restablecimiento(usuario=usuario, actor=actor)

        # SOLO identificadores. Ni la credencial, ni el `corporate_email`, ni el `full_name`, ni el
        # `password_hash`, ni el `motivo` (REQ-063, REQ-076, REQ-079).
        logger.info(
            TRAZA_RESTABLECIMIENTO_CONFIRMADO,
            extra={
                "data": {
                    "user_id": usuario.user_id,
                    "session_user_id": actor.user_id,
                    "notification_id": solicitud.notification_id,
                    "sesiones_revocadas": revocadas,
                    "outcome": "OK",
                }
            },
        )

        return ResultadoRestablecimiento(
            user_id=usuario.user_id,
            must_change_password=True,
            password_expires_at=expira_en,
            reset_at=ahora,
            reset_by_user_id=actor.user_id,
            sesiones_revocadas=revocadas,
            notification_id=solicitud.notification_id,
        )

    def _revocar_sesiones(self, usuario: UsuarioEntity) -> int:
        """
        Revoca EN BLOQUE las sesiones vivas del usuario al reponerle la credencial (AC-PWD-06).

        El criterio de aceptacion exige que el 100 % de las sesiones previas responda 401 en la
        primera peticion posterior, sin esperar a su vencimiento natural: quien tuviera abierta una
        sesion con la credencial anterior -incluido quien se la hubiera apropiado- deja de tener
        acceso en el mismo instante en que la credencial deja de ser vigente.

        Args:
            usuario: entidad del usuario destino.

        Returns:
            int: numero de filas efectivamente revocadas, que es lo que se publica como
            `sesiones_revocadas`. Puede ser 0 y no es un error: un usuario sin sesiones abiertas.
        """

        # El modelo se importa AQUI DENTRO: este servicio puede resolverse antes de `django.setup()`
        # y un import de modelos a nivel de modulo reventaria el arranque (`AppRegistryNotReady`).
        from apps.core.models import SesionUsuarioEntity

        # UN solo UPDATE resuelto por la base, no un recorrido de filas en Python: entre la lectura
        # y la escritura podria emitirse una sesion nueva que quedaria viva con la credencial ya
        # cambiada. `revoked_at` y `revocation_reason` se escriben JUNTOS porque el CHECK del
        # esquema (`ck_sesion_usuario_revocacion`) exige que esten informados exactamente a la vez.
        # Las filas NO se borran (REQ-047): la sesion revocada permanece como evidencia del acceso.
        return SesionUsuarioEntity.objects.filter(user_id=usuario.user_id, revoked_at__isnull=True).update(
            revoked_at=utc_now(),
            revocation_reason=MOTIVO_REVOCACION_RESTABLECIMIENTO,
        )


__all__ = [
    "ESTADO_USUARIO_ACTIVO",
    "LONGITUD_MAXIMA_MOTIVO",
    "MOTIVO_REVOCACION_RESTABLECIMIENTO",
    "ResultadoRestablecimiento",
    "ServicioReposicionCredencial",
]
