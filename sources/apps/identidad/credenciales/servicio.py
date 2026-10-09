"""
Servicio UNICO de custodia de credenciales: hashing, verificacion y politica (REQ-054, REQ-069).

QUE RESUELVE
------------
Es el unico punto del servicio que calcula un `password_hash`, el unico que lo compara y el unico
que lo escribe en la fila de `usuario`. Los tres flujos que establecen contrasenia -cambio propio
(EP-005, PWD-01), primer acceso forzado (EP-006, PWD-04) y restablecimiento por administrador
(EP-017, RST-01)- entran por `establecer`, de modo que una contrasenia rechazada por uno lo es por
los tres con el mismo codigo, el mismo estado y el mismo vocabulario (REQ-069, AC-PWD-03).

Nadie mas escribe `password_hash` directamente. Esa es la garantia que convierte el requisito en
estructura: si el hashing viviera en cada vista, bastaria con que una de ellas olvidase la politica,
el historial o el propio hashing para que la misma contrasenia entrase por una puerta y no por otra.

HASHING CON SAL POR USUARIO Y COMPARACION EN TIEMPO CONSTANTE (REQ-054, REQ-069)
-------------------------------------------------------------------------------
El hash lo calcula `django.contrib.auth.hashers.make_password`, que aplica el PRIMER hasher de
`PASSWORD_HASHERS` (Argon2id) y genera una sal aleatoria criptografica NUEVA en cada invocacion
(`django.utils.crypto.get_random_string`, sembrado por `secrets`), embebida en la cadena del hash.
Dos usuarios con la misma contrasenia no comparten hash, y una tabla robada no es reutilizable.

La verificacion delega en `check_password`, que compara con `hmac.compare_digest`: el tiempo de la
comparacion no depende de cuantos bytes coinciden. En este modulo NO se compara material de
credencial con `==` en ningun sitio.

EL ALGORITMO QUE SE PERSISTE NO SE INVENTA
------------------------------------------
`usuario.password_algorithm` tiene un enumerado CERRADO en el DDL (`argon2id`, `bcrypt`). El valor
que se escribe se DERIVA del hasher activo en vez de fijarse a mano: si manana se cambia
`PASSWORD_HASHERS`, la columna seguira diciendo la verdad. Si el hasher activo no tiene
correspondencia en ese enumerado, se falla con `CustodiaCredencialError` en lugar de escribir un
valor que la CHECK de Oracle rechazaria -o, peor, de mentir sobre como esta protegida la credencial.

SECRETOS (REQ-063, REQ-076) - INNEGOCIABLE
------------------------------------------
Este modulo no registra NINGUNA traza que contenga la contrasenia en claro, el `password_hash`, la
sal o el correo corporativo. El logger solo escribe identificadores (`user_id`) y codigos de
resultado. El `repr` de `Verificador` enmascara el hash para que no se vuelque en una traza de
excepcion ni en una sesion de depuracion, y los detalles tecnicos de los fallos de custodia viajan
en el atributo interno de la excepcion, nunca en el mensaje visible ni en el cuerpo de la respuesta.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from django.contrib.auth.hashers import check_password, get_hashers, make_password
from django.db import transaction

from apps.core.contexto import ContextoSesion, contexto_de_sesion, utc_now
from apps.identidad.credenciales.errores import (
    ContraseniaSinHashearError,
    CustodiaCredencialError,
    PoliticaContraseniaError,
)
from apps.identidad.credenciales.politica import (
    MAXIMO_CONTRASENIAS_HISTORICAS,
    PoliticaContrasenia,
    ReglaIncumplida,
)
from apps.identidad.credenciales.repositorio import RepositorioHistoricoPassword

if TYPE_CHECKING:  # pragma: no cover - solo para el tipado, evita importar modelos antes de django.setup()
    from apps.core.models import UsuarioEntity


logger = logging.getLogger(__name__)


# Valor por defecto -y unico esperado- de `usuario.password_algorithm`. Coincide literalmente con un
# miembro del enumerado cerrado `ck_usuario_password_algorithm` del DDL; no es un nombre decorativo.
ALGORITMO_POR_DEFECTO = "argon2id"

# Correspondencia entre el HASHER activo de Django y el valor del enumerado de Oracle. Se indexa por
# nombre de clase del hasher porque es lo que identifica al algoritmo de forma estable: el
# `algorithm` de Django (`argon2`, `bcrypt_sha256`) NO son valores validos de la columna, y escribir
# cualquiera de ellos haria fallar la CHECK. BCryptSHA256 y BCrypt se mapean ambos a `bcrypt`: la
# columna nombra la familia del algoritmo, que es lo que el enumerado del esquema admite.
ALGORITMO_POR_HASHER: dict[str, str] = {
    "Argon2PasswordHasher": ALGORITMO_POR_DEFECTO,
    "BCryptSHA256PasswordHasher": "bcrypt",
    "BCryptPasswordHasher": "bcrypt",
}

# Indicador booleano de Oracle: CHAR(1) con 'Y' o 'N'; el esquema no usa NUMBER(1) ni BOOLEAN.
INDICADOR_SI = "Y"
INDICADOR_NO = "N"

# Columnas de credencial que `establecer` escribe en `usuario`. Se enumeran para acotar el UPDATE a
# ellas: esta operacion no reinterpreta ninguna otra columna de la fila.
CAMPOS_CREDENCIAL: tuple[str, ...] = (
    "password_hash",
    "password_salt",
    "password_algorithm",
    "password_updated_at",
    "must_change_password",
    "password_expires_at",
)

# Separador de los componentes de un hash codificado de Django (`algoritmo$parametros$sal$hash`).
SEPARADOR_HASH = "$"

# Mascara con la que `Verificador.__repr__` sustituye el material criptografico.
MASCARA_HASH: str = "***"

# Texto interno del que se deriva el hash senuelo de ESTE modulo. NO es una credencial: no se
# persiste, no autentica a nadie y ninguna cuenta puede tenerlo, porque el hash que produce solo vive
# en memoria y jamas se compara contra una fila real. Su unica funcion es dar a `check_password` algo
# valido contra lo que trabajar cuando la fila no tiene hash, de modo que el tiempo de respuesta no
# delate esa circunstancia. Se declara AQUI y no se importa del servicio de autenticacion: la
# direccion de la dependencia correcta es la contraria -la autenticacion consume la custodia-, y
# tomarlo prestado de alla crearia un ciclo el dia que autenticacion delegue en este servicio.
_TEXTO_SENUELO = "custodia-credencial-senuelo-sin-uso-real"

# --- Detalles TECNICOS de los fallos de custodia -------------------------
# No son texto de usuario y por eso no viven en `mensajes.py`, que es el catalogo de literales
# visibles: van al atributo interno de la excepcion y de ahi al log tecnico junto al `traceId`. Hacia
# fuera, los tres se ven como el mismo 500 generico de REQ-054, sin detalle.
DETALLE_HASHER_NO_SOPORTADO = "El hasher activo no tiene correspondencia en el enumerado password_algorithm"
DETALLE_PASSWORD_VACIA = "Se ha solicitado calcular el verificador de una contrasenia vacia"
DETALLE_FALLO_HASHING = "El calculo del hash de la credencial ha fallado"


@lru_cache(maxsize=1)
def _hash_senuelo() -> str:
    """
    Hash senuelo del proceso, calculado de forma PEREZOSA y una sola vez.

    Lo produce `make_password`, que usa el primer hasher de `PASSWORD_HASHERS` (Argon2id), de modo
    que verificarlo cuesta lo mismo -mismo algoritmo y mismos parametros de coste- que verificar el
    hash real de cualquier usuario. Se cachea porque recalcularlo en cada intento gastaria CPU sin
    motivo, y calcularlo en el import encareceria el arranque del servicio.
    """

    return make_password(_TEXTO_SENUELO)


def _algoritmo_vigente() -> str:
    """
    Nombre del algoritmo ACTIVO en el vocabulario del esquema (`argon2id` o `bcrypt`).

    Se deriva del primer hasher de `PASSWORD_HASHERS` -el mismo que usa `make_password`, y que
    `get_hashers()` devuelve en ese orden- para que la columna `password_algorithm` describa el hash
    que de verdad se acaba de calcular y no un literal escrito a mano que podria quedarse obsoleto.

    Raises:
        CustodiaCredencialError: si el hasher activo no esta en `ALGORITMO_POR_HASHER`. Se prefiere
            fallar a inventar un valor: la columna tiene un enumerado CERRADO y cualquier otro valor
            seria rechazado por la CHECK de Oracle o -peor- describiria mal la proteccion aplicada.
    """

    hashers = get_hashers()
    if not hashers:
        raise CustodiaCredencialError(detalle=DETALLE_HASHER_NO_SOPORTADO)

    nombre_clase = type(hashers[0]).__name__
    algoritmo = ALGORITMO_POR_HASHER.get(nombre_clase)
    if algoritmo is None:
        raise CustodiaCredencialError(detalle=DETALLE_HASHER_NO_SOPORTADO)
    return algoritmo


def _algoritmos_configurados() -> set[str]:
    """Prefijos de algoritmo (los de Django: `argon2`, `bcrypt_sha256`...) de los hashers configurados."""

    return {hasher.algorithm for hasher in get_hashers()}


@dataclass(frozen=True, slots=True)
class Verificador:
    """
    Material de credencial YA calculado, listo para persistirse en `usuario`.

    Es el unico vehiculo por el que el hash viaja desde el servicio de hashing hasta la fila: es
    inmutable (`frozen=True`) para que nadie sustituya el material a mitad de la operacion y usa
    `slots=True` para que no admita atributos nuevos -en particular, para que nadie le cuelgue la
    contrasenia en claro «solo un momento».

    `password_salt` es `None` cuando el algoritmo EMBEBE la sal en la cadena del hash, que es el caso
    de Argon2id y de bcrypt. Ver `ServicioCustodiaCredenciales.calcular_verificador`: no es una sal
    ausente, es una sal embebida.
    """

    password_hash: str
    password_salt: str | None
    password_algorithm: str

    def __repr__(self) -> str:
        """
        Representacion con el hash SIEMPRE enmascarado (REQ-063, REQ-076).

        El `repr` generado por `dataclass` volcaria el `password_hash` -y con el la sal embebida- en
        cualquier traza de excepcion, en cualquier `logger.exception` que incluyera el argumento y en
        cualquier sesion de depuracion. Este metodo es la UNICA barrera que lo impide, y por eso la
        mascara no es condicional ni configurable: no hay ningun modo en que esta estructura se
        imprima con el material visible. El algoritmo si se muestra: no es un secreto, es justo el
        dato que hace falta para diagnosticar.
        """

        salt_enmascarada = MASCARA_HASH if self.password_salt else None
        return (
            f"{type(self).__name__}(password_hash={MASCARA_HASH!r}, "
            f"password_salt={salt_enmascarada!r}, "
            f"password_algorithm={self.password_algorithm!r})"
        )


class ServicioCustodiaCredenciales:
    """
    Servicio UNICO de hashing, verificacion y politica de contrasenia (REQ-054, REQ-069).

    Operaciones publicas: `calcular_verificador`, `verificar`, `evaluar_politica`, `establecer` y la
    guardia `exigir_verificador`. `establecer` es el punto por el que pasan los tres flujos que
    establecen contrasenia, y ninguno de ellos escribe `password_hash` por su cuenta.

    Lo que este servicio NO hace, a proposito: no revoca sesiones, no envia correos, no toca el
    contador de intentos fallidos y no emite auditoria. Esas consecuencias del cambio de contrasenia
    las gobiernan sus propios flujos; mezclarlas aqui ataria la custodia a politicas que cambian por
    otros motivos.
    """

    def __init__(self, repositorio: RepositorioHistoricoPassword | None = None, politica: PoliticaContrasenia | None = None) -> None:
        # El repositorio se resuelve de forma PEREZOSA (ver `_historico`): construirlo aqui obligaria
        # a tener el registro de aplicaciones cargado solo para calcular o verificar un hash, que son
        # operaciones que no tocan la base.
        self._repositorio = repositorio
        self._politica = politica if politica is not None else PoliticaContrasenia()

    @property
    def _historico(self) -> RepositorioHistoricoPassword:
        """
        Repositorio del historico de contrasenias, instanciado en su primer uso.

        `RepositorioHistoricoPassword` resuelve el modelo en su constructor, y los modelos no se
        pueden cargar antes de `django.setup()`. Diferirlo hasta aqui mantiene el servicio
        construible -y utilizable para hashing y verificacion- sin el registro de aplicaciones, y no
        cambia nada para quien inyecta su propio repositorio.
        """

        if self._repositorio is None:
            self._repositorio = RepositorioHistoricoPassword()
        return self._repositorio

    def calcular_verificador(self, password: str) -> Verificador:
        """
        Calcula el material de credencial de una contrasenia en claro (REQ-054).

        Usa `make_password`, que aplica el primer hasher de `PASSWORD_HASHERS` (Argon2id) y genera
        una SAL ALEATORIA CRIPTOGRAFICA NUEVA en cada invocacion (`get_random_string`, sembrado por
        `secrets`), embebida en la cadena resultante. De ahi que dos llamadas con la misma
        contrasenia produzcan hashes distintos, que es exactamente lo que se quiere: el hash no es un
        identificador de la contrasenia y no se puede comparar hash contra hash.

        POR QUE `password_salt` QUEDA A `None`. La columna `usuario.password_salt` es NULABLE y su
        regla en el esquema (ARC-110) es «obligatoria solo si el algoritmo no la embebe». Argon2id la
        embebe dentro de `password_hash`, asi que duplicarla en su propia columna no anadiria
        seguridad: multiplicaria por dos los sitios desde los que puede filtrarse y crearia la
        posibilidad de que ambas copias se desincronicen. ESTO NO ES UNA SAL AUSENTE: es una sal
        embebida, y cada usuario tiene la suya.

        A `password` no se le aplica NINGUNA normalizacion -ni `strip()` ni cambio de caja-: los
        espacios y las mayusculas forman parte del secreto, y recortarlos haria que se guardase una
        contrasenia distinta de la que el usuario escribio.

        Args:
            password: contrasenia en claro, no vacia. No se registra, no se copia a ninguna
                excepcion y no sobrevive a esta llamada.

        Returns:
            Verificador: hash, sal (embebida, por tanto `None`) y algoritmo del esquema.

        Raises:
            CustodiaCredencialError: si `password` viene vacia o nula -la politica ya la habria
                rechazado antes, asi que llegar aqui es un defecto de programacion, no un error del
                usuario- o si el calculo del hash falla por cualquier motivo tecnico. El detalle
                queda en el atributo interno de la excepcion; el mensaje visible es el 500 generico
                de REQ-054, sin detalle.
        """

        if not password:
            raise CustodiaCredencialError(detalle=DETALLE_PASSWORD_VACIA)

        algoritmo = _algoritmo_vigente()
        try:
            password_hash = make_password(password)
        except Exception as error:  # noqa: BLE001 - cualquier fallo del hasher es un fallo de custodia
            # El detalle tecnico NO lleva la contrasenia ni material derivado de ella: solo el tipo
            # del fallo, que es lo unico que ayuda a diagnosticar (REQ-063, REQ-076).
            raise CustodiaCredencialError(detalle=f"{DETALLE_FALLO_HASHING}: {type(error).__name__}") from error

        return Verificador(password_hash=password_hash, password_salt=None, password_algorithm=algoritmo)

    def verificar(self, password: str, password_hash: str | None) -> bool:
        """
        Comprueba una contrasenia en claro contra un hash almacenado, en TIEMPO CONSTANTE.

        Delega en `check_password`, que recalcula el hash con la sal embebida del almacenado y los
        compara con `hmac.compare_digest`: el tiempo no depende de cuantos bytes coinciden, de modo
        que la comparacion no filtra informacion por temporizacion (REQ-054, REQ-069).

        Si `password_hash` viene vacio o nulo se verifica IGUALMENTE contra el hash senuelo del
        proceso -mismo algoritmo y mismos parametros de coste-: salir antes responderia en una
        fraccion del tiempo y delataria que esa fila no tiene credencial, que es justo el tipo de
        oraculo que el endurecimiento de la autenticacion prohibe.

        Nunca lanza por contrasenia incorrecta: devolver `False` es el resultado normal de este
        metodo, no un error.

        Args:
            password: contrasenia en claro propuesta, sin normalizar.
            password_hash: hash almacenado; puede ser `None` o vacio.

        Returns:
            bool: `True` solo si la contrasenia corresponde al hash almacenado.
        """

        hash_a_verificar = password_hash if password_hash else _hash_senuelo()
        try:
            return check_password(password, hash_a_verificar)
        except ValueError:
            # El hash almacenado no lo produjo ningun hasher configurado. No se puede verificar, y
            # por tanto la credencial NO coincide. Se registra el hecho sin el valor (REQ-063).
            logger.warning(
                "Verificacion rechazada: el hash almacenado no corresponde a ningun hasher configurado",
                extra={"data": {}},
            )
            return False

    def evaluar_politica(self, password: str, *, usuario: UsuarioEntity) -> list[ReglaIncumplida]:
        """
        Devuelve TODAS las reglas de politica que incumple la contrasenia propuesta (REQ-069).

        Dos mitades, una sola lista: las reglas de forma las evalua `PoliticaContrasenia` (dominio
        puro) y la de no reutilizacion se resuelve aqui, porque exige leer la base. El cliente recibe
        una unica lista de incumplimientos y no puede distinguir cual se resolvio en memoria y cual
        contra Oracle.

        LA LISTA ES COMPLETA, SIEMPRE. El historial se consulta tambien cuando la contrasenia ya
        incumple reglas de forma, aunque eso cueste verificaciones Argon2: AC-PWD-03 exige que el 422
        traiga TODO lo que el usuario debe corregir, y cortar convertiria el cambio de contrasenia en
        una partida de adivinanzas, con un requisito descubierto por intento.

        COMO SE COMPRUEBA LA REUTILIZACION. No se comparan hashes entre si -cada uno tiene su propia
        sal, dos hashes de la misma contrasenia son distintos-: se verifica la contrasenia PROPUESTA
        contra cada hash candidato con `self.verificar`. Los candidatos son el hash VIGENTE del
        usuario mas los `MAXIMO_CONTRASENIAS_HISTORICAS` ultimos del historico, que es la ventana que
        fija REQ-069 y la misma que conserva el trigger de purga.

        Args:
            password: contrasenia propuesta, tal cual la escribio el usuario.
            usuario: fila de `usuario` sobre la que se evalua; aporta `username`, `corporate_email`,
                el hash vigente y el `user_id` con el que se consulta el historico.

        Returns:
            Lista de `ReglaIncumplida` en orden estable y sin duplicados, vacia si la contrasenia es
            aceptable. No contiene la contrasenia, la anterior ni fragmento alguno de ninguna.
        """

        incumplidas = self._politica.evaluar(
            password,
            username=usuario.username,
            corporate_email=usuario.corporate_email,
        )

        if self._esta_reutilizada(password, usuario=usuario):
            incumplidas.append(self._politica.regla_reutilizada())

        return incumplidas

    def establecer(self, usuario: UsuarioEntity, nueva_password: str, *, actor: ContextoSesion | None) -> Verificador:
        """
        Establece la contrasenia del usuario: PUNTO UNICO de los tres flujos (REQ-069).

        Por aqui pasan el cambio propio (EP-005), el primer acceso forzado (EP-006) y el
        restablecimiento por administrador (EP-017). Ninguno de ellos evalua la politica ni calcula
        el hash por su cuenta, y por eso una contrasenia rechazada en uno lo es en los tres.

        ORDEN, Y EL ORDEN IMPORTA:

        1. se evalua la politica COMPLETA; si incumple algo se lanza `PoliticaContraseniaError`
           (422) SIN HABER TOCADO LA BASE, de modo que `password_hash` queda exactamente como estaba
           (AC-PWD-03);
        2. se calcula el verificador nuevo y se comprueba que de verdad es material hasheado por
           este servicio (REQ-054, validacion 1);
        3. dentro de UNA SOLA `transaction.atomic()` se archiva el hash ANTERIOR en
           `usuario_password_historico` y se actualiza la fila de `usuario`. Si una de las dos
           escrituras falla no queda ni una contrasenia nueva sin historial ni un historial con una
           contrasenia que nunca llego a ser vigente (AC-PWD-04).

        El UPDATE se acota con `update_fields` a las columnas de credencial e incluye
        `password_updated_at = utc_now()`, `must_change_password = 'N'` -la contrasenia recien
        establecida ya no esta pendiente de cambio (AC-PWD-01)- y `password_expires_at = None`, que
        retira la caducidad de una credencial temporal que acaba de ser sustituida por una definitiva.

        EL ACTOR NO SE INVENTA (REQ-064). `UsuarioEntity` hereda de `AtribucionMixin` y su `save()`
        exige contexto de sesion. Si se recibe `actor`, la escritura va dentro de
        `contexto_de_sesion(actor)`; si se recibe `None`, se escribe con el contexto YA PUBLICADO por
        el middleware y, si no hubiera ninguno, el mixin falla cerrado con
        `ContextoSesionNoDisponibleError`. En ningun caso se fabrica un actor de relleno: el
        argumento es obligatorio precisamente para que quien llama declare cual de los dos casos es
        el suyo.

        Esta operacion NO revoca sesiones, NO envia correos y NO audita: esas consecuencias son de
        otros flujos.

        Args:
            usuario: fila de `usuario` a la que se le establece la contrasenia.
            nueva_password: contrasenia en claro propuesta. No se registra ni se persiste jamas.
            actor: contexto de sesion con el que atribuir la escritura, o `None` para usar el
                publicado por el middleware.

        Returns:
            Verificador: el material persistido, con el hash enmascarado en su `repr`.

        Raises:
            PoliticaContraseniaError: 422 con la lista completa de reglas incumplidas.
            ContraseniaSinHashearError: si el material a persistir no procede del servicio de hashing.
            CustodiaCredencialError: si falla el calculo del hash.
        """

        reglas = self.evaluar_politica(nueva_password, usuario=usuario)
        if reglas:
            raise PoliticaContraseniaError(reglas)

        verificador = self.calcular_verificador(nueva_password)
        self.exigir_verificador(verificador, password=nueva_password)

        hash_anterior = usuario.password_hash
        with transaction.atomic():
            if hash_anterior:
                self._historico.archivar(user_id=usuario.user_id, password_hash=hash_anterior)
            self._escribir_credencial(usuario, verificador, actor=actor)

        logger.info("Credencial establecida", extra={"data": {"user_id": usuario.user_id}})
        return verificador

    def exigir_verificador(self, verificador: Verificador, *, password: str | None = None) -> None:
        """
        Rechaza cualquier intento de persistir material que NO venga del servicio de hashing (REQ-054).

        Es la validacion 1 de REQ-054 convertida en guardia activa: la persistencia no CONFIA en que
        quien la llama haya hasheado antes, lo COMPRUEBA. Una contrasenia guardada en claro no se
        detecta leyendo la tabla despues -ya esta escrita, ya se ha filtrado-: se evita no
        escribiendola nunca.

        Tres comprobaciones sobre el valor a persistir:

        1. tiene la forma de un hash codificado de Django (contiene `$`, que separa algoritmo,
           parametros, sal y hash);
        2. su prefijo de algoritmo esta entre los hashers CONFIGURADOS del proyecto, de modo que un
           hash producido por un algoritmo ajeno tampoco pasa;
        3. no coincide con la contrasenia en claro, cuando esta se aporta. La comparacion usa
           `hmac.compare_digest` y no `==`: este modulo no compara material de credencial con el
           operador de igualdad en ningun sitio. Se comparan los BYTES UTF-8 y no las cadenas,
           porque `compare_digest` rechaza el texto con caracteres no ASCII y una contrasenia con
           acentos o enies es tan valida como cualquier otra.

        Tambien se comprueba que `password_algorithm` sea uno de los valores del enumerado cerrado
        del esquema: persistir otro haria saltar la CHECK de Oracle y, antes de eso, describiria mal
        la proteccion aplicada.

        El valor sospechoso NO se copia a la excepcion, no se registra y no viaja a ninguna traza
        (REQ-063, REQ-076).

        Raises:
            ContraseniaSinHashearError: si el material no supera cualquiera de las comprobaciones.
                Hacia fuera es un 500 generico, identico al resto de fallos de custodia.
        """

        password_hash = verificador.password_hash or ""

        if SEPARADOR_HASH not in password_hash:
            raise ContraseniaSinHashearError()

        prefijo = password_hash.split(SEPARADOR_HASH, 1)[0]
        if prefijo not in _algoritmos_configurados():
            raise ContraseniaSinHashearError()

        if verificador.password_algorithm not in set(ALGORITMO_POR_HASHER.values()):
            raise ContraseniaSinHashearError()

        if password is not None and hmac.compare_digest(password_hash.encode("utf-8"), password.encode("utf-8")):
            raise ContraseniaSinHashearError()

    def _esta_reutilizada(self, password: str, *, usuario: UsuarioEntity) -> bool:
        """
        Indica si la contrasenia propuesta coincide con alguna de las ultimas usadas (AC-PWD-04).

        Los candidatos son el hash VIGENTE del usuario -la contrasenia que esta a punto de dejar de
        serlo cuenta como usada- y los ultimos `MAXIMO_CONTRASENIAS_HISTORICAS` del historico, leidos
        de la tabla Oracle y nunca de una copia en memoria. Se corta en la primera coincidencia: una
        vez sabido que se reutiliza, verificar el resto solo gastaria CPU y la regla que se anade es
        UNA, sin decir jamas con cual coincidio.
        """

        candidatos: list[str] = []
        if usuario.password_hash:
            candidatos.append(usuario.password_hash)
        candidatos.extend(self._historico.hashes_recientes(usuario.user_id, limite=MAXIMO_CONTRASENIAS_HISTORICAS))

        return any(self.verificar(password, candidato) for candidato in candidatos)

    def _escribir_credencial(self, usuario: UsuarioEntity, verificador: Verificador, *, actor: ContextoSesion | None) -> None:
        """
        Escribe las columnas de credencial de `usuario` con la atribucion del actor.

        El UPDATE se acota a `CAMPOS_CREDENCIAL`: esta operacion establece una contrasenia y no
        reinterpreta ninguna otra columna de la fila (ni el estado de la cuenta, ni el contador de
        intentos fallidos, ni el bloqueo).
        """

        usuario.password_hash = verificador.password_hash
        usuario.password_salt = verificador.password_salt
        usuario.password_algorithm = verificador.password_algorithm
        usuario.password_updated_at = utc_now()
        usuario.must_change_password = INDICADOR_NO
        usuario.password_expires_at = None

        if actor is None:
            # Sin actor explicito se usa el contexto publicado por el middleware; si no hubiera
            # ninguno, `AtribucionMixin.save()` falla cerrado y la transaccion se deshace.
            usuario.save(update_fields=list(CAMPOS_CREDENCIAL))
            return

        with contexto_de_sesion(actor):
            usuario.save(update_fields=list(CAMPOS_CREDENCIAL))


__all__ = [
    "ALGORITMO_POR_DEFECTO",
    "ALGORITMO_POR_HASHER",
    "CAMPOS_CREDENCIAL",
    "INDICADOR_NO",
    "INDICADOR_SI",
    "ServicioCustodiaCredenciales",
    "Verificador",
]
