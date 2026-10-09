"""
Asignacion, cambio y consulta del rol funcional de un usuario (EP-011, EP-012, REQ-005, REQ-043).

Este modulo es el CASO DE USO: decide, en el orden en que hay que decidirlo, y delega todo acceso a
datos en `RepositorioRolesUsuario`. No conoce DRF, no construye respuestas HTTP y no traduce
estados: lanza excepciones de `apps.usuarios.roles.errores` y el manejador unico las convierte.

LA TRANSACCION ES DE AQUI, Y ES UNA SOLA
-----------------------------------------
El cambio de rol escribe DOS cosas que no pueden separarse -el asiento del historico y el rol
vigente de la fila del usuario- mas una tercera que depende de ambas -la marca de recarga de las
sesiones vivas-. Por eso `cambiar` abre UNA `transaction.atomic()` que es la frontera transaccional
completa del caso de uso: si el asiento del historico falla, el rol NO se cambia. El repositorio no
abre ni cierra ninguna, y la consulta del historico no abre ninguna porque solo lee.

AQUI NO SE AUTORIZA
-------------------
Ningun metodo mira el rol del actor para DECIDIR SI PUEDE: de eso vive la guardia declarativa del
router (`apps.core_security.permisos`), que evalua la matriz `permiso_rol_operacion` y produce el
403 uniforme de REQ-078. La UNICA lectura del rol del actor que hay en este modulo es la regla de
negocio de REQ-043 RN-03 -nadie se retira a si mismo el rol de administracion-, que es una
prohibicion de negocio y no una comprobacion de permisos.

TRAZAS SIN DATOS PERSONALES (REQ-063, REQ-076, REQ-079)
--------------------------------------------------------
En las trazas de este modulo viajan identificadores y codigos de rol, nunca el nombre ni el correo
de la persona afectada.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from math import ceil
from typing import TYPE_CHECKING

from django.db import transaction

from apps.core.contexto import ContextoSesion, contexto_de_sesion, utc_now
from apps.core_security.permisos import ROL_ADMINISTRADOR
from apps.usuarios.roles import mensajes
from apps.usuarios.roles.errores import (
    AutorretiradaRolAdministracionError,
    MismoRolError,
    RolNoValidoError,
    RolYaAsignadoError,
    UsuarioDeBajaError,
    UsuarioNoEncontradoError,
)
from apps.usuarios.roles.repositorio import ESTADO_USUARIO_ACTIVO, RepositorioRolesUsuario


if TYPE_CHECKING:  # pragma: no cover - solo tipado: evita importar modelos antes de django.setup()
    from apps.core.models import UsuarioEntity, UsuarioHistoricoEntity


logger = logging.getLogger(__name__)

TRAZA_ROL_ASIGNADO = "Rol asignado en el alta del usuario"
TRAZA_ROL_CAMBIADO = "Rol de usuario cambiado"


@dataclass(frozen=True, slots=True)
class DatosCambioRol:
    """
    Unico dato de entrada del cambio de rol (REQ-005, AC-ROL-03), YA validado por el serializador.

    Lo que NO esta aqui es deliberado: no hay `changedBy` ni alias suyo, porque el actor se toma
    SIEMPRE del contexto de sesion (REQ-064) y aceptarlo del payload seria dejar que el cliente
    firme la auditoria con el nombre de otro.

    Attributes:
        role_code: codigo del rol nuevo, del catalogo `cat_rol`.
    """

    role_code: str


@dataclass(frozen=True, slots=True)
class RolAsignado:
    """
    Desenlace de la asignacion de rol del ACTO DEL ALTA (AC-ROL-01).

    `assigned_by_user_id` y `assigned_at` son los nombres del criterio de aceptacion; las columnas
    FISICAS que los sostienen son `usuario.role_changed_by` y `usuario.role_changed_at` del esquema
    T.5 (ARC-110), que es donde los escribe el repositorio.

    Attributes:
        user_id: usuario al que se le asigno el rol.
        role_code: codigo del rol asignado.
        assigned_by_user_id: administrador que ejecuto el alta, leido del contexto de sesion.
        assigned_at: instante UTC naive de la asignacion, el MISMO del asiento del historico.
    """

    user_id: int
    role_code: str
    assigned_by_user_id: int
    assigned_at: datetime


@dataclass(frozen=True, slots=True)
class RolCambiado:
    """
    Desenlace publicable de un cambio de rol ya confirmado (EP-011, AC-ROL-03, AC-PERM-05).

    `frozen=True` por lo de siempre: es la foto de lo que YA ocurrio y nadie aguas arriba debe poder
    retocarla.

    Attributes:
        user_id: usuario al que se le cambio el rol.
        full_name: nombre del usuario afectado, para que la respuesta sea legible. NO viaja a las
            trazas (REQ-063, REQ-076, REQ-079).
        role_code: codigo del rol nuevo, ya vigente.
        role_name: denominacion del rol nuevo en `cat_rol`, o `None` si el catalogo no la publica.
        previous_role_code: codigo del rol que el usuario tenia hasta este cambio.
        role_changed_at: instante UTC naive del cambio, el MISMO del asiento del historico.
        role_changed_by: administrador que lo ejecuto, leido del contexto de sesion.
        sesiones_refrescadas: cuantas sesiones vivas quedaron marcadas para recargar permisos. Puede
            ser 0 -el usuario no tenia ninguna abierta- y no es un fallo.
    """

    user_id: int
    full_name: str
    role_code: str
    role_name: str | None
    previous_role_code: str
    role_changed_at: datetime
    role_changed_by: int
    sesiones_refrescadas: int


@dataclass(frozen=True, slots=True)
class CriteriosHistoricoRol:
    """
    Criterios de la consulta del historico de rol (EP-012, AC-ROL-05), YA validados.

    Attributes:
        desde: primer dia incluido, o `None` para no acotar por abajo.
        hasta: ultimo dia INCLUIDO, o `None` para no acotar por arriba.
        pagina: numero de pagina pedido, base 1.
        tamanio_pagina: numero de entradas por pagina.
    """

    desde: date | None
    hasta: date | None
    pagina: int
    tamanio_pagina: int


@dataclass(frozen=True, slots=True)
class EntradaHistoricoRol:
    """
    Una entrada de rol del historico, proyectada para su publicacion (AC-ROL-05).

    Attributes:
        history_id: identificador del asiento, asignado por la IDENTITY de Oracle.
        user_id: usuario al que se refiere el movimiento.
        previous_role_code: rol anterior; `None` en la entrada de ALTA, que no cierra ninguno.
        new_role_code: rol que la entrada deja abierto.
        changed_by_user_id: usuario que ejecuto el movimiento.
        changed_by_display_name: nombre de ese actor, o `None` si la entrada no lo tiene.
        changed_at: instante del asiento, UTC naive.
        valid_from: instante en que se abre la vigencia del rol nuevo.
        valid_to: instante en que se cierra la del anterior; `None` en la entrada de ALTA.
    """

    history_id: int
    user_id: int
    previous_role_code: str | None
    new_role_code: str
    changed_by_user_id: int
    changed_by_display_name: str | None
    changed_at: datetime
    valid_from: datetime
    valid_to: datetime | None


@dataclass(frozen=True, slots=True)
class PaginaHistoricoRol:
    """
    Pagina de historico de rol lista para publicar (EP-012, AC-ROL-05).

    Attributes:
        items: entradas de la pagina, en orden cronologico descendente. Tupla y no lista: el
            resultado no se retoca aguas arriba.
        page: numero de pagina servido, base 1.
        page_size: tamanio de pagina aplicado.
        total_count: total de entradas que cumplen el filtro, ANTES de trocear.
        total_pages: numero de paginas de ese total; 0 cuando no hay ninguna entrada.
        empty_message: literal explicativo cuando la pagina no trae entradas; `None` si las trae.
    """

    items: tuple[EntradaHistoricoRol, ...]
    page: int
    page_size: int
    total_count: int
    total_pages: int
    empty_message: str | None


class ServicioRolUsuario:
    """
    Caso de uso del rol funcional del usuario: asignacion en el alta, cambio y consulta del historico.

    Expone tres metodos publicos y ninguno mas: `asignar_en_alta` (AC-ROL-01), `cambiar` (EP-011,
    AC-ROL-03) y `consultar_historico` (EP-012, AC-ROL-05).

    Lo que este servicio NO hace, a proposito: no comprueba permisos (de eso vive la guardia del
    router, que produce el 403 uniforme), no valida formatos ni rangos de la peticion (lo hace el
    serializador, que es quien puede atribuir el fallo a un CAMPO) y no traduce nada a HTTP.
    """

    def __init__(self, repositorio: RepositorioRolesUsuario | None = None) -> None:
        # El repositorio se resuelve de forma PEREZOSA (ver la propiedad de abajo), igual que en
        # `ServicioAltaUsuario`: resuelve modelos del ORM, asi que construirlo aqui obligaria a
        # tener el registro de aplicaciones cargado solo para instanciar el servicio.
        #
        # Se inyecta por constructor SOLO para poder verificar el caso de uso sin base de datos.
        self._repositorio_inyectado = repositorio

    @property
    def _repositorio(self) -> RepositorioRolesUsuario:
        """Repositorio de roles, instanciado en su primer uso (resuelve modelos del ORM)."""

        if self._repositorio_inyectado is None:
            self._repositorio_inyectado = RepositorioRolesUsuario()
        return self._repositorio_inyectado

    def asignar_en_alta(self, usuario: UsuarioEntity, *, actor: ContextoSesion) -> RolAsignado:
        """
        Registra la asignacion de rol del ACTO DEL ALTA (AC-ROL-01, REQ-004 RN-01/RN-04, REQ-043 RN-01).

        El rol se asigna EN EL MISMO ACTO del alta, nunca de forma diferida: no existe un estado
        intermedio en el que el usuario exista sin rol.

        POR QUE RECIBE LA ENTIDAD Y NO UN `user_id`
        --------------------------------------------
        Lo invoca el servicio de alta DENTRO de su propia `transaction.atomic()` y de su
        `contexto_de_sesion`, con la fila recien insertada en la mano. Pedirle el identificador y
        releer la fila anadiria un `SELECT` que no aporta nada y que, ademas, veria exactamente lo
        mismo. La `transaction.atomic()` de aqui es ANIDADA en ese caso -Django la resuelve como un
        SAVEPOINT, es inocua- y existe para que el metodo siga siendo correcto si algun dia se
        invoca suelto.

        Args:
            usuario: entidad del usuario RECIEN CREADA, con su `user_id` y su `role_code` ya
                escritos por el alta.
            actor: contexto de sesion del administrador que ejecuta el alta. De el -y nunca del
                payload- sale la atribucion (REQ-048, REQ-064).

        Returns:
            RolAsignado: el desenlace de la asignacion inicial.

        Raises:
            RolNoValidoError: 400, el rol no esta en `cat_rol` o esta retirado.
            RolYaAsignadoError: 409, el usuario ya tiene una asignacion de rol vigente.
        """

        role_code = usuario.role_code_id

        # `contexto_de_sesion` es lo que permite a `AtribucionMixin.save()` sellar la atribucion
        # desde la SESION y nunca desde el payload (REQ-048, REQ-064).
        with transaction.atomic():
            with contexto_de_sesion(actor):
                # GUARDIA DE ULTIMA LINEA. El serializador del alta ya valida el rol contra el catalogo,
                # pero este servicio no asume su propia proteccion: invocado desde un comando o desde
                # otro caso de uso no habria pasado por ningun serializador.
                if not self._repositorio.existe_rol_vigente(role_code):
                    raise RolNoValidoError()

                # EL ARBITRO FINAL ES LA BASE: el indice unico `ux_usuario_hist_alta` rechaza una
                # segunda entrada de alta para el mismo usuario, de modo que la invariante se sostiene
                # aunque dos altas compitan. Esta comprobacion existe para dar el 409 con un mensaje
                # util en el caso normal, no para sustituirlo (AC-ROL-02, tercer caso).
                if self._repositorio.contar_asignaciones_sin_cierre(usuario.user_id) > 0:
                    raise RolYaAsignadoError()

                # UNA sola lectura de reloj para todo el acto: el asiento del historico y el sello de la
                # fila del usuario tienen que contar la misma hora.
                instante = utc_now()

                self._repositorio.registrar_asignacion_inicial(
                    user_id=usuario.user_id,
                    role_code=role_code,
                    actor_user_id=actor.user_id,
                    instante=instante,
                )

                # AC-ROL-01 exige que `assigned_by_user_id` y `assigned_at` queden informados. Los
                # nombres FISICOS de esas dos columnas en el esquema T.5 (ARC-110) son
                # `usuario.role_changed_by` y `usuario.role_changed_at`: no existen columnas `assigned_*`
                # en la tabla y no se inventan.
                self._repositorio.sellar_asignacion_inicial_en_usuario(
                    usuario,
                    actor_user_id=actor.user_id,
                    instante=instante,
                )

        # Traza con SOLO identificadores: ni el nombre ni el correo (REQ-063, REQ-076, REQ-079).
        logger.info(
            TRAZA_ROL_ASIGNADO,
            extra={
                "data": {
                    "user_id": usuario.user_id,
                    "session_user_id": actor.user_id,
                    "role_code": role_code,
                    "outcome": "OK",
                }
            },
        )

        return RolAsignado(
            user_id=usuario.user_id,
            role_code=role_code,
            assigned_by_user_id=actor.user_id,
            assigned_at=instante,
        )

    def cambiar(self, user_id: int, datos: DatosCambioRol, *, actor: ContextoSesion) -> RolCambiado:
        """
        Cambia el rol funcional del usuario de forma TRANSACCIONAL (EP-011, AC-ROL-03, REQ-005).

        UNA SOLA TRANSACCION, QUE ES LA FRONTERA DEL CASO DE USO
        ---------------------------------------------------------
        El asiento del historico, la actualizacion del rol vigente de la fila y la marca de recarga
        de las sesiones caen JUNTOS o no cae ninguno. Si falla el historico, el rol NO se cambia: no
        puede quedar un usuario con rol nuevo y sin movimiento que lo narre.

        EL ORDEN DE LAS COMPROBACIONES ES LEY
        --------------------------------------
        Cada paso tiene su estado y su prueba, y el orden no es intercambiable:

        1. usuario inexistente -> 404;
        2. usuario de baja -> 409 (REQ-005, escenario de error 5);
        3. rol inexistente o retirado -> 400 (AC-ROL-03 de REQ-043);
        4. mismo rol que el vigente -> 409;
        5. el administrador se retira a si mismo la administracion -> 422 (REQ-043 RN-03).

        La validacion de FORMA (400) va antes que las de ESTADO (409/422) porque un rol que no
        existe ni siquiera se puede comparar con el actual. Y el 409 de «mismo rol» va antes que el
        422 de autorretirada porque un administrador que se reasigna su propio rol de administracion
        no se esta retirando nada: lo que hace es un cambio vacio.

        Args:
            user_id: usuario al que se le cambia el rol.
            datos: el rol nuevo, ya validado en su forma por el serializador.
            actor: contexto de sesion del administrador que ejecuta el cambio (REQ-048, REQ-064).

        Returns:
            RolCambiado: el desenlace publicable del cambio ya confirmado.

        Raises:
            UsuarioNoEncontradoError: 404, no existe el usuario indicado.
            UsuarioDeBajaError: 409, la cuenta esta dada de baja y no admite cambio de rol.
            RolNoValidoError: 400, el rol pedido no esta en `cat_rol` o esta retirado.
            MismoRolError: 409, el usuario ya ostenta ese rol.
            AutorretiradaRolAdministracionError: 422, el actor se retira su propio rol de
                administracion. El rol permanece INALTERADO.
        """

        # `contexto_de_sesion` es lo que permite a `AtribucionMixin.save()` sellar la atribucion
        # desde la SESION y nunca desde el payload (REQ-048, REQ-064).
        with transaction.atomic():
            with contexto_de_sesion(actor):
                # --- 1. Sujeto, con su fila BLOQUEADA ----------------------------
                # El `SELECT ... FOR UPDATE` SERIALIZA dos cambios concurrentes sobre el mismo usuario:
                # sin el, ambos leerian el mismo rol anterior y escribirian dos asientos que romperian
                # la cadena de vigencias.
                usuario = self._repositorio.obtener_usuario_para_actualizar(user_id)
                if usuario is None:
                    raise UsuarioNoEncontradoError()

                # --- 2. Estado de la cuenta (REQ-005, escenario de error 5) ------
                if usuario.status != ESTADO_USUARIO_ACTIVO:
                    raise UsuarioDeBajaError()

                # --- 3. El rol pedido tiene que existir y ser asignable ----------
                nuevo = datos.role_code
                if not self._repositorio.existe_rol_vigente(nuevo):
                    raise RolNoValidoError()

                # --- 4. Un cambio tiene que cambiar algo -------------------------
                anterior = usuario.role_code_id
                if nuevo == anterior:
                    raise MismoRolError()

                # --- 5. Nadie se retira a si mismo la administracion (RN-03) -----
                # Se llega aqui sin haber escrito NADA todavia, que es lo que garantiza que el rol del
                # actor permanezca inalterado cuando se rechaza con 422.
                if user_id == actor.user_id and anterior == ROL_ADMINISTRADOR:
                    raise AutorretiradaRolAdministracionError()

                # --- 6. Escritura: UNA sola lectura de reloj para todo ----------
                instante = utc_now()

                # La fila del historico lleva `valid_to = valid_from = instante`: el cierre de la
                # asignacion anterior y la apertura de la nueva ocurren EN EL MISMO INSTANTE, que es lo
                # que hace imposible que dos asignaciones coexistan (REQ-005 RN-02).
                self._repositorio.registrar_cambio_de_rol(
                    user_id=user_id,
                    previous_role_code=anterior,
                    new_role_code=nuevo,
                    actor_user_id=actor.user_id,
                    instante=instante,
                )

                self._repositorio.actualizar_rol_vigente(
                    usuario,
                    new_role_code=nuevo,
                    actor_user_id=actor.user_id,
                    instante=instante,
                )

                # Las sesiones vivas NO se revocan: se marcan para recargar rol y capacidades, que es lo
                # que exige AC-PERM-05 (el usuario no cierra ni reabre sesion) sin contradecir REQ-043
                # RN-05. El porque, al detalle, en `refrescar_permisos_de_sesiones`.
                refrescadas = self._repositorio.refrescar_permisos_de_sesiones(user_id, instante=instante)

        # Traza con SOLO identificadores y codigos de rol: ni el nombre ni el correo del afectado
        # (REQ-063, REQ-076, REQ-079).
        logger.info(
            TRAZA_ROL_CAMBIADO,
            extra={
                "data": {
                    "user_id": user_id,
                    "session_user_id": actor.user_id,
                    "previous_role_code": anterior,
                    "role_code": nuevo,
                    "sesiones_refrescadas": refrescadas,
                    "outcome": "OK",
                }
            },
        )

        return RolCambiado(
            user_id=user_id,
            full_name=usuario.full_name,
            role_code=nuevo,
            role_name=self._repositorio.nombre_de_rol(nuevo),
            previous_role_code=anterior,
            role_changed_at=instante,
            role_changed_by=actor.user_id,
            sesiones_refrescadas=refrescadas,
        )

    def consultar_historico(self, user_id: int, criterios: CriteriosHistoricoRol) -> PaginaHistoricoRol:
        """
        Devuelve la pagina pedida del historico de rol del usuario (EP-012, AC-ROL-05).

        ESTE METODO SOLO LEE
        --------------------
        No abre transaccion -no hay nada que hacer atomico- y no existe ninguna ruta de modificacion
        ni de borrado del historico: es append-only, y lo garantizan `RegistroInmutableMixin` en el
        modelo y el trigger `trg_usuario_historico_inmutable` en la base, que rechaza con ORA-20001
        cualquier `UPDATE` o `DELETE` sobre `usuario_historico`.

        CERO MOVIMIENTOS NO ES UN ERROR
        --------------------------------
        Un usuario sin movimientos de rol -o una pagina por encima del total- devuelve 200 con
        `items` vacio y el literal `HISTORICO_VACIO`, NUNCA un 404 ni un 422: el usuario existe y la
        consulta es valida; lo que no hay son entradas que mostrar.

        Args:
            user_id: usuario cuyo historico de rol se consulta.
            criterios: rango de fechas y paginacion, ya validados por el serializador.

        Returns:
            PaginaHistoricoRol: la pagina pedida, con su total y su total de paginas.

        Raises:
            UsuarioNoEncontradoError: 404, no existe el usuario indicado.
        """

        if self._repositorio.obtener_usuario(user_id) is None:
            raise UsuarioNoEncontradoError()

        # Se acotan igual que el repositorio para que el `page` / `page_size` publicados sean
        # exactamente los que se aplicaron al troceo y no los pedidos en bruto.
        pagina = max(1, criterios.pagina)
        tamanio = max(1, criterios.tamanio_pagina)

        filas, total = self._repositorio.historico_de_rol(
            user_id,
            desde=criterios.desde,
            hasta=criterios.hasta,
            pagina=pagina,
            tamanio_pagina=tamanio,
        )

        items = tuple(self._proyectar_entrada(fila) for fila in filas)

        # `ceil` sobre el total YA filtrado. Con total 0 el resultado es 0 paginas, que es lo que
        # un paginador necesita para no pintar «pagina 1 de 1» sobre un listado vacio.
        total_pages = ceil(total / tamanio) if total else 0

        return PaginaHistoricoRol(
            items=items,
            page=pagina,
            page_size=tamanio,
            total_count=total,
            total_pages=total_pages,
            # El literal se mira sobre la PAGINA, no sobre el total: una pagina por encima del
            # ultimo tramo tambien llega vacia y merece la misma explicacion.
            empty_message=None if items else mensajes.HISTORICO_VACIO,
        )

    def _proyectar_entrada(self, fila: UsuarioHistoricoEntity) -> EntradaHistoricoRol:
        """
        Proyecta una fila del historico a su forma publicable, sin disparar consultas extra.

        Las claves ajenas se leen por su `attname` (`previous_role_code_id`, `new_role_code_id`,
        `changed_by_id`): el atributo de la relacion traeria la fila entera del rol o del actor solo
        para quedarnos con su identificador, una consulta por campo y por entrada (N+1).

        El nombre del actor si sale de la relacion, pero el repositorio ya la resolvio en el MISMO
        `SELECT` con `select_related("changed_by")`, de modo que leerlo no anade ninguna consulta.

        Args:
            fila: entrada de `usuario_historico` de tipo ROL.

        Returns:
            EntradaHistoricoRol: la entrada proyectada.
        """

        actor = fila.changed_by
        return EntradaHistoricoRol(
            history_id=fila.history_id,
            user_id=fila.user_id,
            previous_role_code=fila.previous_role_code_id,
            new_role_code=fila.new_role_code_id,
            changed_by_user_id=fila.changed_by_id,
            changed_by_display_name=actor.full_name if actor is not None else None,
            changed_at=fila.changed_at,
            valid_from=fila.valid_from,
            valid_to=fila.valid_to,
        )


__all__ = [
    "CriteriosHistoricoRol",
    "DatosCambioRol",
    "EntradaHistoricoRol",
    "PaginaHistoricoRol",
    "RolAsignado",
    "RolCambiado",
    "ServicioRolUsuario",
]
