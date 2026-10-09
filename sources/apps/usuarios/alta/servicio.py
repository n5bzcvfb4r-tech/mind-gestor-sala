"""
Alta de usuario por el ADMINISTRADOR (EP-007, REQ-037, REQ-038, REQ-045): orquestacion del caso de uso.

Este modulo coordina a colaboradores que YA existen y no reimplementa nada de lo suyo: la
normalizacion de los datos de identidad (`apps.usuarios.alta.normalizacion`), el acceso a datos
(`RepositorioAltaUsuario`), el generador de credencial temporal
(`apps.identidad.reposicion.generador`), la custodia de credenciales (`ServicioCustodiaCredenciales`,
punto UNICO de hashing y politica, REQ-069) y la entrega del aviso (`ServicioEntregaCredencial`,
ARC-014). Lo propio de aqui es el ORDEN en que se invocan.

EL ORDEN ES LA REGLA DE NEGOCIO, Y ES EL INVERSO DEL DE EP-017
---------------------------------------------------------------
REQ-038 regla 6 es explicito para el alta: si el correo con la credencial inicial no sale, EL
USUARIO QUEDA CREADO IGUALMENTE y la respuesta es 502. Por eso aqui se escribe PRIMERO (usuario +
solicitud de aviso, en la misma transaccion) y se entrega DESPUES, ya fuera de ella. El
restablecimiento administrativo (`apps.identidad.reposicion.servicio`, EP-017, REQ-073 regla 7)
hace justo lo contrario -entrega primero y solo confirma si el correo salio- porque alli SI habia
una credencial vigente que preservar. Aqui no la hay: deshacer el alta no devolveria a nadie a un
estado util, solo borraria un usuario correctamente validado por un fallo del servidor de correo.

LA FORMA CANONICA SE COMPARA Y SE PERSISTE, Y ES LA MISMA (REQ-045, RN-01)
---------------------------------------------------------------------------
El correo que se compara contra el censo y el que se escribe en la fila son el MISMO valor, el que
devuelve `normalizar_correo`. Esa igualdad es la invariante que acredita PBT-001: comparar la forma
normalizada y guardar el texto original haria que dos altas que solo difieren en mayusculas
chocasen la primera vez y dejasen de chocar despues.

SECRETOS (REQ-063, REQ-076, REQ-079)
------------------------------------
La credencial inicial existe en claro solo dentro de `crear`, viaja a la custodia y a la entrega, y
muere con la llamada. NO se devuelve -`UsuarioCreado` no tiene donde guardarla-, NO se registra en
ninguna traza y NO se interpola en ningun mensaje. Tampoco se loggean el `corporate_email`, el
`full_name` ni el `password_hash`: en las trazas de este modulo solo viajan identificadores.
"""

from __future__ import annotations

import logging
import string
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from django.db import IntegrityError, transaction

from apps.avisos.credenciales.entrega import (
    MENSAJE_502_CREDENCIAL,
    DatosCredencial,
    ServicioEntregaCredencial,
    servicio_entrega_credencial,
)
from apps.avisos.motor.outbox import FORMATO_DISCRIMINANTE, TIPO_CREDENCIAL_EMITIDA
from apps.core.contexto import ContextoSesion, contexto_de_sesion, utc_now
from apps.core.errores import ErrorDominio
from apps.identidad.credenciales.servicio import ServicioCustodiaCredenciales
from apps.identidad.reposicion.generador import caducidad_credencial_temporal, generar_credencial_temporal
from apps.usuarios.alta.errores import CorreoDuplicadoError, EntregaCredencialInicialFallidaError
from apps.usuarios.alta.normalizacion import normalizar_correo, normalizar_nombre
from apps.usuarios.alta.repositorio import ESTADO_ACTIVO, INDICADOR_SI, RepositorioAltaUsuario


if TYPE_CHECKING:  # pragma: no cover - solo tipado: evita importar modelos antes de django.setup()
    from apps.core.models import UsuarioEntity


logger = logging.getLogger(__name__)

#: Estado con el que nace la cuenta (REQ-037 regla 4): el usuario es operativo desde el alta y
#: aparece de inmediato en el censo (USR-03). NO se reescribe el literal: se toma del repositorio,
#: que es quien lo ESCRIBE en la fila, para que el valor publicado en la respuesta y el persistido
#: no puedan desalinearse nunca.
ESTADO_INICIAL = ESTADO_ACTIVO

#: `INDICADOR_SI` ('Y' de Oracle, porque `must_change_password` es CHAR(1) y no un booleano) se
#: REEXPORTA del repositorio -se importa arriba y se publica en `__all__`- por el mismo motivo que
#: `ESTADO_INICIAL`: aqui se usa solo para TRADUCIR la columna al booleano que publica
#: `UsuarioCreado`, y la traduccion tiene que mirar exactamente el mismo caracter que se escribio.
#: Reescribir el literal crearia una segunda fuente de verdad para el mismo valor fisico.

#: CHECK `ck_usuario_username_len` del DDL (`06-usuario-credenciales.xml`):
#: `LENGTH(username) BETWEEN 3 AND 100`. El identificador derivado tiene que caer dentro de esa
#: horquilla SIEMPRE; un candidato fuera de ella no es un dato discutible, es un INSERT que Oracle
#: rechaza en mitad de la transaccion del alta.
LONGITUD_MINIMA_USERNAME = 3
LONGITUD_MAXIMA_USERNAME = 100

#: Caracteres admitidos en el identificador de acceso derivado: minusculas, digitos y los tres
#: separadores que aparecen de forma natural en la parte local de un correo corporativo. Se
#: construye por LISTA BLANCA y no descartando «los raros»: un acento, un espacio o una comilla
#: colados en el `username` lo convertirian en un dato incomodo de teclear en el formulario de
#: acceso -y la credencial inicial llega por correo, donde se transcribe a mano-.
CARACTERES_USERNAME: frozenset[str] = frozenset(string.ascii_lowercase + string.digits + "._-")

#: Base de respaldo cuando la parte local del correo no deja material suficiente (por ejemplo un
#: correo cuya parte local son tres acentos, que la lista blanca vacia). No se inventa un
#: identificador «bonito»: lo unico que importa es que cumpla el CHECK de longitud y que el bucle
#: de sufijos encuentre uno libre.
PREFIJO_USERNAME_POR_DEFECTO = "usr"

#: Cuantos sufijos `.2`, `.3`, ... se prueban antes de rendirse. Es una red de seguridad contra un
#: bucle infinito, no un limite de negocio: con el censo de una organizacion real no se agota
#: jamas, y si se agotase significaria que algo mas grave pasa con los datos.
MAXIMO_SUFIJOS_USERNAME = 50

#: Fragmento del nombre del indice unico funcional del correo tal y como aparece en el ORA-00001
#: que devuelve Oracle. Se compara EN MINUSCULAS contra el texto del `IntegrityError` para
#: distinguir la carrera de altas simultaneas de cualquier otra violacion de integridad.
INDICE_CORREO_UNICO = "ux_usuario_email_ci"

#: Mensaje del fallo interno al derivar el identificador de acceso. NO vive en
#: `apps.usuarios.alta.mensajes` a proposito: ese catalogo recoge los literales que REQ-037 publica
#: para el administrador, y este no es uno de ellos -es un 500 generico de agotamiento, sin accion
#: posible por parte de quien rellena el formulario-.
USERNAME_NO_DISPONIBLE = "No se ha podido asignar un identificador de acceso al usuario"

TRAZA_ALTA_CONFIRMADA = "Usuario creado y credencial inicial entregada"


@dataclass(frozen=True, slots=True)
class DatosAltaUsuario:
    """
    Los tres datos de entrada del alta (REQ-037, PRE-03), YA validados por el serializador.

    Es el contrato entre la frontera HTTP y el caso de uso. Lo que NO esta aqui es tan deliberado
    como lo que esta: no hay `createdBy` ni ningun alias suyo, porque el actor del alta se toma
    SIEMPRE del contexto de sesion (REQ-064) y aceptarlo del payload seria dejar que el cliente
    firme la auditoria con el nombre de otro. Tampoco hay contrasenia: el administrador no elige
    la credencial inicial, la genera el sistema (REQ-038 regla 1).

    `frozen=True` y `slots=True` por lo de siempre: lo que se valido una vez no se retoca por el
    camino y la estructura no admite atributos nuevos.

    Attributes:
        full_name: nombre de la persona, tal y como lo dejo el serializador.
        corporate_email: correo corporativo, ya en su forma canonica.
        role_code: codigo del rol del catalogo `cat_rol`, ya comprobado como vigente.
    """

    full_name: str
    corporate_email: str
    role_code: str


@dataclass(frozen=True, slots=True)
class UsuarioCreado:
    """
    Desenlace publicable de un alta ya confirmada (REQ-037 regla 4, REQ-038, AC-USR-01).

    ESTA ESTRUCTURA NO TRANSPORTA LA CREDENCIAL INICIAL, NI SU HASH, NI SU SAL, Y NO PUEDE
    HACERLO. No es una omision que haya que recordar al rellenarla: `slots=True` cierra la clase a
    atributos nuevos, de modo que un `resultado.password = ...` aniadido mas adelante falla en
    ejecucion en vez de colar el secreto hasta el serializador. La contrasenia inicial NUNCA se
    devuelve en la respuesta de la API y el administrador no la ve en ningun momento (REQ-038
    regla 1, REQ-063): el unico que la recibe es el usuario dado de alta, en su buzon corporativo.

    `frozen=True` por el mismo motivo que el resto de resultados del proyecto: lo que se devuelve
    es la foto de lo que ya ocurrio, y nadie aguas arriba debe poder retocarla.

    Attributes:
        user_id: identificador que asigno la base al insertar la fila.
        full_name: nombre NORMALIZADO que se persistio, no el texto en bruto de la peticion.
        corporate_email: correo en su forma canonica, el mismo con el que se comprobo la unicidad.
        role_code: codigo del rol asignado en el alta.
        status: estado de la cuenta recien creada; siempre `ESTADO_INICIAL`.
        must_change_password: siempre `True`; la credencial inicial es temporal y el usuario debera
            cambiarla en su primer acceso (REQ-038 regla 3, EP-006).
        created_at: marca de alta que SELLO EL SERVIDOR, naive en UTC (REQ-048).
        created_by: identificador del administrador que la ejecuto, leido del contexto de sesion.
            Es nulo solo en la cuenta semilla, que no se crea por este camino.
        credential_issued_at: instante de emision de la credencial inicial, naive en UTC.
        password_expires_at: caducidad de esa credencial (REQ-038: 48 h), naive en UTC.
        notification_id: identificador de la solicitud de aviso, para la traza y el soporte
            posterior. Identifica el envio, NUNCA su contenido.
    """

    user_id: int
    full_name: str
    corporate_email: str
    role_code: str
    status: str
    must_change_password: bool
    created_at: datetime
    created_by: int | None
    credential_issued_at: datetime
    password_expires_at: datetime
    notification_id: str


class ServicioAltaUsuario:
    """
    Caso de uso del alta de usuario (EP-007, REQ-037, REQ-038, REQ-045).

    Expone UN solo metodo publico, `crear`. Todo lo demas -normalizacion, derivacion del
    identificador de acceso, hashing, escritura y envio- son pasos internos cuyo orden es la propia
    regla de negocio.

    Lo que este servicio NO hace, a proposito: no comprueba permisos (de eso vive la capa de
    seguridad, que produce el 403 de REQ-037), no valida formatos ni longitudes ni el catalogo de
    roles (lo hace el serializador, que es quien puede atribuir el fallo a un CAMPO concreto) y no
    traduce nada a HTTP: sus errores son `ErrorDominio` y el manejador unico los convierte.
    """

    def __init__(
        self,
        repositorio: RepositorioAltaUsuario | None = None,
        custodia: ServicioCustodiaCredenciales | None = None,
        entrega: ServicioEntregaCredencial | None = None,
    ) -> None:
        # Los tres colaboradores se resuelven de forma PEREZOSA (ver las propiedades de abajo),
        # igual que en `ServicioReposicionCredencial`: el repositorio resuelve modelos del ORM y la
        # entrega lee `settings` y la fila activa de `configuracion_smtp`, asi que construirlos aqui
        # obligaria a tener el registro de aplicaciones cargado solo para instanciar el servicio.
        #
        # Se inyectan por constructor SOLO para poder verificar el caso de uso sin base de datos y
        # sin un SMTP real. La fabrica de produccion (`servicio_entrega_credencial`) NO tiene ningun
        # modo simulado: sin configuracion SMTP activa la entrega se cierra como FALLIDO y aqui
        # acaba en 502.
        self._repositorio_inyectado = repositorio
        self._custodia_inyectada = custodia
        self._entrega_inyectada = entrega

    @property
    def _repositorio(self) -> RepositorioAltaUsuario:
        """Repositorio del alta, instanciado en su primer uso (resuelve modelos del ORM)."""

        if self._repositorio_inyectado is None:
            self._repositorio_inyectado = RepositorioAltaUsuario()
        return self._repositorio_inyectado

    @property
    def _custodia(self) -> ServicioCustodiaCredenciales:
        """Servicio de custodia de credenciales, instanciado en su primer uso (REQ-054, REQ-069)."""

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

    def crear(self, datos: DatosAltaUsuario, *, actor: ContextoSesion) -> UsuarioCreado:
        """
        Da de alta al usuario, le emite la credencial inicial y se la entrega por correo.

        EL ORDEN ES LA REGLA DE NEGOCIO
        ===============================
        REQ-038 regla 6: si la entrega del correo con la credencial inicial falla, el usuario queda
        CREADO y la respuesta es 502. Ese requisito fija el orden, que es el INVERSO del
        restablecimiento administrativo (EP-017, REQ-073 regla 7), donde no se confirma nada hasta
        que el correo ha salido porque alli hay una credencial previa que preservar:

        1. normalizar nombre y correo; el valor normalizado es el que se compara Y el que se
           persiste, y son el mismo (REQ-045, RN-01, invariante de PBT-001);
        2. comprobacion previa de unicidad del correo: si ya existe, 409 SIN haber escrito nada;
        3. derivacion del identificador de acceso a partir de la parte local del correo;
        4. generacion de la credencial inicial EN MEMORIA y calculo de su caducidad;
        5. calculo del verificador y GUARDIA de REQ-054 antes de que nada se escriba;
        6. UNA transaccion: INSERT del usuario + encolado de la solicitud de aviso (patron outbox,
           ADR-006), de modo que la solicitud caiga con el alta o no caiga ninguna;
        7. ya FUERA de la transaccion: entrega sincrona por SMTP;
        8. si la entrega no prospero, 502 CON EL USUARIO YA CREADO.

        POR QUE EL ALTA NO SE REVIERTE ANTE UN 502 (REQ-038 regla 6)
        ------------------------------------------------------------
        Cuando se lanza `EntregaCredencialInicialFallidaError` la fila de `usuario` ya esta escrita
        y es DEFINITIVA. A diferencia de EP-017 no habia credencial previa que preservar, asi que
        deshacer el alta no devolveria a nadie a un estado util: solo borraria un usuario
        correctamente validado y obligaria al administrador a repetir el formulario por un fallo del
        servidor de correo. El 502 informa unicamente de que el correo con la credencial inicial no
        salio, y la via de reparacion es REEMITIR la credencial (EP-017), nunca repetir el alta
        -que ademas chocaria con el 409 de correo duplicado-.

        Args:
            datos: los tres datos del alta, YA validados por el serializador de la peticion.
            actor: contexto de sesion del ADMINISTRADOR que ejecuta el alta. De el -y nunca del
                payload- salen `created_by` y `created_at` (REQ-048, REQ-064).

        Returns:
            UsuarioCreado: el desenlace publicable, SIN la credencial.

        Raises:
            CorreoDuplicadoError: 409, el correo ya identifica a otra persona. No se crea nada.
            CredencialNoGenerableError: 500, el generador no obtuvo credencial valida.
            ContraseniaSinHashearError: 500, la guardia de custodia rechazo el material a persistir.
            EntregaCredencialInicialFallidaError: 502, el correo con la credencial inicial no salio.
                EL USUARIO YA ESTA CREADO: esta excepcion NO revierte el alta.
            ErrorDominio: 500, no se encontro ningun identificador de acceso libre.
        """

        # --- 1. Forma canonica: lo que se compara es lo que se persiste -------
        full_name = normalizar_nombre(datos.full_name)
        corporate_email = normalizar_correo(datos.corporate_email)

        # --- 2. Unicidad del correo, ANTES de escribir ni generar nada --------
        if self._repositorio.existe_correo(corporate_email):
            # 409 sin haber tocado la base y sin haber generado ningun secreto: ni se inserta una
            # fila a medias ni se quema una credencial que nadie va a recibir (REQ-037 regla 3).
            raise CorreoDuplicadoError()

        # --- 3. Identificador de acceso --------------------------------------
        username = self._username_para(corporate_email)

        # --- 4. Credencial inicial EN MEMORIA ---------------------------------
        ahora = utc_now()
        # Se le pasan `username` y `corporate_email` para que la credencial generada no los
        # contenga y, por tanto, no la rechace despues la politica unica del proyecto (REQ-069).
        credencial = generar_credencial_temporal(username=username, corporate_email=corporate_email)
        expira_en = caducidad_credencial_temporal(ahora)

        # --- 5. Verificador y guardia de REQ-054 ------------------------------
        # `exigir_verificador` no es redundante por venir el material del propio servicio de
        # custodia: es la guardia que impide persistir algo que no sea un hash de los hashers
        # configurados, y se invoca SIEMPRE, en el unico punto por el que pasa la escritura. Se le
        # pasa tambien la credencial en claro para que compruebe que el valor a persistir no es la
        # contrasenia misma.
        verificador = self._custodia.calcular_verificador(credencial)
        self._custodia.exigir_verificador(verificador, password=credencial)

        # --- 6. UNA transaccion: usuario + solicitud de aviso -----------------
        try:
            with transaction.atomic():
                # `contexto_de_sesion` es lo que permite a `AtribucionMixin.save()` sellar
                # `created_at` y `created_by` desde la SESION y nunca desde el payload (REQ-048,
                # REQ-064): sin el, el alta ejecutada fuera de una peticion HTTP quedaria sin actor.
                with contexto_de_sesion(actor):
                    usuario = self._repositorio.crear(
                        full_name=full_name,
                        corporate_email=corporate_email,
                        role_code=datos.role_code,
                        username=username,
                        password_hash=verificador.password_hash,
                        password_salt=verificador.password_salt,
                        password_algorithm=verificador.password_algorithm,
                        password_expires_at=expira_en,
                        credential_issued_at=ahora,
                    )

                    # EL AVISO SE COMPONE AQUI DENTRO, Y NO ANTES, porque `user_id` no existe hasta
                    # despues del INSERT: lo genera la IDENTITY de Oracle. Encolar dentro de la
                    # misma transaccion es el patron outbox (ADR-006, REQ-132): la solicitud cae con
                    # el alta o no cae ninguna, de modo que no puede quedar un aviso encolado para
                    # un usuario que nunca llego a existir ni un usuario sin su aviso pendiente.
                    datos_aviso = DatosCredencial(
                        notification_type=TIPO_CREDENCIAL_EMITIDA,
                        user_id=usuario.user_id,
                        full_name=usuario.full_name,
                        corporate_email=usuario.corporate_email,
                        credencial_temporal=credencial,
                        expires_at=expira_en,
                        # El discriminante es OBLIGATORIO y distinto en cada emision: sin el, una
                        # reemision al mismo usuario chocaria con la clave de idempotencia de la
                        # anterior y el correo no llegaria nunca.
                        discriminante=ahora.strftime(FORMATO_DISCRIMINANTE),
                    )
                    solicitud = self._entrega.encolar(datos_aviso)
        except IntegrityError as error:
            # LA CARRERA ENTRE DOS ALTAS SIMULTANEAS. Entre el `SELECT` del paso 2 y este `INSERT`
            # caben dos peticiones con el mismo correo: ambas verian «no existe» y ambas
            # escribirian. La BASE ES LA AUTORIDAD FINAL de la unicidad -el indice unico funcional
            # `ux_usuario_email_ci`-, y su ORA-00001 se traduce al MISMO 409 que la comprobacion
            # previa, porque para el administrador el hecho es identico: ese correo ya identifica a
            # alguien. La transaccion entera quedo deshecha, asi que tampoco aqui se crea nada.
            if INDICE_CORREO_UNICO in str(error).lower():
                raise CorreoDuplicadoError() from error
            # Cualquier otra violacion de integridad (una FK, una CHECK, el indice del `username`)
            # se RELANZA tal cual: taparla con un 409 de correo duplicado mentiria sobre la causa y
            # esconderia un defecto de datos real.
            raise

        # --- 7. Entrega, ya FUERA de toda transaccion -------------------------
        # Abrir una conexion SMTP con la transaccion abierta mantendria bloqueada la fila del
        # usuario recien creado durante todo el timeout del servidor de correo.
        resultado = self._entrega.entregar(solicitud, datos_aviso)

        # --- 8. El 502 que NO revierte el alta (REQ-038 regla 6) --------------
        if resultado.debe_responder_502:
            # Se mira `debe_responder_502` y no `entregado` a secas: una entrega OMITIDA -la
            # solicitud ya estaba encolada con la misma clave de idempotencia- no es un fallo que
            # deba llegarle al administrador. EL USUARIO YA ESTA CREADO Y SIGUE ESTANDOLO: esta
            # excepcion informa del correo que no salio, no deshace el alta.
            raise EntregaCredencialInicialFallidaError(
                resultado.mensaje_usuario or MENSAJE_502_CREDENCIAL,
                codigo_error=resultado.codigo_error,
            )

        # --- 9. Traza: SOLO identificadores -----------------------------------
        # Ni la credencial, ni el `corporate_email`, ni el `full_name`, ni el `password_hash`
        # (REQ-063, REQ-076, REQ-079).
        logger.info(
            TRAZA_ALTA_CONFIRMADA,
            extra={
                "data": {
                    "user_id": usuario.user_id,
                    "session_user_id": actor.user_id,
                    "notification_id": solicitud.notification_id,
                    "outcome": "OK",
                }
            },
        )

        return self._proyectar(
            usuario,
            credential_issued_at=ahora,
            password_expires_at=expira_en,
            solicitud_id=solicitud.notification_id,
        )

    def _proyectar(
        self,
        usuario: UsuarioEntity,
        *,
        credential_issued_at: datetime,
        password_expires_at: datetime,
        solicitud_id: str,
    ) -> UsuarioCreado:
        """
        Proyecta la fila recien escrita al resultado publicable, campo a campo y sin atajos.

        La proyeccion es EXPLICITA y no un volcado de la entidad: `UsuarioEntity` lleva
        `password_hash`, `password_salt` y `password_algorithm`, y cualquier mecanismo generico de
        copia los arrastraria hasta la frontera HTTP. Aqui se nombra uno por uno lo que sale, de
        modo que publicar material de credencial exigiria escribirlo a mano y quedaria a la vista en
        el diff (REQ-063, AC-USR-01).

        `must_change_password` se TRADUCE del CHAR(1) de Oracle al booleano del contrato comparando
        contra `INDICADOR_SI`: la columna no es un booleano y publicar la letra cruda obligaria a
        cada cliente a conocer el convenio fisico del esquema.

        `created_by` se lee de `created_by_id` y no de `created_by`: el atributo de la relacion
        dispararia una consulta extra para traer la fila entera del administrador solo para
        quedarnos con su identificador.

        Args:
            usuario: la entidad ya persistida, con su `user_id` asignado.
            credential_issued_at: instante de emision de la credencial inicial.
            password_expires_at: caducidad de esa credencial.
            solicitud_id: identificador de la solicitud de aviso entregada.

        Returns:
            UsuarioCreado: el desenlace publicable, sin ningun material de credencial.
        """

        return UsuarioCreado(
            user_id=usuario.user_id,
            full_name=usuario.full_name,
            corporate_email=usuario.corporate_email,
            role_code=usuario.role_code_id,
            status=usuario.status,
            must_change_password=usuario.must_change_password == INDICADOR_SI,
            created_at=usuario.created_at,
            created_by=usuario.created_by_id,
            credential_issued_at=credential_issued_at,
            password_expires_at=password_expires_at,
            notification_id=solicitud_id,
        )

    def _username_para(self, corporate_email: str) -> str:
        """
        Deriva un identificador de acceso LIBRE a partir de la parte local del correo corporativo.

        REQ-037 no pide el `username` al administrador -el formulario tiene tres campos y ninguno es
        este-, pero `usuario.username` es NOT NULL y lleva su propio indice unico funcional
        (`ux_usuario_username_ci ON usuario (LOWER(username))`, `06-usuario-credenciales.xml`). Hay
        que derivarlo, y derivarlo de modo que cumpla a la vez el CHECK de longitud
        (`ck_usuario_username_len`: entre 3 y 100) y esa unicidad.

        Como se construye:

        1. se toma la parte local del correo, lo anterior a la primera arroba;
        2. se filtra por LISTA BLANCA (`CARACTERES_USERNAME`) y se recorta a
           `LONGITUD_MAXIMA_USERNAME`;
        3. si lo que queda no llega a `LONGITUD_MINIMA_USERNAME` se parte de
           `PREFIJO_USERNAME_POR_DEFECTO`, porque un identificador mas corto lo rechazaria el CHECK;
        4. si el candidato ya esta ocupado se prueban sufijos `.2`, `.3`, ..., RECORTANDO LA BASE
           para que el total nunca pase de 100 caracteres: alargar sin recortar produciria un
           ORA-12899 en mitad del alta.

        LA COMPARACION DE OCUPACION ES LA DEL INDICE. `RepositorioAltaUsuario.existe_username`
        compara `LOWER(username)`, exactamente la expresion del indice, de modo que no se pueda
        elegir un candidato que la base vaya a rechazar despues.

        ESTO NO SUSTITUYE A LA RESTRICCION DE LA BASE: entre esta busqueda y el INSERT cabe otra
        alta que tome el mismo identificador. Si eso ocurre, el `IntegrityError` resultante NO es el
        del correo duplicado y `crear` lo relanza tal cual en vez de disfrazarlo de 409.

        Args:
            corporate_email: correo YA en su forma canonica (minusculas, sin espacios).

        Returns:
            str: identificador de acceso libre, de entre `LONGITUD_MINIMA_USERNAME` y
            `LONGITUD_MAXIMA_USERNAME` caracteres.

        Raises:
            ErrorDominio: si se agotan los `MAXIMO_SUFIJOS_USERNAME` intentos sin encontrar ninguno
                libre. Es un 500 generico: el administrador no ha escrito este dato y no tiene nada
                que corregir en el formulario.
        """

        parte_local = corporate_email.split("@", 1)[0]
        base = "".join(caracter for caracter in parte_local if caracter in CARACTERES_USERNAME)[:LONGITUD_MAXIMA_USERNAME]
        if len(base) < LONGITUD_MINIMA_USERNAME:
            base = PREFIJO_USERNAME_POR_DEFECTO

        if not self._repositorio.existe_username(base):
            return base

        # Los sufijos empiezan en `.2` y no en `.1`: el primer ocupante es «el 1» sin decirlo, y
        # `ana.lopez.2` se lee como «la segunda Ana Lopez», que es justo lo que ha pasado.
        for orden in range(2, MAXIMO_SUFIJOS_USERNAME + 2):
            sufijo = f".{orden}"
            candidato = f"{base[: LONGITUD_MAXIMA_USERNAME - len(sufijo)]}{sufijo}"
            if not self._repositorio.existe_username(candidato):
                return candidato

        # No se dice cuantos intentos se consumieron ni cual fue el ultimo candidato: el detalle no
        # ayuda a quien lee el mensaje y el diagnostico esta en la traza del servidor.
        raise ErrorDominio(USERNAME_NO_DISPONIBLE)


__all__ = [
    "CARACTERES_USERNAME",
    "ESTADO_INICIAL",
    "INDICADOR_SI",
    "LONGITUD_MAXIMA_USERNAME",
    "LONGITUD_MINIMA_USERNAME",
    "PREFIJO_USERNAME_POR_DEFECTO",
    "DatosAltaUsuario",
    "ServicioAltaUsuario",
    "UsuarioCreado",
]
