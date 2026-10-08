"""
Servicio de resolucion de destinatarios de aviso (REQ-089, REQ-140, ARC-120).

Decide A QUIEN se puede avisar antes de que nadie componga un correo: resuelve el colectivo de
mantenimiento, comprueba si el empleado reportante es notificable y deja constancia auditable de
cada intento en `resolucion_destinatario_log`.

Es un servicio INTERNO del backend (REQ-089): no se expone como endpoint ni a EMPLEADO ni a
TECNICO_MANTENIMIENTO, y esta tarea no declara ninguna ruta. Lo consume la capa de avisos.

Tres decisiones estructurales gobiernan todo el modulo:

* SIN CACHE DE NINGUN TIPO. Cada resolucion ejecuta su consulta contra la base (REQ-089, RN-03):
  una desactivacion de cuenta debe surtir efecto en el aviso SIGUIENTE, y cualquier memoizacion
  -`lru_cache`, `cached_property`, atributos de instancia o de modulo que guarden resultados-
  mandaria correos a personas ya dadas de baja. Si una consulta duele, se arregla con indices.
* MODO DEGRADADO. La indisponibilidad del directorio NO impide la operacion de negocio: el fallo
  tecnico se traduce en un resultado con `degradado=True` que el consumidor interpreta como "no
  se ha podido avisar", nunca en una excepcion que tumbe el alta o el cambio de estado.
* EL LOG NO LLEVA DATOS PERSONALES. En `logger` solo viajan identificadores y contadores; jamas
  un `corporate_email`, un nombre completo ni una lista de direcciones (REQ-063, REQ-076,
  REQ-079). Las direcciones viven en el resultado devuelto, que no se persiste ni se traza.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, TypeVar

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import Error as ErrorBaseDatos

from apps.avisos.resolucion.errores import (
    ColectivoDesconocidoError,
    ConsultaNoAutorizadaError,
    DirectorioNoDisponibleError,
)
from apps.avisos.resolucion.repositorio import (
    RepositorioDirectorioDestinatarios,
    RepositorioResolucionDestinatarioLog,
)
from apps.avisos.resolucion.resultados import (
    COLECTIVO_EQUIPO_MANTENIMIENTO,
    ESTADO_USUARIO_ACTIVO,
    LONGITUD_MAXIMA_CORREO,
    DestinatarioResuelto,
    MotivoNoNotificable,
    NotificabilidadDestinatario,
    ResolucionColectivo,
    ResultadoResolucion,
    TipoResolucion,
)
from apps.core.contexto import contexto_requerido, obtener_contexto

if TYPE_CHECKING:  # pragma: no cover - solo tipado; importar modelos aqui romperia antes de django.setup()
    from apps.core.models import ResolucionDestinatarioLogEntity, UsuarioEntity


logger = logging.getLogger(__name__)


# Modulo que solicita la resolucion; se persiste tal cual en `resolucion_destinatario_log.
# requested_by_module` (VARCHAR2(50)), asi que ningun valor pasado por el consumidor debe
# exceder los 50 caracteres o la insercion la rechazara Oracle.
MODULO_POR_DEFECTO = "ARC-014"

# Rol unico autorizado a consultar la traza de resoluciones (REQ-089 RN-09, REQ-140) y unico
# que se informa como `resolved_by_user_id` (ARC-120).
ROL_ADMINISTRADOR = "ADMINISTRADOR"

_T = TypeVar("_T")


class ServicioResolucionDestinatarios:
    """
    Resuelve destinatarios de aviso contra el directorio vigente y audita cada resolucion.

    Los repositorios se inyectan por constructor para poder sustituirlos por dobles en prueba sin
    levantar Oracle; en produccion se instancian por defecto. Lo que NO se puede sustituir es la
    politica: quien es notificable, que se registra y que ocurre cuando el directorio no responde
    se decide aqui y en un unico sitio.
    """

    def __init__(
        self,
        directorio: RepositorioDirectorioDestinatarios | None = None,
        registro: RepositorioResolucionDestinatarioLog | None = None,
    ) -> None:
        self.directorio: RepositorioDirectorioDestinatarios = directorio or RepositorioDirectorioDestinatarios()
        self.registro: RepositorioResolucionDestinatarioLog = registro or RepositorioResolucionDestinatarioLog()

    # --- Resolucion de colectivos ----------------------------------------

    def resolver_colectivo(self, colectivo: str, *, modulo: str = MODULO_POR_DEFECTO) -> ResolucionColectivo:
        """
        Resuelve un colectivo por su identificador de contrato.

        El catalogo de colectivos notificables es CERRADO: hoy solo existe el equipo de
        mantenimiento. Un identificador fuera del catalogo es un error del llamante, no un
        colectivo vacio, y por eso se distingue con una excepcion (escenario de error 2 de
        REQ-089) en vez de devolver una lista sin destinatarios que se confundiria con el caso
        legitimo "no hay tecnicos activos".

        Raises:
            ColectivoDesconocidoError: si `colectivo` no pertenece al catalogo.
        """

        if colectivo.strip().lower() != COLECTIVO_EQUIPO_MANTENIMIENTO:
            raise ColectivoDesconocidoError(colectivo)
        return self.resolver_equipo_mantenimiento(modulo=modulo)

    def resolver_equipo_mantenimiento(self, *, modulo: str = MODULO_POR_DEFECTO) -> ResolucionColectivo:
        """
        Resuelve el equipo de mantenimiento en el INSTANTE de la llamada (REQ-089, RN-03).

        La composicion del colectivo sale de la consulta al directorio, que ya filtra por rol y por
        estado ACTIVO en SQL: un usuario INACTIVO no puede figurar jamas como destinatario. Sobre
        ese resultado se descartan los correos no notificables y los duplicados por direccion.

        Un colectivo vacio NO es un error: se devuelve con `outcome = SIN_DESTINATARIOS`, queda
        registrado en la traza para que el ADMINISTRADOR pueda consultarlo, y el consumidor no
        envia nada porque `debe_enviarse` es falso.
        """

        try:
            return self._resolver_equipo_mantenimiento(modulo=modulo)
        except DirectorioNoDisponibleError as error:
            return self._colectivo_degradado(modulo=modulo, error=error)

    def _resolver_equipo_mantenimiento(self, *, modulo: str) -> ResolucionColectivo:
        """Camino nominal de `resolver_equipo_mantenimiento`; deja subir `DirectorioNoDisponibleError`."""

        usuarios = self._ejecutar(self.directorio.listar_equipo_mantenimiento)
        destinatarios = self._destinatarios_notificables(usuarios)
        outcome = ResultadoResolucion.OK if destinatarios else ResultadoResolucion.SIN_DESTINATARIOS

        # `is_fallback_used` va SIEMPRE a False y es una decision consciente (REQ-140, RN-02): un
        # colectivo vacio no origina envio y NO se sustituye el destinatario por el buzon de
        # respaldo de facilities. El RFP no define destinatario alternativo para este caso, y
        # redirigir el aviso a un buzon no previsto seria inventar una regla de negocio y, de paso,
        # enviar informacion de incidencias a quien nadie ha autorizado a recibirla.
        asiento = self._ejecutar(
            lambda: self.registro.registrar(
                request_type=TipoResolucion.COLECTIVO,
                requested_by_module=modulo,
                resolved_user_ids=[destinatario.user_id for destinatario in destinatarios],
                outcome=outcome,
                subject_user_id=None,
                is_fallback_used=False,
                resolved_by_user_id=self._actor_administrador(),
            )
        )
        resolucion = ResolucionColectivo(
            colectivo=COLECTIVO_EQUIPO_MANTENIMIENTO,
            destinatarios=destinatarios,
            outcome=outcome,
            resolution_id=asiento.resolution_id,
            degradado=False,
        )
        logger.info(
            "destinatarios_resueltos",
            extra={
                "data": {
                    "requested_by_module": modulo,
                    "request_type": TipoResolucion.COLECTIVO.value,
                    "colectivo": COLECTIVO_EQUIPO_MANTENIMIENTO,
                    "outcome": outcome.value,
                    "recipient_count": len(destinatarios),
                    "descartados": len(usuarios) - len(destinatarios),
                    "resolution_id": resolucion.resolution_id,
                }
            },
        )
        return resolucion

    # --- Resolucion individual -------------------------------------------

    def resolver_notificabilidad_reportante(self, user_id: int, *, modulo: str = MODULO_POR_DEFECTO) -> NotificabilidadDestinatario:
        """
        Determina si el EMPLEADO reportante puede recibir el aviso de cambio de estado (REQ-089, REQ-140).

        Lee SIEMPRE el dato vigente del directorio, nunca una copia cacheada ni la que viajase en la
        peticion: el estado de la cuenta y el correo pueden haber cambiado desde que se creo la
        incidencia.

        No lanza excepcion cuando el reportante no es notificable. El cambio de estado de la
        incidencia no se bloquea JAMAS por esto: se devuelve el resultado con su motivo y es el
        consumidor quien lo traduce a una supresion registrada en `aviso_correo`.
        """

        try:
            return self._resolver_notificabilidad(user_id, modulo=modulo)
        except DirectorioNoDisponibleError as error:
            return self._individual_degradado(user_id, modulo=modulo, error=error)

    def _resolver_notificabilidad(self, user_id: int, *, modulo: str) -> NotificabilidadDestinatario:
        """Camino nominal de `resolver_notificabilidad_reportante`; deja subir `DirectorioNoDisponibleError`."""

        usuario = self._ejecutar(lambda: self.directorio.obtener_usuario(user_id))

        if usuario is None:
            return self._reportante_no_encontrado(user_id, modulo=modulo)

        if usuario.status != ESTADO_USUARIO_ACTIVO:
            # Motivo RECIPIENT_INACTIVE de REQ-089; la capa de avisos lo traduce al codigo fisico
            # USUARIO_DESACTIVADO del catalogo cerrado via `suppression_reason_code`.
            return self._registrar_individual(
                user_id,
                modulo=modulo,
                es_notificable=False,
                destinatario=None,
                motivo=MotivoNoNotificable.RECIPIENT_INACTIVE,
                outcome=ResultadoResolucion.NO_NOTIFICABLE,
            )

        if not self._es_correo_notificable(usuario.corporate_email):
            return self._registrar_individual(
                user_id,
                modulo=modulo,
                es_notificable=False,
                destinatario=None,
                motivo=MotivoNoNotificable.RECIPIENT_WITHOUT_EMAIL,
                outcome=ResultadoResolucion.NO_NOTIFICABLE,
            )

        return self._registrar_individual(
            user_id,
            modulo=modulo,
            es_notificable=True,
            destinatario=self._como_destinatario(usuario),
            motivo=None,
            outcome=ResultadoResolucion.OK,
        )

    def _reportante_no_encontrado(self, user_id: int, *, modulo: str) -> NotificabilidadDestinatario:
        """
        Resultado NO_ENCONTRADO: unico caso en el que la resolucion NO se persiste.

        EXCEPCION DOCUMENTADA (hallazgo para arquitectura, no un atajo): el DDL de
        `resolucion_destinatario_log` exige a la vez `subject_user_id IS NOT NULL` para las
        resoluciones INDIVIDUAL (CHECK `ck_res_dest_subject_iff`) y que ese identificador exista en
        `usuario` (FK `fk_res_dest_subject_user`). Ambas restricciones juntas hacen IMPOSIBLE dejar
        constancia de la resolucion de un identificador inexistente: la fila violaria la clave
        ajena. Se omite el insert, se deja traza por `logger` y se devuelve `resolution_id = None`.
        Resolverlo de verdad exige un changelog que relaje la FK o una tabla de intentos aparte.
        """

        logger.warning(
            "resolucion_destinatario_no_registrada",
            extra={
                "data": {
                    "requested_by_module": modulo,
                    "request_type": TipoResolucion.INDIVIDUAL.value,
                    "subject_user_id": user_id,
                    "outcome": ResultadoResolucion.NO_ENCONTRADO.value,
                    "motivo": "subject_user_id inexistente: la FK fk_res_dest_subject_user impide persistir el asiento",
                }
            },
        )
        return NotificabilidadDestinatario(
            user_id=user_id,
            es_notificable=False,
            destinatario=None,
            motivo=MotivoNoNotificable.RECIPIENT_NOT_RESOLVABLE,
            outcome=ResultadoResolucion.NO_ENCONTRADO,
            resolution_id=None,
            degradado=False,
        )

    def _registrar_individual(
        self,
        user_id: int,
        *,
        modulo: str,
        es_notificable: bool,
        destinatario: DestinatarioResuelto | None,
        motivo: MotivoNoNotificable | None,
        outcome: ResultadoResolucion,
    ) -> NotificabilidadDestinatario:
        """Persiste el asiento de una resolucion individual y devuelve el resultado ya sellado con su `resolution_id`."""

        # La lista de resueltos lleva al propio destinatario solo si es notificable; si no lo es, no
        # se ha resuelto a nadie a quien enviar, aunque el usuario exista.
        resueltos: Sequence[int] = [user_id] if es_notificable else []
        asiento = self._ejecutar(
            lambda: self.registro.registrar(
                request_type=TipoResolucion.INDIVIDUAL,
                requested_by_module=modulo,
                resolved_user_ids=resueltos,
                outcome=outcome,
                subject_user_id=user_id,
                is_fallback_used=False,
                resolved_by_user_id=self._actor_administrador(),
            )
        )
        logger.info(
            "destinatarios_resueltos",
            extra={
                "data": {
                    "requested_by_module": modulo,
                    "request_type": TipoResolucion.INDIVIDUAL.value,
                    "subject_user_id": user_id,
                    "outcome": outcome.value,
                    "recipient_count": len(resueltos),
                    "motivo": motivo.value if motivo is not None else None,
                    "resolution_id": asiento.resolution_id,
                }
            },
        )
        return NotificabilidadDestinatario(
            user_id=user_id,
            es_notificable=es_notificable,
            destinatario=destinatario,
            motivo=motivo,
            outcome=outcome,
            resolution_id=asiento.resolution_id,
            degradado=False,
        )

    # --- Consulta de diagnostico ------------------------------------------

    def consultar_resoluciones(self, *, limite: int = 50) -> list[ResolucionDestinatarioLogEntity]:
        """
        Ultimas resoluciones registradas, para diagnostico del administrador (REQ-089 RN-09, REQ-140).

        Deny-by-default: `contexto_requerido()` ya rechaza la llamada sin sesion establecida, y aqui
        se exige ademas el rol ADMINISTRADOR. La traza revela a quien se aviso y cuando, asi que no
        es informacion de diagnostico inocua: queda reservada al unico rol con alcance global.

        Raises:
            ContextoSesionNoDisponibleError: si no hay contexto de sesion.
            ConsultaNoAutorizadaError: si el rol de la sesion no es ADMINISTRADOR.
        """

        contexto = contexto_requerido()
        if contexto.role_code != ROL_ADMINISTRADOR:
            raise ConsultaNoAutorizadaError()
        return self.registro.listar_recientes(limite=limite)

    # --- Helpers de dominio -----------------------------------------------

    @staticmethod
    def _normalizar_correo(valor: str) -> str:
        """
        Forma canonica de una direccion: sin espacios al borde y en minusculas.

        Es la misma normalizacion que impone el indice unico `ux_usuario_email_ci` sobre
        `LOWER(TRIM(corporate_email))`, de modo que dos fichas que la base considera la misma
        persona tampoco produzcan dos destinatarios distintos aqui.
        """

        return valor.strip().lower()

    @staticmethod
    def _es_correo_notificable(valor: str | None) -> bool:
        """
        Indica si una direccion sirve para enviar el aviso (REQ-089).

        Se comprueba aqui y no al enviar porque un correo invalido no es un fallo de entrega: es un
        destinatario que no se puede resolver, y debe descartarse ANTES de originar el envio para
        que no quede un aviso en PENDIENTE esperando a un buzon que no existe.
        """

        if valor is None:
            return False
        direccion = valor.strip()
        if not direccion or len(direccion) > LONGITUD_MAXIMA_CORREO:
            return False
        try:
            validate_email(direccion)
        except ValidationError:
            return False
        return True

    def _destinatarios_notificables(self, usuarios: Sequence[UsuarioEntity]) -> tuple[DestinatarioResuelto, ...]:
        """
        Convierte las fichas del directorio en destinatarios, descartando no notificables y duplicados.

        La deduplicacion es por direccion NORMALIZADA, no por `user_id`: dos fichas distintas pueden
        compartir buzon y enviar dos veces el mismo aviso a la misma persona seria ruido. Se conserva
        el orden de la consulta (ya determinista por `user_id`) para que la traza sea reproducible.
        """

        destinatarios: list[DestinatarioResuelto] = []
        direcciones_vistas: set[str] = set()
        for usuario in usuarios:
            if not self._es_correo_notificable(usuario.corporate_email):
                continue
            direccion = self._normalizar_correo(usuario.corporate_email)
            if direccion in direcciones_vistas:
                continue
            direcciones_vistas.add(direccion)
            destinatarios.append(self._como_destinatario(usuario, direccion=direccion))
        return tuple(destinatarios)

    @staticmethod
    def _como_destinatario(usuario: UsuarioEntity, *, direccion: str | None = None) -> DestinatarioResuelto:
        """
        Traduce una ficha del directorio al objeto de valor del dominio con la direccion ya normalizada.

        Se lee `role_code_id` (el valor crudo de la clave ajena) y no `role_code`, que dispararia una
        consulta adicional a `rol` para devolver una entidad que aqui no hace falta.
        """

        if direccion is None:
            direccion = ServicioResolucionDestinatarios._normalizar_correo(usuario.corporate_email)
        return DestinatarioResuelto(
            user_id=usuario.user_id,
            full_name=usuario.full_name,
            corporate_email=direccion,
            role_code=usuario.role_code_id,
        )

    def _actor_administrador(self) -> int | None:
        """
        Identificador del actor a registrar en `resolved_by_user_id`, o `None`.

        Se usa `obtener_contexto()` y NO `contexto_requerido()` a proposito: la resolucion la puede
        disparar un proceso del sistema sin sesion (el consumidor de avisos), y exigir identidad
        bloquearia una operacion que no la necesita.

        La columna solo se informa cuando la consulta la lanza un administrador (ARC-120); para el
        resto de roles va `None`, porque en esos casos la resolucion es un efecto del flujo de
        negocio y no una consulta atribuible a una persona.
        """

        contexto = obtener_contexto()
        if contexto is None or contexto.role_code != ROL_ADMINISTRADOR:
            return None
        return contexto.user_id

    # --- Modo degradado ----------------------------------------------------

    @staticmethod
    def _ejecutar(operacion: Callable[[], _T]) -> _T:
        """
        Envuelve un acceso a datos y traduce el fallo de base a `DirectorioNoDisponibleError`.

        Captura SOLO la familia `django.db.Error`: cualquier otra excepcion (un `AttributeError`, un
        dato corrupto) es un defecto del codigo y debe subir intacta en vez de disfrazarse de
        indisponibilidad del directorio y degradar silenciosamente la resolucion.
        """

        try:
            return operacion()
        except ErrorBaseDatos as error:
            raise DirectorioNoDisponibleError(str(error)) from error

    def _colectivo_degradado(self, *, modulo: str, error: DirectorioNoDisponibleError) -> ResolucionColectivo:
        """Resultado degradado de un colectivo: la operacion de negocio sigue, el aviso no se da por enviado."""

        self._registrar_error_tecnico(request_type=TipoResolucion.COLECTIVO, modulo=modulo, subject_user_id=None, detalle=error.detalle)
        logger.error(
            "resolucion_destinatarios_degradada",
            extra={
                "data": {
                    "requested_by_module": modulo,
                    "request_type": TipoResolucion.COLECTIVO.value,
                    "colectivo": COLECTIVO_EQUIPO_MANTENIMIENTO,
                    "outcome": ResultadoResolucion.ERROR_TECNICO.value,
                    "recipient_count": 0,
                    "resolution_id": None,
                    "detalle": error.detalle,
                }
            },
        )
        return ResolucionColectivo(
            colectivo=COLECTIVO_EQUIPO_MANTENIMIENTO,
            destinatarios=(),
            outcome=ResultadoResolucion.ERROR_TECNICO,
            resolution_id=None,
            degradado=True,
        )

    def _individual_degradado(self, user_id: int, *, modulo: str, error: DirectorioNoDisponibleError) -> NotificabilidadDestinatario:
        """Resultado degradado de una resolucion individual: nunca bloquea el cambio de estado de la incidencia."""

        self._registrar_error_tecnico(
            request_type=TipoResolucion.INDIVIDUAL,
            modulo=modulo,
            subject_user_id=user_id,
            detalle=error.detalle,
        )
        logger.error(
            "resolucion_destinatarios_degradada",
            extra={
                "data": {
                    "requested_by_module": modulo,
                    "request_type": TipoResolucion.INDIVIDUAL.value,
                    "subject_user_id": user_id,
                    "outcome": ResultadoResolucion.ERROR_TECNICO.value,
                    "recipient_count": 0,
                    "resolution_id": None,
                    "detalle": error.detalle,
                }
            },
        )
        return NotificabilidadDestinatario(
            user_id=user_id,
            es_notificable=False,
            destinatario=None,
            motivo=MotivoNoNotificable.RECIPIENT_NOT_RESOLVABLE,
            outcome=ResultadoResolucion.ERROR_TECNICO,
            resolution_id=None,
            degradado=True,
        )

    def _registrar_error_tecnico(
        self,
        *,
        request_type: TipoResolucion,
        modulo: str,
        subject_user_id: int | None,
        detalle: str,
    ) -> None:
        """
        Intenta dejar constancia del ERROR_TECNICO en la traza; es BEST-EFFORT.

        Si el directorio no responde lo normal es que la traza tampoco acepte el insert. Esa
        excepcion secundaria se traga y se registra por `logger`, porque propagarla enmascararia el
        fallo ORIGINAL y convertiria una degradacion controlada en un error de la operacion de
        negocio, que es justo lo que REQ-089 prohibe.
        """

        try:
            self.registro.registrar(
                request_type=request_type,
                requested_by_module=modulo,
                resolved_user_ids=[],
                outcome=ResultadoResolucion.ERROR_TECNICO,
                subject_user_id=subject_user_id,
                is_fallback_used=False,
                resolved_by_user_id=self._actor_administrador(),
            )
        except Exception as secundario:  # noqa: BLE001 - best-effort: la traza no puede tumbar la degradacion
            logger.error(
                "resolucion_destinatarios_traza_no_persistida",
                extra={
                    "data": {
                        "requested_by_module": modulo,
                        "request_type": request_type.value,
                        "subject_user_id": subject_user_id,
                        "detalle_original": detalle,
                        "detalle_secundario": str(secundario),
                    }
                },
            )


__all__ = [
    "MODULO_POR_DEFECTO",
    "ROL_ADMINISTRADOR",
    "ServicioResolucionDestinatarios",
]
