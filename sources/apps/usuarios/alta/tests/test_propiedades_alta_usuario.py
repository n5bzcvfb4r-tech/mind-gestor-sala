"""
Pruebas basadas en propiedades del alta de usuario (PBT-001).

Cuantifican sobre un ESPACIO de correos corporativos generado por Hypothesis -con variaciones
aleatorias de mayusculas y minusculas y con espacios anadidos en los extremos-, no sobre ejemplos
escogidos a mano: lo que se afirma es la INVARIANTE DE IDENTIDAD del usuario (REQ-045, RN-01), y
una invariante solo se acredita probandola contra entradas que el autor del test no ha elegido.

POR QUE ESTA PROPIEDAD NO TOCA ORACLE
-------------------------------------
Por dos motivos distintos y ambos suficientes:

1. La plataforma NO ha podido levantar el entorno de prueba de esta sesion: `.mind/TSK-069/env.json`
   trae `status: unavailable` porque no hay ningun engine Docker alcanzable, de modo que no existe
   el contenedor de Oracle 23ai contra el que ejecutar nada.
2. Aunque lo hubiera, una propiedad ejecuta CIENTOS de casos. Lanzar cada uno contra un contenedor
   -con su INSERT, su rollback y, en este caso, un hash Argon2id por alta- es inviable como prueba
   de desarrollo.

LOS DOBLES REPLICAN LA SEMANTICA QUE IMPONE ORACLE, NO UNA PARECIDA
-------------------------------------------------------------------
El servicio admite sus tres colaboradores por constructor, y lo que se ejercita aqui es su POLITICA
-normalizar, comparar la unicidad sobre la forma canonica, persistir ESE MISMO valor y traducir la
colision-, que vive integra en `servicio.py` y no en el SQL. El censo falso decide la unicidad con
`LOWER(TRIM(corporate_email))`, que es LITERALMENTE la expresion del indice unico funcional
`ux_usuario_email_ci ON usuario (LOWER(TRIM(corporate_email)))` del DDL
(`sources/facilities/changelogs/0.0.1/ddl/05-usuario.xml`), y cuando esa expresion colisiona lanza
el mismo `IntegrityError` con el texto del ORA-00001 que devuelve el motor. Si el doble decidiera
la unicidad de otra forma, la propiedad no estaria midiendo el sistema que se despliega.

LA EVIDENCIA DE PERSISTENCIA CONTRA EL MOTOR REAL QUEDA DIFERIDA AL CI
-----------------------------------------------------------------------
Que la fila escrita en `usuario` conserve de verdad la forma canonica y que el indice unico rechace
de verdad la segunda alta son hechos del MOTOR, y se acreditan con las pruebas de integracion
marcadas `integration` cuando el CI disponga de Docker. Aqui se cubre la invariante algebraica, que
es lo que ninguna prueba contra contenedor puede cubrir por volumen.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

import pytest
from django.db.utils import IntegrityError
from hypothesis import given, settings
from hypothesis import strategies as st

from apps.core.contexto import AlcanceDatos, ContextoSesion, contexto_requerido, utc_now
from apps.usuarios.alta import servicio as modulo_servicio
from apps.usuarios.alta.errores import CorreoDuplicadoError
from apps.usuarios.alta.normalizacion import (
    LONGITUD_MAXIMA_CORREO,
    LONGITUD_MAXIMA_NOMBRE,
    LONGITUD_MINIMA_NOMBRE,
    normalizar_correo,
    normalizar_nombre,
)
from apps.usuarios.alta.repositorio import ESTADO_ACTIVO, INDICADOR_SI
from apps.usuarios.alta.servicio import DatosAltaUsuario, ServicioAltaUsuario


#: Longitud minima del correo que piden los datos de prueba del catalogo. La forma normalizada se
#: genera SIEMPRE dentro de la horquilla [6, 150]: el maximo es `LONGITUD_MAXIMA_CORREO`, la anchura
#: real de `usuario.corporate_email` en el esquema T.5, y generar fuera de ella probaria un sistema
#: que no existe, porque Oracle rechazaria la fila con un ORA-12899 antes de llegar a la invariante.
LONGITUD_MINIMA_CORREO = 6

#: Catalogo CERRADO de roles: son las tres filas que siembra
#: `facilities/changelogs/0.0.1/dml/01-seed-cat-seguridad.xml` en `cat_rol`. Es una precondicion del
#: escenario que el rol indicado pertenezca al catalogo, asi que no se genera ninguno fuera de el.
ROLES_DEL_CATALOGO: tuple[str, ...] = ("EMPLEADO", "TECNICO_MANTENIMIENTO", "ADMINISTRADOR")

#: Texto del ORA-00001 tal y como lo devuelve Oracle al violar el indice unico funcional del correo.
#: Es el arbitro REAL de la carrera entre dos altas simultaneas y el unico `IntegrityError` que el
#: servicio puede traducir a 409; el resto los relanza.
ORA_00001_CORREO = "ORA-00001: unique constraint (FACILITIES.UX_USUARIO_EMAIL_CI) violated"

#: Identificador del ADMINISTRADOR que ejecuta el alta. Los usuarios creados reciben identificadores
#: a partir de `PRIMER_USER_ID`, de modo que el actor NUNCA puede coincidir con el usuario creado:
#: es una precondicion del escenario y se afirma ademas en cada prueba.
USER_ID_ACTOR = 1
PRIMER_USER_ID = 1_000

#: Pool PEQUENO de partes locales y dominios. Que sea corto es parte del diseno: con pocas bases,
#: Hypothesis genera con frecuencia pares de correos que coinciden tras normalizar, que es justo la
#: rama de rechazo que la invariante tiene que ejercitar.
PARTES_LOCALES: tuple[str, ...] = ("ana.lopez", "luis.gomez", "eva.ruiz", "j.m", "mcarmen-diaz", "usuario01")
DOMINIOS: tuple[str, ...] = ("mind.local", "empresa.com", "sub.dominio.corporativo.es")

#: Piezas con las que se componen nombres completos. Llevan acentos y enies a proposito: la
#: normalizacion del nombre colapsa espacios y NO pliega Unicode (RN-06), y eso tiene que seguir
#: siendo cierto con las entradas generadas.
PIEZAS_NOMBRE: tuple[str, ...] = ("Ana", "Maria", "Lopez", "Nunez", "del Valle", "Jose Luis", "Ruiz-Diaz", "Mª Angeles")


@dataclass
class FichaUsuario:
    """
    Doble de `UsuarioEntity` con los campos que el alta ESCRIBE y los que `_proyectar` LEE.

    Los nombres son los del modelo real, no alias comodos: `role_code_id` (y no `role_code`) porque
    la proyeccion lee el `attname` de la clave ajena para no disparar una consulta extra, y
    `created_by_id` por el mismo motivo. Si este doble renombrase algo, la proyeccion fallaria aqui
    por un motivo que no existe en produccion -o peor, pasaria sin probar el campo real-.
    """

    user_id: int
    full_name: str
    corporate_email: str
    role_code_id: str
    status: str
    must_change_password: str
    created_at: datetime
    created_by_id: int | None
    credential_issued_at: datetime | None
    password_expires_at: datetime | None


class CensoFalso:
    """
    Doble del `RepositorioAltaUsuario` que mantiene el censo en memoria.

    LA UNICIDAD SE DECIDE CON `LOWER(TRIM(...))`, COMO EL INDICE
    -------------------------------------------------------------
    Tanto `existe_correo` como la guardia de `crear` comparan `fila.corporate_email.strip().lower()`,
    que es la traduccion literal a Python de `LOWER(TRIM(corporate_email))`, la expresion del indice
    unico funcional `ux_usuario_email_ci`. Es la MISMA que anota el repositorio real con
    `Lower(Trim("corporate_email"))`.

    `crear` NO confia en que quien llama haya comprobado antes: si la forma canonica ya esta en el
    censo lanza `IntegrityError` con el texto del ORA-00001, igual que haria el motor. Esa es la
    rama que cubre la carrera entre dos altas simultaneas.

    `existe_rol_vigente` devuelve `True` para los tres codigos del catalogo sembrado: que el rol
    pertenezca al catalogo cerrado es una precondicion del escenario, no algo que esta propiedad
    ponga a prueba.
    """

    def __init__(self) -> None:
        self.filas: list[FichaUsuario] = []
        self.usernames: list[str] = []

    @staticmethod
    def _clave(corporate_email: str) -> str:
        """Expresion del indice unico funcional del correo: `LOWER(TRIM(corporate_email))`."""

        return corporate_email.strip().lower()

    @property
    def claves(self) -> list[str]:
        """Las formas canonicas de todas las filas del censo, en orden de alta."""

        return [self._clave(fila.corporate_email) for fila in self.filas]

    def existe_correo(self, corporate_email_normalizado: str) -> bool:
        """`True` si alguna fila almacenada coincide con el correo bajo la expresion del indice."""

        return any(self._clave(fila.corporate_email) == corporate_email_normalizado for fila in self.filas)

    def existe_username(self, username: str) -> bool:
        """`True` si el identificador de acceso ya esta ocupado; compara `LOWER(username)`, como `ux_usuario_username_ci`."""

        return any(fila_username.lower() == username.lower() for fila_username in self.usernames)

    def existe_rol_vigente(self, role_code: str) -> bool:
        """`True` para los roles del catalogo cerrado sembrado en `cat_rol`."""

        return role_code in ROLES_DEL_CATALOGO

    def crear(self, **campos: Any) -> FichaUsuario:
        """
        Escribe la fila del usuario nuevo y la devuelve con su `user_id` asignado.

        Replica tres hechos del INSERT real: el `user_id` lo asigna la base (aqui, correlativo desde
        `PRIMER_USER_ID`), la atribucion la sella `AtribucionMixin.save()` desde el CONTEXTO DE
        SESION -nunca desde los parametros, que por eso no la traen- y el indice unico arbitra la
        unicidad del correo lanzando ORA-00001.
        """

        corporate_email = campos["corporate_email"]
        # La guardia del INDICE se evalua sobre las filas, NO llamando a `existe_correo`: en Oracle
        # son dos mecanismos distintos -un SELECT previo y una restriccion del motor-, y confundirlos
        # aqui haria que cegar la consulta previa cegase tambien al arbitro de la carrera.
        if self._clave(corporate_email) in self.claves:
            raise IntegrityError(ORA_00001_CORREO)

        actor = contexto_requerido()
        fila = FichaUsuario(
            user_id=PRIMER_USER_ID + len(self.filas),
            full_name=campos["full_name"],
            corporate_email=corporate_email,
            role_code_id=campos["role_code"],
            status=ESTADO_ACTIVO,
            must_change_password=INDICADOR_SI,
            created_at=utc_now(),
            created_by_id=actor.user_id,
            credential_issued_at=campos["credential_issued_at"],
            password_expires_at=campos["password_expires_at"],
        )
        self.filas.append(fila)
        self.usernames.append(campos["username"])
        return fila


@dataclass(frozen=True, slots=True)
class SolicitudFalsa:
    """Doble de `SolicitudCredencial`: solo expone el `notification_id` que el servicio traza y proyecta."""

    notification_id: str


@dataclass(frozen=True, slots=True)
class ResultadoEntregaFalso:
    """Doble de `ResultadoEntregaCredencial` con el desenlace favorable: entregado y sin 502."""

    notification_id: str
    entregado: bool = True
    omitida: bool = False
    debe_responder_502: bool = False
    mensaje_usuario: str | None = None
    codigo_error: str | None = None


class EntregaFalsa:
    """
    Doble del `ServicioEntregaCredencial` que encola y entrega sin tocar el outbox ni SMTP.

    El transporte NO es lo que esta propiedad mide: lo que se afirma es la identidad del correo
    PERSISTIDO, y el 502 por correo no entregado (REQ-038 regla 6) tiene sus propias pruebas. Aqui
    la entrega siempre prospera para que ninguna rama de fallo del aviso enmascare la invariante.
    """

    def __init__(self) -> None:
        self.encolados: list[Any] = []

    def encolar(self, datos: Any) -> SolicitudFalsa:
        """Registra la solicitud y devuelve su identificador, como haria el outbox dentro de la transaccion."""

        self.encolados.append(datos)
        return SolicitudFalsa(notification_id=f"aviso-{len(self.encolados):04d}")

    def entregar(self, solicitud: SolicitudFalsa, datos: Any) -> ResultadoEntregaFalso:
        """Cierra la entrega como ENTREGADA: sin fallo de transporte que desvie el flujo al 502."""

        return ResultadoEntregaFalso(notification_id=solicitud.notification_id)


class TransaccionFalsa:
    """
    Doble del modulo `django.db.transaction` para el `with transaction.atomic()` del servicio.

    NO hay conexion a Oracle en esta sesion, asi que el `atomic()` real fallaria al abrirla y la
    propiedad no llegaria a ejecutarse nunca. Este doble reproduce lo UNICO que la invariante
    necesita del bloque transaccional: que si algo revienta dentro, el censo vuelva a como estaba
    (ROLLBACK). Esa fidelidad no es decorativa: es lo que permite afirmar que, cuando el indice
    unico rechaza el INSERT, NO queda ninguna fila a medias y por tanto «como maximo uno de los dos
    usuarios existe».
    """

    def __init__(self, censo: CensoFalso) -> None:
        self._censo = censo

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """Bloque atomico en memoria: confirma al salir bien y deshace el censo si sale una excepcion."""

        instantanea = list(self._censo.filas)
        usernames = list(self._censo.usernames)
        try:
            yield
        except Exception:
            self._censo.filas[:] = instantanea
            self._censo.usernames[:] = usernames
            raise


def actor_administrador() -> ContextoSesion:
    """
    ADMINISTRADOR autenticado que ejecuta el alta (precondicion del escenario).

    Su `user_id` es `USER_ID_ACTOR` y los usuarios creados nacen desde `PRIMER_USER_ID`, asi que el
    actor es SIEMPRE distinto del usuario creado.
    """

    return ContextoSesion(
        user_id=USER_ID_ACTOR,
        role_code="ADMINISTRADOR",
        session_id="22222222-2222-4222-8222-222222222222",
        data_scope=AlcanceDatos.ALL.value,
        display_name="Administrador de prueba",
    )


class AsignacionDeRolFalsa:
    """
    Doble del servicio de asignacion de rol (`apps.usuarios.roles.ServicioRolUsuario`).

    El alta REGISTRA el asiento de rol dentro de su propia transaccion (AC-ROL-01), y el servicio
    real de roles escribe en `usuario_historico`, que aqui no existe: esta propiedad no toca Oracle
    (ver el docstring del modulo). El doble no simula la tabla, solo ANOTA la asignacion que el alta
    le pidio registrar, que es lo unico que esta propiedad necesita saber del rol.

    Que anote en vez de no hacer nada importa: permite afirmar, sin mirar la base, que el asiento se
    pide para el usuario recien creado, con SU rol y con el ADMINISTRADOR de la sesion como autor, y
    no con un actor tomado del payload (REQ-004 RN-04, REQ-064).
    """

    def __init__(self) -> None:
        self.asignaciones: list[tuple[int, str, int]] = []

    def asignar_en_alta(self, usuario: Any, *, actor: ContextoSesion) -> None:
        """Anota `(user_id, role_code, actor)` de la asignacion pedida por el alta."""

        self.asignaciones.append((usuario.user_id, usuario.role_code_id, actor.user_id))


def construir_servicio(censo: CensoFalso, parche: pytest.MonkeyPatch) -> ServicioAltaUsuario:
    """
    Arma el servicio REAL con el censo y la entrega dobles, y con la custodia de verdad.

    La custodia NO se dobla: `ServicioCustodiaCredenciales()` es el punto unico de hashing (REQ-069),
    calcula el verificador con Argon2id y no toca la base. Sustituirla por un doble convertiria la
    guardia `exigir_verificador` -que es la que impide persistir material sin hashear- en un adorno.

    El modulo `transaction` SI se sustituye, y solo dentro del espacio de nombres del servicio: ver
    `TransaccionFalsa` para el motivo.
    """

    from apps.identidad.credenciales.servicio import ServicioCustodiaCredenciales

    parche.setattr(modulo_servicio, "transaction", TransaccionFalsa(censo))
    return ServicioAltaUsuario(
        repositorio=censo,
        custodia=ServicioCustodiaCredenciales(),
        entrega=EntregaFalsa(),
        roles=AsignacionDeRolFalsa(),
    )


@st.composite
def _correos_base(draw: Any) -> str:
    """
    Correo corporativo valido YA en forma canonica, de entre 6 y 150 caracteres.

    El relleno `x` alarga la parte local hasta rozar el tope de la columna, de modo que el espacio
    generado cubra tambien los correos largos: el catalogo pide «de 6 a 150 caracteres» y un pool de
    direcciones cortas dejaria sin probar justo el borde que la normalizacion tiene que respetar.
    """

    local = draw(st.sampled_from(PARTES_LOCALES))
    dominio = draw(st.sampled_from(DOMINIOS))
    margen = LONGITUD_MAXIMA_CORREO - len(local) - len(dominio) - 1
    relleno = draw(st.integers(min_value=0, max_value=margen))
    correo = f"{local}{'x' * relleno}@{dominio}"
    assert LONGITUD_MINIMA_CORREO <= len(correo) <= LONGITUD_MAXIMA_CORREO
    return correo


@st.composite
def _variacion(draw: Any, base: str) -> str:
    """
    Deforma un correo canonico como lo deformaria una persona: caja aleatoria y espacios en los extremos.

    Es la entrada REAL del escenario -lo que el administrador teclea o pega-, y su forma normalizada
    sigue siendo `base`: esa igualdad es la que la propiedad usa como oraculo.
    """

    cajas = draw(st.lists(st.booleans(), min_size=len(base), max_size=len(base)))
    texto = "".join(caracter.upper() if mayuscula else caracter for caracter, mayuscula in zip(base, cajas))
    izquierda = " " * draw(st.integers(min_value=0, max_value=3))
    derecha = " " * draw(st.integers(min_value=0, max_value=3))
    return f"{izquierda}{texto}{derecha}"


@st.composite
def _correos_corporativos(draw: Any) -> str:
    """Correo tal y como llega en la peticion: valido, de 6 a 150 ya normalizado, con caja y espacios aleatorios."""

    return draw(_variacion(draw(_correos_base())))


@st.composite
def _nombres_completos(draw: Any) -> str:
    """
    Nombre completo de 2 a 120 caracteres con espacios REPETIDOS intercalados y en los extremos.

    Los huecos dobles son los datos de prueba del catalogo y no un capricho: son lo que deja el
    pegado desde una hoja de calculo, y `normalizar_nombre` tiene que colapsarlos.
    """

    piezas = draw(st.lists(st.sampled_from(PIEZAS_NOMBRE), min_size=1, max_size=4))
    separadores = [" " * draw(st.integers(min_value=1, max_value=4)) for _ in range(len(piezas) - 1)]
    cuerpo = "".join(pieza + separador for pieza, separador in zip(piezas, separadores + [""]))
    nombre = " " * draw(st.integers(min_value=0, max_value=3)) + cuerpo + " " * draw(st.integers(min_value=0, max_value=3))
    assert LONGITUD_MINIMA_NOMBRE <= len(normalizar_nombre(nombre)) <= LONGITUD_MAXIMA_NOMBRE
    return nombre


#: Argon2id cuesta decenas de milisegundos POR ALTA y el servicio calcula un verificador en cada
#: una, asi que el presupuesto de ejemplos se fija en 30 -y no en los 100 de PBT-011/PBT-012- para
#: que la propiedad siga siendo una prueba de desarrollo y no una espera. `deadline=None` por el
#: mismo motivo: el coste del hash hace que el limite por ejemplo de Hypothesis sea ruido.
MAXIMO_EJEMPLOS = 30


@settings(max_examples=MAXIMO_EJEMPLOS, deadline=None)
@given(correo=_correos_corporativos(), nombre=_nombres_completos(), role_code=st.sampled_from(ROLES_DEL_CATALOGO))
def test_PBT_001_el_correo_persistido_es_siempre_su_forma_normalizada(correo: str, nombre: str, role_code: str) -> None:
    """
    [PBT-001] Para todo correo generado, el valor PERSISTIDO es exactamente su forma normalizada.

    Mitad «invariante» del escenario: el valor que se compara para decidir la unicidad y el que se
    escribe en la fila son EL MISMO (REQ-045, RN-01). Se afirma sobre cuatro observables: la fila
    del censo, el `corporate_email` que publica `UsuarioCreado`, la forma de ese valor (minusculas y
    sin espacios) y el hecho de que `existe_correo` reconozca despues la forma canonica -que es lo
    que impide que una segunda alta con otra caja prospere-.
    """

    censo = CensoFalso()
    with pytest.MonkeyPatch.context() as parche:
        servicio = construir_servicio(censo, parche)
        actor = actor_administrador()
        resultado = servicio.crear(DatosAltaUsuario(full_name=nombre, corporate_email=correo, role_code=role_code), actor=actor)

    canonico = normalizar_correo(correo)

    # El alta ha escrito UNA fila y el valor persistido es la forma canonica, no el texto en bruto.
    assert len(censo.filas) == 1, f"el alta debe escribir exactamente una fila, se han escrito {len(censo.filas)}"
    persistido = censo.filas[0].corporate_email
    assert persistido == canonico, f"se ha persistido {persistido!r} en vez de la forma canonica {canonico!r}"
    assert persistido == persistido.lower(), f"el correo persistido conserva mayusculas: {persistido!r}"
    assert persistido == "".join(persistido.split()), f"el correo persistido conserva espacios: {persistido!r}"
    assert len(persistido) <= LONGITUD_MAXIMA_CORREO

    # Lo comparado y lo almacenado son el mismo valor, y es el que se publica.
    assert resultado.corporate_email == persistido, "el correo publicado difiere del persistido"
    assert censo.existe_correo(canonico), "tras el alta, la forma canonica no es reconocida por la comprobacion de unicidad"

    # El nombre viaja normalizado: espacios de los extremos recortados y huecos internos colapsados.
    assert censo.filas[0].full_name == normalizar_nombre(nombre)
    assert resultado.full_name == censo.filas[0].full_name

    # Precondicion del escenario: el actor es un ADMINISTRADOR distinto del usuario creado.
    assert actor.role_code == "ADMINISTRADOR"
    assert resultado.created_by == actor.user_id
    assert resultado.user_id != actor.user_id, "el administrador que ejecuta el alta no puede ser el usuario creado"


@st.composite
def _pares_de_correos(draw: Any) -> tuple[str, str]:
    """
    Par de correos que, tras normalizar, COINCIDEN o NO coinciden: los dos casos del escenario.

    Cuando `mismo` es cierto, las dos entradas son variaciones de caja y espacios del MISMO correo
    canonico -el caso que la invariante tiene que rechazar-. Cuando es falso, se dibujan dos bases
    distintas: la colision accidental dentro del pool corto ya la cubre la otra rama.
    """

    primera = draw(_correos_base())
    mismo = draw(st.booleans())
    segunda = primera if mismo else draw(_correos_base().filter(lambda otra: otra != primera))
    return draw(_variacion(primera)), draw(_variacion(segunda))


@settings(max_examples=MAXIMO_EJEMPLOS, deadline=None)
@given(
    par=_pares_de_correos(),
    nombres=st.tuples(_nombres_completos(), _nombres_completos()),
    role_code=st.sampled_from(ROLES_DEL_CATALOGO),
)
def test_PBT_001_dos_variantes_del_mismo_correo_no_pueden_coexistir(
    par: tuple[str, str],
    nombres: tuple[str, str],
    role_code: str,
) -> None:
    """
    [PBT-001] Para todo par de correos cuya forma normalizada coincide, como MAXIMO uno de los dos usuarios existe.

    Mitad «rechazo» del escenario. Se dan de alta los dos correos con el mismo ADMINISTRADOR y se
    afirma la disyuntiva completa: si las formas canonicas coinciden, la segunda alta muere con
    `CorreoDuplicadoError` (409) y el censo conserva UNA sola fila; si no coinciden, existen los dos
    y cada uno con su propia forma canonica. En ningun caso puede haber dos filas con la misma forma
    canonica: es la invariante que el indice unico `ux_usuario_email_ci` garantiza en Oracle y que el
    servicio tiene que respetar antes de llegar a el.
    """

    censo = CensoFalso()
    actor = actor_administrador()
    canonicos = (normalizar_correo(par[0]), normalizar_correo(par[1]))
    desenlaces: list[CorreoDuplicadoError | None] = []

    with pytest.MonkeyPatch.context() as parche:
        servicio = construir_servicio(censo, parche)
        for correo, nombre in zip(par, nombres):
            try:
                servicio.crear(DatosAltaUsuario(full_name=nombre, corporate_email=correo, role_code=role_code), actor=actor)
                desenlaces.append(None)
            except CorreoDuplicadoError as error:
                desenlaces.append(error)

    coinciden = canonicos[0] == canonicos[1]
    existentes = censo.claves

    # Ninguna forma canonica aparece dos veces en el censo: es lo que impide el indice unico.
    assert len(set(existentes)) == len(existentes), f"el censo contiene la misma forma canonica dos veces: {existentes}"
    assert actor.role_code == "ADMINISTRADOR"
    assert all(fila.user_id != actor.user_id for fila in censo.filas), "el actor del alta no puede ser uno de los usuarios creados"

    if coinciden:
        # Como MAXIMO uno de los dos usuarios llega a existir, y es el primero: el segundo intento
        # choca con el 409 y no deja nada escrito.
        assert len(censo.filas) == 1, f"dos altas con la forma canonica {canonicos[0]!r} han dejado {len(censo.filas)} filas"
        assert existentes == [canonicos[0]]
        assert desenlaces[0] is None, "la primera alta no deberia haber fallado"
        rechazo = desenlaces[1]
        assert isinstance(rechazo, CorreoDuplicadoError), f"la segunda alta no fue rechazada: {rechazo!r}"
        assert rechazo.http_status == 409
        assert rechazo.codigo == "USR_EMAIL_DUPLICATED"
    else:
        # Correos distintos: existen los dos, cada uno persistido en su forma canonica.
        assert desenlaces == [None, None], f"un alta de correos distintos ha sido rechazada: {desenlaces}"
        assert len(censo.filas) == 2
        assert existentes == list(canonicos)
        assert [fila.corporate_email for fila in censo.filas] == list(canonicos)


@settings(max_examples=MAXIMO_EJEMPLOS, deadline=None)
@given(base=_correos_base(), nombre=_nombres_completos(), role_code=st.sampled_from(ROLES_DEL_CATALOGO))
def test_PBT_001_la_carrera_entre_dos_altas_simultaneas_la_arbitra_el_indice_unico(base: str, nombre: str, role_code: str) -> None:
    """
    [PBT-001] El ORA-00001 de `ux_usuario_email_ci` se traduce al MISMO 409 que la comprobacion previa.

    La comprobacion previa NO basta por si sola para sostener la invariante: entre el SELECT que no
    ve nada y el INSERT que escribe caben dos altas simultaneas del mismo correo. Aqui se reproduce
    esa carrera cegando `existe_correo` -devuelve siempre `False`, como le ocurriria a la peticion
    que mira el censo antes de que la otra confirme- y dejando que arbitre el indice unico. El
    servicio tiene que traducir ese `IntegrityError` al MISMO `CorreoDuplicadoError` 409, y el censo
    tiene que quedarse con una sola fila: la transaccion deshecha no deja nada a medias.
    """

    censo = CensoFalso()
    actor = actor_administrador()
    canonico = normalizar_correo(base)

    with pytest.MonkeyPatch.context() as parche:
        servicio = construir_servicio(censo, parche)
        servicio.crear(DatosAltaUsuario(full_name=nombre, corporate_email=base, role_code=role_code), actor=actor)

        # La peticion rival no ve el correo que la otra acaba de escribir: su SELECT previo dice «no
        # existe» y llega hasta el INSERT. Es exactamente la ventana de la carrera.
        parche.setattr(censo, "existe_correo", lambda _correo: False)
        with pytest.raises(CorreoDuplicadoError) as rechazo:
            servicio.crear(DatosAltaUsuario(full_name=nombre, corporate_email=base.upper(), role_code=role_code), actor=actor)

    assert rechazo.value.http_status == 409, "la violacion del indice unico del correo debe acabar en 409"
    assert rechazo.value.codigo == "USR_EMAIL_DUPLICATED"
    assert isinstance(rechazo.value.__cause__, IntegrityError), "el 409 debe encadenar el error de integridad que lo origino"
    assert len(censo.filas) == 1, f"la carrera ha dejado {len(censo.filas)} filas: el INSERT rechazado no debe persistir nada"
    assert censo.filas[0].corporate_email == canonico
