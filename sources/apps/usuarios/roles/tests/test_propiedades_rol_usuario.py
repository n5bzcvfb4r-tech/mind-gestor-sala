"""
Pruebas basadas en propiedades del rol funcional del usuario (PBT-003).

Implementan PBT-003 del catalogo TEST_SUITES: «Para toda secuencia de altas y cambios de rol cuando
se aplica sobre un usuario entonces este tiene exactamente un rol vigente y su vigencia es continua
y sin solapes». Lo que se cuantifica es una SECUENCIA de operaciones generada por Hypothesis -no una
lista de casos escogidos a mano-, porque la invariante de REQ-005 RN-02 / AC-ROL-01 no habla de un
cambio concreto sino de cualquier historia posible del usuario.

POR QUE ESTA PROPIEDAD NO TOCA ORACLE
-------------------------------------
Por dos motivos distintos y ambos suficientes:

1. La plataforma NO ha podido levantar el entorno de prueba de esta sesion: `.mind/TSK-071/env.json`
   trae `status: unavailable` porque no hay ningun engine Docker alcanzable, de modo que no existe
   el contenedor `gvenzl/oracle-free:23-slim` contra el que ejecutar nada.
2. Aunque lo hubiera, una propiedad ejecuta CIENTOS de secuencias de hasta 15 operaciones cada una.
   Lanzar cada secuencia contra un contenedor -con sus INSERT, su `SELECT ... FOR UPDATE` y su
   rollback- es inviable como prueba de desarrollo.

LA EVIDENCIA CONTRA EL MOTOR REAL QUEDA DIFERIDA AL CI
-------------------------------------------------------
Que la base rechace de verdad una segunda entrada de alta, que las CHECK de coherencia esten de
verdad creadas y que la consulta de solapamiento del changeset `ddl-0.0.1-07-06` devuelva de verdad
0 filas son hechos del MOTOR, y se acreditan con las pruebas marcadas `integration` cuando el CI
disponga de Docker. Aqui se cubre la invariante algebraica sobre el espacio de secuencias, que es lo
que ninguna prueba contra contenedor puede cubrir por volumen.

EL DOBLE REPLICA LA SEMANTICA QUE IMPONE ORACLE, NO UNA PARECIDA
------------------------------------------------------------------
`RepositorioRolesFalso` no es un repositorio «comodo»: reproduce LITERALMENTE las reglas que el
esquema impone sobre `usuario_historico` (`facilities/changelogs/0.0.1/ddl/07-usuario-historico.xml`)
y, cuando una fila las viola, lanza el mismo `IntegrityError` que devolveria el motor nombrando la
restriccion violada:

* `ck_usuario_hist_coherencia`: en una fila de rol, `new_role_code` NO puede ser nulo; si
  `previous_role_code` es nulo entonces `valid_to` tiene que ser NULO, y si no lo es entonces
  `valid_to` tiene que ser IGUAL a `valid_from`.
* `ck_usuario_hist_valor_distinto`: `previous_role_code` nulo o DISTINTO de `new_role_code`.
* `ux_usuario_hist_alta`: como mucho UNA fila por usuario sin valor anterior.
* `ux_usuario_hist_rol_sin_cierre`: como mucho UNA fila de rol por usuario con `valid_to` nulo.

Si el doble decidiera estas reglas de otra forma -o se limitase a acumular filas-, la propiedad no
estaria midiendo el sistema que se despliega: estaria midiendo el doble.

LO QUE SE EJERCITA ES EL SERVICIO REAL
----------------------------------------
Se instancia `ServicioRolUsuario` TAL CUAL, sin tocar su codigo y con el doble inyectado por
constructor. La politica que esta propiedad pone a prueba -una sola lectura de reloj por acto, el
asiento del historico con `valid_to = valid_from`, la actualizacion del rol vigente de la fila del
usuario y el orden de las comprobaciones- vive integra en `servicio.py` + `repositorio.py`, NO en el
SQL: el SQL solo la hace cumplir. Por eso la invariante es observable sin motor.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.db.utils import IntegrityError
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule, run_state_machine_as_test

from apps.core.contexto import AlcanceDatos, ContextoSesion
from apps.usuarios.roles import servicio as modulo_servicio
from apps.usuarios.roles.errores import MismoRolError
from apps.usuarios.roles.servicio import DatosCambioRol, ServicioRolUsuario


#: Catalogo CERRADO de roles: son las tres filas que siembra
#: `facilities/changelogs/0.0.1/dml/01-seed-cat-seguridad.xml` en `cat_rol`. Es PRECONDICION del
#: escenario de PBT-003 que todo rol destino pertenezca al catalogo, asi que no se genera ninguno
#: fuera de el y el doble no tiene que modelar el rechazo de un rol inexistente.
ROLES_DEL_CATALOGO: tuple[str, ...] = ("EMPLEADO", "TECNICO_MANTENIMIENTO", "ADMINISTRADOR")

#: Identificador del ADMINISTRADOR que ejecuta cada operacion de la secuencia (alta y cambios).
USER_ID_ACTOR = 1

#: Identificador del usuario DESTINO. Es SIEMPRE distinto de `USER_ID_ACTOR` -precondicion de
#: PBT-003-, lo que ademas deja fuera del escenario la regla de autorretirada de REQ-043 RN-03: esa
#: rama tiene sus propias pruebas y aqui enmascararia la invariante de vigencia.
USER_ID_DESTINO = 1000

#: Valor de `usuario_historico.change_type` de las entradas de rol, del CHECK cerrado
#: `ck_usuario_hist_change_type` (`'ROL'` | `'ESTADO'`). Se repite aqui, y no se importa del
#: repositorio, para que el doble no herede del codigo bajo prueba el literal que la base impone.
CAMBIO_DE_ROL = "ROL"

#: Textos de las violaciones que el doble reproduce. Nombran la restriccion EXACTA del DDL, igual
#: que el ORA-02290 (CHECK) y el ORA-00001 (indice unico) que devolveria Oracle: un `IntegrityError`
#: sin el nombre de la restriccion no permitiria distinguir que invariante se ha roto.
ORA_CK_COHERENCIA = "ORA-02290: check constraint (FACILITIES.CK_USUARIO_HIST_COHERENCIA) violated"
ORA_CK_VALOR_DISTINTO = "ORA-02290: check constraint (FACILITIES.CK_USUARIO_HIST_VALOR_DISTINTO) violated"
ORA_UX_ALTA = "ORA-00001: unique constraint (FACILITIES.UX_USUARIO_HIST_ALTA) violated"
ORA_UX_ROL_SIN_CIERRE = "ORA-00001: unique constraint (FACILITIES.UX_USUARIO_HIST_ROL_SIN_CIERRE) violated"


@dataclass
class FilaUsuarioFalsa:
    """
    Doble de `UsuarioEntity` con los campos que el servicio de rol LEE y ESCRIBE.

    Los nombres son los `attname` REALES del modelo y no alias comodos: `role_code_id` (y no
    `role_code`) porque tanto el servicio como el repositorio leen y escriben el `attname` de la
    clave ajena para no disparar una consulta extra, y `role_changed_by_id` por el mismo motivo. Es
    la misma disciplina que `FichaUsuario` en PBT-001: si este doble renombrase algo, la propiedad
    fallaria por un motivo que no existe en produccion -o, peor, pasaria sin probar el campo real-.
    """

    user_id: int
    full_name: str
    role_code_id: str
    status: str
    role_changed_at: datetime | None = None
    role_changed_by_id: int | None = None


@dataclass
class ActorFalso:
    """
    Doble minimo de la fila de `usuario` referenciada por `usuario_historico.changed_by`.

    Existe porque `ServicioRolUsuario._proyectar_entrada` lee `fila.changed_by.full_name` para
    rotular quien hizo el cambio; el repositorio real lo resuelve en el MISMO `SELECT` con
    `select_related("changed_by")`. Solo publica `full_name`: el resto de la fila del actor no lo
    toca nadie en este caso de uso.
    """

    full_name: str


@dataclass
class FilaHistoricoFalsa:
    """
    Doble de `UsuarioHistoricoEntity` acotado a las entradas de ROL.

    Los nombres son, de nuevo, los `attname` REALES: `previous_role_code_id`, `new_role_code_id` y
    `changed_by_id` son lo que lee `_proyectar_entrada`, que nunca navega la relacion para quedarse
    con un identificador. `changed_by` SI es la relacion, porque de ahi sale el nombre publicable
    del actor; es el unico atributo de relacion que el servicio consulta.

    Las columnas de la dimension de ESTADO (`previous_status`, `new_status`, `reason_code`) no estan:
    en una fila de rol van siempre vacias -lo exigen `ck_usuario_hist_coherencia` y
    `ck_usuario_hist_motivo`- y modelarlas invitaria a escribirlas.
    """

    history_id: int
    user_id: int
    change_type: str
    previous_role_code_id: str | None
    new_role_code_id: str | None
    valid_from: datetime
    valid_to: datetime | None
    changed_by_id: int
    changed_at: datetime
    changed_by: ActorFalso | None = None


@dataclass
class RepositorioRolesFalso:
    """
    Doble EN MEMORIA de `RepositorioRolesUsuario`, con sus mismos nombres y firmas publicas.

    Los parametros keyword-only se respetan uno a uno: el servicio invoca todo lo que escribe con
    `*`, y un doble con firmas posicionales dejaria pasar un cambio de orden de argumentos que en
    produccion seria un `TypeError`.

    EL BLOQUEO DE FILA NO SE MODELA, Y NO HACE FALTA
    -------------------------------------------------
    `obtener_usuario_para_actualizar` devuelve lo mismo que `obtener_usuario`: el
    `SELECT ... FOR UPDATE` no tiene equivalente en memoria y esta propiedad es SECUENCIAL, no
    concurrente. Las operaciones se aplican una tras otra sobre el mismo usuario, de modo que no hay
    ninguna carrera que serializar. La serializacion de cambios simultaneos es un hecho del motor y
    se acredita con las pruebas de integracion.

    LAS INVARIANTES DEL ESQUEMA LAS HACE CUMPLIR ESTE DOBLE
    --------------------------------------------------------
    Los dos metodos de escritura del historico validan la fila ANTES de anadirla y lanzan
    `IntegrityError` si viola `ck_usuario_hist_coherencia`, `ck_usuario_hist_valor_distinto`,
    `ux_usuario_hist_alta` o `ux_usuario_hist_rol_sin_cierre`. Ver el docstring del modulo.
    """

    usuario: FilaUsuarioFalsa
    historico: list[FilaHistoricoFalsa] = field(default_factory=list)
    refrescos: list[datetime] = field(default_factory=list)

    # --- Lecturas de la fila del usuario --------------------------------------------------------

    def obtener_usuario(self, user_id: int) -> FilaUsuarioFalsa | None:
        """Devuelve la fila del usuario destino si el identificador coincide, o `None` si no existe."""

        return self.usuario if self.usuario.user_id == user_id else None

    def obtener_usuario_para_actualizar(self, user_id: int) -> FilaUsuarioFalsa | None:
        """Igual que `obtener_usuario`: el bloqueo de fila no se modela (ver el docstring de la clase)."""

        return self.obtener_usuario(user_id)

    # --- Catalogo de roles ----------------------------------------------------------------------

    def existe_rol_vigente(self, role_code: str) -> bool:
        """`True` para los tres roles del catalogo cerrado sembrado en `cat_rol`, `False` fuera de el."""

        return role_code in ROLES_DEL_CATALOGO

    def nombre_de_rol(self, role_code: str) -> str | None:
        """
        Denominacion del rol en el catalogo, o `None` si el codigo no esta.

        El valor concreto es irrelevante para la invariante -es un dato de PRESENTACION- pero tiene
        que ser ESTABLE: el servicio lo publica en `RolCambiado.role_name` y una etiqueta cambiante
        haria fallar comparaciones por un motivo ajeno a la vigencia.
        """

        return f"Rol {role_code}" if role_code in ROLES_DEL_CATALOGO else None

    # --- Lecturas de la cadena de vigencias -----------------------------------------------------

    def _filas_de_rol(self, user_id: int) -> list[FilaHistoricoFalsa]:
        """Entradas de tipo ROL del usuario; las de ESTADO viven en la misma tabla y no mueven el rol."""

        return [fila for fila in self.historico if fila.user_id == user_id and fila.change_type == CAMBIO_DE_ROL]

    @staticmethod
    def _orden_total(fila: FilaHistoricoFalsa) -> tuple[datetime, int]:
        """
        Orden total `(valid_from, history_id)` de la cadena de vigencias.

        El desempate por `history_id` NO es decorativo: dos cambios pueden compartir `valid_from`
        -la semantica de T.5 es que el cierre de la asignacion anterior y la apertura de la nueva
        ocurren a la vez- y sin el la nocion de «posterior» dependeria del orden fisico de las filas,
        que Oracle no garantiza. Es el mismo orden que usa `ddl-0.0.1-07-06`.
        """

        return (fila.valid_from, fila.history_id)

    def contar_asignaciones_sin_cierre(self, user_id: int) -> int:
        """
        Cuenta las asignaciones de rol que NINGUNA fila posterior ha cerrado.

        Replica la semantica del metodo real -`EXISTS` correlacionado con el orden total-, no un
        atajo: una asignacion esta vigente si y solo si su fila no tiene ninguna otra fila de rol del
        mismo usuario ESTRICTAMENTE POSTERIOR en `(valid_from, history_id)`. Mirar `valid_to IS NULL`
        responderia a una pregunta distinta y daria 1 siempre, cegando la invariante.
        """

        filas = self._filas_de_rol(user_id)
        return sum(1 for fila in filas if not any(self._orden_total(otra) > self._orden_total(fila) for otra in filas))

    def rol_vigente_en_historico(self, user_id: int) -> str | None:
        """`new_role_code` de la fila de rol con `(valid_from, history_id)` maximo, o `None` si no hay ninguna."""

        filas = self._filas_de_rol(user_id)
        if not filas:
            return None
        return max(filas, key=self._orden_total).new_role_code_id

    # --- Escrituras del historico ---------------------------------------------------------------

    def _validar_restricciones(self, fila: FilaHistoricoFalsa) -> None:
        """
        Aplica las reglas de la BASE sobre una fila de rol antes de anadirla, como haria el motor.

        Las cuatro reglas estan transcritas del DDL y en el mismo orden en que Oracle las evaluaria:
        primero las CHECK de la propia fila y despues los indices unicos, que miran la tabla entera.

        Raises:
            IntegrityError: con el texto de la restriccion violada.
        """

        # `ck_usuario_hist_coherencia`, rama `change_type = 'ROL'`.
        if fila.new_role_code_id is None:
            raise IntegrityError(ORA_CK_COHERENCIA)
        if fila.previous_role_code_id is None:
            if fila.valid_to is not None:
                raise IntegrityError(ORA_CK_COHERENCIA)
        elif fila.valid_to != fila.valid_from:
            raise IntegrityError(ORA_CK_COHERENCIA)

        # `ck_usuario_hist_valor_distinto`: un cambio tiene que cambiar algo.
        if fila.previous_role_code_id is not None and fila.previous_role_code_id == fila.new_role_code_id:
            raise IntegrityError(ORA_CK_VALOR_DISTINTO)

        propias = [otra for otra in self.historico if otra.user_id == fila.user_id]

        # `ux_usuario_hist_alta`: como mucho UNA fila por usuario sin valor anterior.
        if fila.previous_role_code_id is None and any(otra.previous_role_code_id is None for otra in propias):
            raise IntegrityError(ORA_UX_ALTA)

        # `ux_usuario_hist_rol_sin_cierre`: como mucho UNA fila de rol por usuario sin cierre.
        sin_cierre = any(otra.change_type == CAMBIO_DE_ROL and otra.valid_to is None for otra in propias)
        if fila.change_type == CAMBIO_DE_ROL and fila.valid_to is None and sin_cierre:
            raise IntegrityError(ORA_UX_ROL_SIN_CIERRE)

    def _anadir(self, fila: FilaHistoricoFalsa) -> FilaHistoricoFalsa:
        """Valida la fila contra las restricciones y la persiste con su `history_id` correlativo."""

        self._validar_restricciones(fila)
        self.historico.append(fila)
        return fila

    def _siguiente_history_id(self) -> int:
        """`history_id` correlativo y creciente, como la IDENTITY de Oracle: nunca reutiliza valores."""

        return len(self.historico) + 1

    def registrar_asignacion_inicial(
        self,
        *,
        user_id: int,
        role_code: str,
        actor_user_id: int,
        instante: datetime,
    ) -> FilaHistoricoFalsa:
        """Entrada de ALTA: `previous_role_code` NULO y `valid_to` NULO, la unica forma que admite la CHECK."""

        return self._anadir(
            FilaHistoricoFalsa(
                history_id=self._siguiente_history_id(),
                user_id=user_id,
                change_type=CAMBIO_DE_ROL,
                previous_role_code_id=None,
                new_role_code_id=role_code,
                valid_from=instante,
                valid_to=None,
                changed_by_id=actor_user_id,
                changed_at=instante,
                changed_by=ActorFalso(full_name="Administrador de prueba"),
            )
        )

    def registrar_cambio_de_rol(
        self,
        *,
        user_id: int,
        previous_role_code: str,
        new_role_code: str,
        actor_user_id: int,
        instante: datetime,
    ) -> FilaHistoricoFalsa:
        """
        Entrada de CAMBIO: `valid_to = valid_from = instante`, el MISMO objeto en las dos columnas.

        No son dos lecturas de reloj: con dos instantes distintos la cadena tendria un hueco -o un
        solapamiento- de microsegundos y la consulta del changeset `ddl-0.0.1-07-06` dejaria de
        devolver 0 filas.
        """

        return self._anadir(
            FilaHistoricoFalsa(
                history_id=self._siguiente_history_id(),
                user_id=user_id,
                change_type=CAMBIO_DE_ROL,
                previous_role_code_id=previous_role_code,
                new_role_code_id=new_role_code,
                valid_from=instante,
                valid_to=instante,
                changed_by_id=actor_user_id,
                changed_at=instante,
                changed_by=ActorFalso(full_name="Administrador de prueba"),
            )
        )

    # --- Escrituras de la fila del usuario ------------------------------------------------------

    def actualizar_rol_vigente(
        self,
        usuario: FilaUsuarioFalsa,
        *,
        new_role_code: str,
        actor_user_id: int,
        instante: datetime,
    ) -> None:
        """Escribe en la fila del usuario los MISMOS campos que el repositorio real: rol vigente y sello del cambio."""

        usuario.role_code_id = new_role_code
        usuario.role_changed_at = instante
        usuario.role_changed_by_id = actor_user_id

    def sellar_asignacion_inicial_en_usuario(
        self,
        usuario: FilaUsuarioFalsa,
        *,
        actor_user_id: int,
        instante: datetime,
    ) -> None:
        """Sella quien asigno el rol INICIAL y cuando; el `role_code` ya lo escribio el alta y no se toca."""

        usuario.role_changed_at = instante
        usuario.role_changed_by_id = actor_user_id

    # --- Consulta del historico -----------------------------------------------------------------

    def historico_de_rol(
        self,
        user_id: int,
        *,
        desde: date | None,
        hasta: date | None,
        pagina: int,
        tamanio_pagina: int,
    ) -> tuple[list[FilaHistoricoFalsa], int]:
        """
        Pagina del historico de ROL y total de la consulta filtrada COMPLETA.

        Reproduce los tres hechos del metodo real que el servicio observa: el limite superior es
        `changed_at <` la medianoche del dia SIGUIENTE -`hasta` es inclusivo por dia-, el orden es
        `(-changed_at, -history_id)` y el `total` se calcula ANTES de trocear.
        """

        filas = self._filas_de_rol(user_id)
        if desde is not None:
            filas = [fila for fila in filas if fila.changed_at >= datetime.combine(desde, time.min)]
        if hasta is not None:
            limite = datetime.combine(hasta + timedelta(days=1), time.min)
            filas = [fila for fila in filas if fila.changed_at < limite]

        total = len(filas)
        filas.sort(key=lambda fila: (fila.changed_at, fila.history_id), reverse=True)

        tamanio = max(1, tamanio_pagina)
        inicio = (max(1, pagina) - 1) * tamanio
        return filas[inicio : inicio + tamanio], total

    # --- Sesiones -------------------------------------------------------------------------------

    def refrescar_permisos_de_sesiones(self, user_id: int, *, instante: datetime) -> int:
        """
        Anota la llamada y devuelve 0: en esta propiedad el usuario destino no tiene sesiones abiertas.

        Devolver 0 NO es un fallo y el servicio no lo trata como tal (`RolCambiado.sesiones_refrescadas`
        admite 0 explicitamente): lo que acredita el marcado de sesiones vivas es AC-PERM-05, que
        tiene sus propias pruebas. Aqui se anota el instante para poder afirmar que el servicio pide
        el refresco con el MISMO instante del asiento, que si es parte de la coherencia temporal.
        """

        self.refrescos.append(instante)
        return 0


#: Instante de arranque del reloj controlado. El valor concreto es irrelevante para la invariante
#: -lo que importa es el ORDEN entre marcas-, pero tiene que ser FIJO para que un contraejemplo de
#: Hypothesis se pueda reproducir tal cual en otra maquina y otro dia.
INSTANTE_INICIAL = datetime(2026, 1, 1, 8, 0, 0)

#: Deltas entre dos actos consecutivos de la secuencia. El microsegundo es el dato de prueba que el
#: catalogo EXIGE -«instantes muy proximos entre si»- y es justo donde la contiguidad se rompe si el
#: orden de la cadena no desempata por `history_id`; los otros dos cubren el caso holgado.
AVANCES_DEL_RELOJ = (timedelta(microseconds=1), timedelta(milliseconds=1), timedelta(seconds=1))


class RelojFalso:
    """
    Fuente de marca temporal CONTROLADA que sustituye a `utc_now()` dentro del servicio.

    POR QUE NO VALE EL RELOJ DEL SISTEMA
    -------------------------------------
    `utc_now()` lee `datetime.now()`, y dos llamadas consecutivas pueden devolver EL MISMO valor: la
    resolucion efectiva del reloj no garantiza avanzar entre dos operaciones que se ejecutan sin
    base de datos de por medio. El catalogo pide, en cambio, instantes ESTRICTAMENTE CRECIENTES y,
    en parte de los casos, MUY PROXIMOS entre si -microsegundos-, que es exactamente el punto donde
    la contiguidad de vigencias se rompe si el desempate por `history_id` del orden total no esta
    bien hecho. Con el reloj del sistema la propiedad no seria reproducible -el mismo contraejemplo
    dejaria de fallar en la ejecucion siguiente- y, ademas, no cubriria el caso proximo: nunca se
    sabria si dos asientos cayeron en el mismo microsegundo o a medio segundo de distancia.

    Cada llamada devuelve un instante estrictamente mayor que el anterior, avanzando el delta que la
    secuencia generada haya fijado con `avanzar`.
    """

    def __init__(self, inicio: datetime = INSTANTE_INICIAL) -> None:
        self._actual = inicio
        self._delta = AVANCES_DEL_RELOJ[0]

    def avanzar(self, delta: timedelta) -> None:
        """Fija el salto de la SIGUIENTE lectura; nunca por debajo del microsegundo (el reloj no se para)."""

        self._delta = max(delta, timedelta(microseconds=1))

    def __call__(self) -> datetime:
        """Marca temporal actual, estrictamente posterior a la devuelta en la llamada anterior."""

        self._actual = self._actual + self._delta
        return self._actual


class TransaccionFalsa:
    """
    Doble del modulo `django.db.transaction` para el `with transaction.atomic()` del servicio.

    NO hay conexion a Oracle en esta sesion, asi que el `atomic()` real falla al abrirla
    (`DPY-6005: cannot connect to database`) y la propiedad no llegaria a ejecutarse nunca. Este
    doble reproduce lo UNICO que la invariante necesita del bloque transaccional: que si algo
    revienta dentro, lo escrito en memoria -el historico y la fila del usuario- vuelva a como estaba
    (ROLLBACK).

    Esa fidelidad no es decorativa: es lo que permite afirmar que una operacion RECHAZADA -el 409 de
    mismo rol que esta propiedad genera a proposito, o el 422 de autorretirada que vive en otras
    pruebas- NO deja ningun asiento a medias ni toca el rol vigente de la fila del usuario.
    """

    def __init__(self, repositorio: RepositorioRolesFalso) -> None:
        self._repositorio = repositorio

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """Bloque atomico en memoria: confirma al salir bien y deshace historico y fila de usuario si sale una excepcion."""

        historico = list(self._repositorio.historico)
        usuario = self._repositorio.usuario
        sello = (usuario.role_code_id, usuario.role_changed_at, usuario.role_changed_by_id)
        try:
            yield
        except Exception:
            self._repositorio.historico[:] = historico
            usuario.role_code_id, usuario.role_changed_at, usuario.role_changed_by_id = sello
            raise


class MaquinaRolVigente(RuleBasedStateMachine):
    """
    Maquina de estados de PBT-003: un usuario, un ADMINISTRADOR y una secuencia de cambios de rol.

    Hypothesis genera la SECUENCIA -«de 1 a 15 operaciones», un alta inicial y cambios posteriores-,
    aplica cada paso sobre el servicio REAL y, DESPUES DE CADA PASO, comprueba las seis invariantes
    de abajo. Esa comprobacion en cada punto intermedio es lo que distingue esta prueba de una de
    ejemplo: la invariante de REQ-005 RN-02 / AC-ROL-01 no habla de un cambio concreto sino de
    cualquier historia posible del usuario.

    EL ROL INICIAL ES FIJO, Y ESTA JUSTIFICADO
    --------------------------------------------
    Hypothesis no admite `draw` en `__init__`, asi que el rol de partida se deja FIJO en
    `ROLES_DEL_CATALOGO[0]` en lugar de sortearlo (sortearlo con `random` romperia la reproducibilidad
    del contraejemplo, que es el motivo por el que Hypothesis lo prohibe). No se pierde cobertura: el
    espacio de roles vigentes lo recorren los CAMBIOS, que si se generan con `st.sampled_from` sobre
    el catalogo completo, de modo que toda secuencia de longitud >= 2 pasa por roles distintos del
    inicial. La alternativa correcta seria un `@initialize(role_code=...)`, pero aqui no sirve: el
    rol del alta sale de `usuario.role_code_id` -ver mas abajo- y habria que escribir la fila del
    usuario ANTES de que el servicio la lea, lo que equivale a lo mismo con mas maquinaria.

    POR QUE EL ROL DE LA FILA Y EL DEL ALTA SON EL MISMO POR CONSTRUCCION
    ----------------------------------------------------------------------
    `ServicioRolUsuario.asignar_en_alta` LEE el rol de `usuario.role_code_id` y no lo reescribe: el
    alta ya lo dejo puesto en la fila y el servicio solo sella quien y cuando. Por eso el estado
    inicial de la maquina es coherente sin tocar nada.
    """

    def __init__(self) -> None:
        super().__init__()

        self.usuario = FilaUsuarioFalsa(
            user_id=USER_ID_DESTINO,
            full_name="Usuario destino de prueba",
            role_code_id=ROLES_DEL_CATALOGO[0],
            status="ACTIVO",
        )
        self.repositorio = RepositorioRolesFalso(usuario=self.usuario)
        self.reloj = RelojFalso()

        # Los dos dobles se instalan SOLO en el espacio de nombres del servicio -`utc_now` y
        # `transaction` estan importados ahi-, nunca de forma global: un parche sobre
        # `apps.core.contexto.utc_now` alteraria el reloj de todo el proceso y contaminaria
        # cualquier otra prueba de la misma sesion.
        self._parches = [
            patch.object(modulo_servicio, "utc_now", self.reloj),
            patch.object(modulo_servicio, "transaction", TransaccionFalsa(self.repositorio)),
        ]
        for parche in self._parches:
            parche.start()

        # PRECONDICION del catalogo: cada operacion la ejecuta un ADMINISTRADOR DISTINTO del usuario
        # destino. De ahi que el 422 de autorretirada de REQ-043 RN-03 no pueda aparecer en esta
        # propiedad: esa rama tiene sus propias pruebas y aqui enmascararia la invariante.
        assert USER_ID_ACTOR != USER_ID_DESTINO
        self.actor = ContextoSesion(
            user_id=USER_ID_ACTOR,
            role_code="ADMINISTRADOR",
            session_id="33333333-3333-4333-8333-333333333333",
            data_scope=AlcanceDatos.ALL.value,
            display_name="Administrador de prueba",
        )

        # El servicio es el REAL, sin tocar su codigo: solo se le inyecta el doble de repositorio.
        self.servicio = ServicioRolUsuario(repositorio=self.repositorio)

        #: Operaciones ACEPTADAS de la secuencia. Las rechazadas no cuentan: el catalogo pide «una
        #: entrada de historico por cada cambio ACEPTADO».
        self.operaciones_aceptadas = 0

    @initialize()
    def dar_de_alta(self) -> None:
        """Primer elemento de toda secuencia: el alta con su asignacion de rol inicial (AC-ROL-01)."""

        self.servicio.asignar_en_alta(self.usuario, actor=self.actor)
        self.operaciones_aceptadas += 1

    @rule(role_code=st.sampled_from(ROLES_DEL_CATALOGO), avance=st.sampled_from(AVANCES_DEL_RELOJ))
    def cambiar_rol(self, role_code: str, avance: timedelta) -> None:
        """
        Un cambio de rol de la secuencia, ejecutado por el ADMINISTRADOR sobre el usuario destino.

        El rol destino se muestrea del catalogo CERRADO completo, lo que incluye por construccion
        REPETICIONES DEL ROL VIGENTE: es un dato de prueba EXIGIDO por el catalogo, y la operacion
        que produce -un 409 `MismoRolError`- es justamente la que acredita que un rechazo no deja
        asiento ni mueve la cadena.
        """

        self.reloj.avanzar(avance)
        try:
            self.servicio.cambiar(USER_ID_DESTINO, DatosCambioRol(role_code=role_code), actor=self.actor)
        except MismoRolError:
            # Unica excepcion esperada en este escenario, y NO suma: una operacion rechazada no es
            # una operacion aceptada. Cualquier otra se propaga y hace fallar la propiedad.
            return
        self.operaciones_aceptadas += 1

    # --- Invariantes: una por cada «Then» del escenario de PBT-003 ------------------------------

    def _cadena(self) -> list[FilaHistoricoFalsa]:
        """Filas de rol del usuario destino en el orden total `(valid_from, history_id)` de la cadena."""

        filas = self.repositorio._filas_de_rol(USER_ID_DESTINO)
        return sorted(filas, key=lambda fila: (fila.valid_from, fila.history_id))

    @invariant()
    def exactamente_una_asignacion_vigente(self) -> None:
        """
        «tras cada paso el usuario tiene exactamente una asignacion de rol vigente».

        Es el oraculo literal de AC-ROL-01 y REQ-005 RN-02, y se mide con la MISMA semantica que la
        consulta de solapamiento del changeset `ddl-0.0.1-07-06`: vigente es la fila que ninguna otra
        posterior ha cerrado.
        """

        assert self.repositorio.contar_asignaciones_sin_cierre(USER_ID_DESTINO) == 1

    @invariant()
    def vigencias_contiguas_sin_solape_ni_hueco(self) -> None:
        """
        «el instante de cierre de cada asignacion coincide con el de apertura de la siguiente, sin solape ni hueco».

        Cada fila posterior al alta hace las dos cosas a la vez: CIERRA la asignacion abierta por la
        fila anterior con su `valid_to` y ABRE la suya con su `valid_from`. Que ambos sean el mismo
        instante es lo que hace imposible el solape (`valid_to > valid_from` siguiente) y el hueco
        (`valid_to < valid_from` siguiente).

        Se comprueba ademas la forma de la entrada de ALTA (REQ-008 RN-02): es la PRIMERA de la
        cadena, es la UNICA sin cierre y es la UNICA sin rol anterior.
        """

        cadena = self._cadena()
        assert cadena, "tras el alta la cadena nunca puede estar vacia"

        for anterior, siguiente in zip(cadena, cadena[1:]):
            assert siguiente.valid_to == siguiente.valid_from
            assert siguiente.valid_from >= anterior.valid_from

        sin_cierre = [fila for fila in cadena if fila.valid_to is None]
        assert sin_cierre == [cadena[0]]

        sin_rol_anterior = [fila for fila in cadena if fila.previous_role_code_id is None]
        assert sin_rol_anterior == [cadena[0]]

    @invariant()
    def una_entrada_de_historico_por_operacion_aceptada(self) -> None:
        """
        «el historico contiene una entrada por cada cambio aceptado».

        Ni una menos -cada acto aceptado deja su asiento- ni una mas: un 409 de mismo rol revienta
        dentro de la transaccion y el rollback no deja rastro.
        """

        assert len(self._cadena()) == self.operaciones_aceptadas

    @invariant()
    def cada_entrada_lleva_autor_y_fecha(self) -> None:
        """
        «... con autor y fecha».

        El autor sale SIEMPRE de la sesion y nunca del payload (REQ-004 RN-04, REQ-064): por eso se
        exige el identificador del ADMINISTRADOR de la sesion, no uno cualquiera informado.
        """

        for fila in self._cadena():
            assert fila.changed_by_id == USER_ID_ACTOR
            assert fila.changed_at is not None

    @invariant()
    def el_rol_vigente_coincide_en_las_dos_caras(self) -> None:
        """
        La fila de `usuario` y la cadena del historico no pueden divergir.

        El rol vigente se materializa DOS veces -`usuario.role_code` y la ultima fila de rol del
        historico- y el cambio escribe ambas dentro de la misma transaccion. Si divergieran, la
        respuesta de la API y el historico publicado contarian historias distintas.
        """

        assert self.repositorio.rol_vigente_en_historico(USER_ID_DESTINO) == self.usuario.role_code_id

    @invariant()
    def la_cadena_encadena_los_roles(self) -> None:
        """
        Cada eslabon arranca donde termino el anterior, y ningun eslabon es un cambio vacio.

        `siguiente.previous_role_code` tiene que ser el `new_role_code` de la fila anterior -si no,
        la cadena tendria un salto que ningun movimiento narra- y nunca puede coincidir con su
        propio `new_role_code`, que es lo que prohibe `ck_usuario_hist_valor_distinto`.
        """

        cadena = self._cadena()
        for anterior, siguiente in zip(cadena, cadena[1:]):
            assert siguiente.previous_role_code_id == anterior.new_role_code_id

        for fila in cadena:
            assert fila.previous_role_code_id != fila.new_role_code_id

    def teardown(self) -> None:
        """Retira los dos parches instalados en el servicio, pase lo que pase durante la secuencia."""

        for parche in reversed(self._parches):
            parche.stop()


#: Ajustes de la propiedad. Se declaran como OBJETO y se pasan a `run_state_machine_as_test` en vez
#: de decorar la prueba con `@settings`: ese decorador sobre una funcion sin `@given` lo rechaza la
#: propia Hypothesis (`InvalidArgument`), y el parametro `settings=` es el mecanismo que la libreria
#: publica para una maquina de estados.
#:
#: * `stateful_step_count=15` materializa «secuencias de 1 a 15 operaciones» del catalogo: es el
#:   MAXIMO de pasos, y Hypothesis genera secuencias de longitud 1 hasta ese tope.
#: * `max_examples=80` recorre el espacio de secuencias en pocos segundos: no hay base de datos de
#:   por medio, de modo que el coste por ejemplo es el de unas decenas de objetos en memoria.
#: * `deadline=None` porque el tiempo por paso NO es la propiedad: lo que se mide es la invariante
#:   algebraica, y un pico de latencia de la maquina de CI no es un contraejemplo.
#: * `HealthCheck.too_slow` se suprime por el mismo motivo: la instalacion de los dos parches en
#:   cada ejemplo es trabajo de preparacion, no de generacion de datos.
AJUSTES_PBT_003 = settings(
    max_examples=80,
    stateful_step_count=15,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def test_PBT_003_el_usuario_tiene_siempre_exactamente_un_rol_vigente_sin_solapes_ni_huecos() -> None:
    """
    PBT-003: «Una persona tiene siempre exactamente un rol vigente, haga los cambios que haga».

    Para toda secuencia generada -un alta inicial y de 0 a 14 cambios posteriores ejecutados por un
    ADMINISTRADOR distinto del usuario destino, con roles del catalogo cerrado INCLUIDAS las
    repeticiones del rol vigente y con instantes estrictamente crecientes, en parte de los casos
    separados por un solo microsegundo- y en CUALQUIER punto intermedio de ella, se comprueban las
    seis invariantes de `MaquinaRolVigente`:

    1. `exactamente_una_asignacion_vigente`: el numero de asignaciones vigentes es exactamente 1.
    2. `vigencias_contiguas_sin_solape_ni_hueco`: el cierre de cada asignacion coincide con la
       apertura de la siguiente, la cadena es monotona y solo la entrada de alta queda sin cerrar.
    3. `una_entrada_de_historico_por_operacion_aceptada`: tantas entradas como actos aceptados.
    4. `cada_entrada_lleva_autor_y_fecha`: autor tomado de la SESION y fecha informada.
    5. `el_rol_vigente_coincide_en_las_dos_caras`: la fila del usuario y el historico no divergen.
    6. `la_cadena_encadena_los_roles`: cada eslabon arranca en el rol que dejo el anterior y ningun
       asiento es un cambio vacio.
    """

    run_state_machine_as_test(MaquinaRolVigente, settings=AJUSTES_PBT_003)
