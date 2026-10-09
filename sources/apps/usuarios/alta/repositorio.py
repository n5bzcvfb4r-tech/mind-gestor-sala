"""
Acceso a datos del ALTA de usuario (EP-007, REQ-037, REQ-045, tabla `usuario`).

Tres operaciones y ninguna mas: comprobar si el correo ya identifica a alguien, escribir la fila
del usuario nuevo y comprobar que el rol elegido existe y esta vigente en el catalogo. No valida
formato, no genera credenciales, no envia correos y no construye respuestas: todo eso es del
servicio y de la vista.

LOS PREDICADOS VIAJAN A ORACLE, NUNCA A MEMORIA
-----------------------------------------------
Ni `existe_correo` ni `existe_rol_vigente` materializan filas: usan `.exists()`, que Oracle
resuelve con un `SELECT 1 ... FETCH FIRST 1 ROW ONLY`. El anti-patron que este modulo evita a
proposito es el de «traer el censo y buscar en Python»: ademas de crecer linealmente con el numero
de usuarios, obligaria a materializar columnas de credencial que no pintan nada en una
comprobacion de unicidad (REQ-063).

EL BORRADO NO EXISTE
--------------------
Hereda de `RepositorioBase`, que no publica -ni puede publicar- ninguna operacion de borrado
fisico (REQ-047). Este repositorio solo inserta y lee.

El modelo se resuelve de forma PEREZOSA en el constructor porque importar modelos a nivel de
modulo revienta con `AppRegistryNotReady` antes de `django.setup()`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from django.db import models
from django.db.models.functions import Lower, Trim

from apps.core.repositorios import RepositorioBase


if TYPE_CHECKING:  # pragma: no cover - solo tipado: evita importar modelos antes de django.setup()
    from apps.core.models import UsuarioEntity


#: REQ-037 regla 4: el usuario nace ACTIVO. Es un valor del CHECK cerrado `ESTADOS_USUARIO`.
ESTADO_ACTIVO = "ACTIVO"

#: Indicador 'Y'/'N' de Oracle: `must_change_password` es CHAR(1) en el esquema T.5, no un booleano.
INDICADOR_SI = "Y"

#: Valor de `cat_rol.is_active` que marca un rol vigente. La baja de un elemento de catalogo es
#: LOGICA (REQ-047), asi que «existe» no basta: hay que mirar tambien que siga activo.
ROL_VIGENTE = "Y"


class RepositorioAltaUsuario(RepositorioBase):
    """
    Escritura y comprobaciones previas del alta de usuario sobre `UsuarioEntity` (EP-007).

    El modelo llega por parametro solo para los tests; en produccion se resuelve solo.
    """

    def __init__(self, modelo: type[models.Model] | None = None) -> None:
        if modelo is None:
            from apps.core.models import UsuarioEntity

            modelo = UsuarioEntity
        super().__init__(modelo)

    def existe_correo(self, corporate_email_normalizado: str) -> bool:
        """
        Indica si ya hay un usuario cuyo correo corporativo coincide con el indicado.

        LA EXPRESION ES LA DEL INDICE, LITERALMENTE
        -------------------------------------------
        La anotacion `Lower(Trim("corporate_email"))` replica EXACTAMENTE la expresion del indice
        unico funcional del DDL: `CREATE UNIQUE INDEX ux_usuario_email_ci ON usuario
        (LOWER(TRIM(corporate_email)))` (`05-usuario.xml`). Es deliberado y no es cosmetico: si
        esta comprobacion usara otra cosa -por ejemplo `corporate_email__iexact`, que no recorta
        espacios- la aplicacion y la base podrian discrepar sobre si dos correos son el mismo, y
        entonces habria altas que pasan el chequeo previo y revientan contra la restriccion, o peor,
        altas que el chequeo rechaza con 409 aunque la base las hubiera aceptado.

        EL PREDICADO SE RESUELVE EN SQL
        -------------------------------
        `.exists()` se traduce a una consulta que para en cuanto encuentra la primera fila. No se
        trae ninguna fila a Python, lo que ademas evita materializar `password_hash` y companiia
        (REQ-063).

        ESTO NO SUSTITUYE A LA RESTRICCION DE LA BASE
        ---------------------------------------------
        Entre este `SELECT` y el `INSERT` posterior caben dos altas simultaneas del mismo correo:
        ambas verian «no existe» y ambas escribirian. El arbitro de esa carrera es el indice unico,
        y quien llama debe capturar tambien el error de integridad y traducirlo al MISMO 409
        (`CorreoDuplicadoError`). Esta comprobacion existe para dar el mensaje util en el caso
        normal, no para sustituir a la base.

        Args:
            corporate_email_normalizado: correo YA pasado por
                `apps.usuarios.alta.normalizacion.normalizar_correo` (minusculas, sin espacios).
                Pasarle el valor en bruto haria que la comparacion fallase en silencio.

        Returns:
            `True` si el correo ya identifica a un usuario del censo.
        """

        return (
            self.modelo.objects.annotate(_correo_ci=Lower(Trim("corporate_email")))
            .filter(_correo_ci=corporate_email_normalizado)
            .exists()
        )

    def existe_username(self, username: str) -> bool:
        """
        Indica si el identificador de acceso ya esta ocupado, ignorando mayusculas y minusculas.

        LA EXPRESION ES LA DEL INDICE, LITERALMENTE
        -------------------------------------------
        `Lower("username")` replica EXACTAMENTE la expresion del indice unico funcional del DDL:
        `CREATE UNIQUE INDEX ux_usuario_username_ci ON usuario (LOWER(username))`
        (`06-usuario-credenciales.xml`). Igual que en `existe_correo`, la igualdad no es cosmetica:
        si esta comprobacion midiera otra cosa -por ejemplo comparando el texto tal cual-, el
        servicio creeria libre un identificador que la base va a rechazar y el alta moriria con un
        error de integridad crudo en vez de probar el siguiente candidato.

        OJO: ESTA EXPRESION NO LLEVA `Trim`, Y LA DEL CORREO SI
        -------------------------------------------------------
        No es un descuido ni una inconsistencia que haya que «arreglar»: el DDL define los dos
        indices de forma distinta (`LOWER(TRIM(corporate_email))` frente a `LOWER(username)`), y
        cada comprobacion replica el suyo. Aniadir aqui un `Trim` que el indice no tiene volveria a
        abrir justo la discrepancia que este metodo existe para cerrar.

        EL PREDICADO SE RESUELVE EN SQL
        -------------------------------
        `.exists()` para en la primera fila y no materializa ninguna columna de credencial
        (REQ-063).

        ESTO NO SUSTITUYE A LA RESTRICCION DE LA BASE
        ---------------------------------------------
        Entre este `SELECT` y el `INSERT` caben dos altas que deriven el mismo identificador. El
        arbitro de esa carrera es el indice unico; esta consulta existe para que el caso normal
        elija un candidato libre a la primera, no para sustituir a la base.

        Args:
            username: identificador de acceso candidato. Se compara en minusculas, de modo que
                `A.Lopez` y `a.lopez` se consideran el MISMO identificador, igual que hace Oracle.

        Returns:
            `True` si ya hay un usuario con ese identificador de acceso.
        """

        return self.modelo.objects.annotate(_usuario_ci=Lower("username")).filter(_usuario_ci=username.lower()).exists()

    def crear(
        self,
        *,
        full_name: str,
        corporate_email: str,
        role_code: str,
        username: str,
        password_hash: str,
        password_salt: str | None,
        password_algorithm: str,
        password_expires_at: datetime | None,
        credential_issued_at: datetime | None,
    ) -> UsuarioEntity:
        """
        Inserta la fila del usuario nuevo y la devuelve con su `user_id` ya asignado.

        LA ATRIBUCION NO SE PASA: LA SELLA EL MIXIN
        -------------------------------------------
        Esta firma NO acepta `created_at` ni `created_by`, y es a proposito. Los escribe
        `AtribucionMixin.save()` a partir del contexto de sesion y del reloj del servidor (REQ-048,
        REQ-064): el actor del alta es quien tiene la sesion abierta, no quien lo diga el payload, y
        la fecha es la del servidor, no una que pueda antedatar un cliente. Admitirlos como
        parametros abriria justamente el agujero que el mixin cierra, porque bastaria con que un
        llamador los reenviase desde la peticion.

        ESTADO INICIAL DE LA CUENTA (REQ-037 regla 4, REQ-038)
        ------------------------------------------------------
        * `status = "ACTIVO"`: el usuario nace operativo y aparece de inmediato en el censo (USR-03).
        * `must_change_password = "Y"`: la credencial inicial es de un solo uso en la practica; el
          usuario la cambia en su primer acceso y asi el secreto que viajo por correo deja de valer.
        * `failed_password_attempts = 0`: contador de la politica de bloqueo (REQ-055) a cero. Se
          fija explicitamente en vez de confiar en el `default` del modelo porque la columna existe
          en la base con su propio valor por defecto y el alta no debe depender de cual gane.
        * `password_updated_at = credential_issued_at`: la credencial inicial es la primera que tiene
          la cuenta, asi que el instante en que se emitio ES el instante en que se fijo. Darle otro
          reloj haria que la antiguedad de la contrasenia naciera ya descuadrada respecto a su
          caducidad.

        `role_code_id=role_code` asigna la clave ajena por su CODIGO, sin ir a leer la fila de
        `cat_rol`: la vigencia del rol ya se comprobo con `existe_rol_vigente` y resolver el objeto
        aqui solo anadiria un SELECT por alta.

        `save()` Y NO `bulk_create()`
        -----------------------------
        `user_id` lo genera la IDENTITY de Oracle. El `INSERT` normal de `save()` recupera la clave
        generada y la deja puesta en la instancia, de modo que la entidad devuelta ya sirve para
        componer la respuesta de EP-007 y para el evento `UserCreated`. `bulk_create()` no garantiza
        esa devolucion en todos los backends y, ademas, saltaria el `save()` del modelo y con el la
        atribucion del mixin: el usuario quedaria sin `created_by`.

        Args:
            full_name: nombre YA normalizado (`normalizar_nombre`).
            corporate_email: correo YA en su forma canonica (`normalizar_correo`); es el valor que
                se persiste y el mismo con el que se comprobo la unicidad (REQ-045, PBT-001).
            role_code: codigo del rol del catalogo `cat_rol`, ya validado como vigente.
            username: identificador de acceso del usuario.
            password_hash: resumen de la credencial inicial. NUNCA la contrasenia en claro (REQ-063).
            password_salt: sal del resumen, o `None` si el algoritmo la lleva embebida (argon2id).
            password_algorithm: algoritmo del resumen, del enumerado `ALGORITMOS_PASSWORD`.
            password_expires_at: caducidad de la credencial inicial.
            credential_issued_at: instante de emision de la credencial inicial.

        Returns:
            La entidad persistida, con `user_id` asignado por la base.
        """

        entidad = self.modelo(
            full_name=full_name,
            corporate_email=corporate_email,
            role_code_id=role_code,
            status=ESTADO_ACTIVO,
            username=username,
            password_hash=password_hash,
            password_salt=password_salt,
            password_algorithm=password_algorithm,
            password_updated_at=credential_issued_at,
            must_change_password=INDICADOR_SI,
            credential_issued_at=credential_issued_at,
            password_expires_at=password_expires_at,
            failed_password_attempts=0,
        )
        # `save()` dispara `AtribucionMixin.save()`, que es quien pone `created_at`/`created_by`
        # desde el contexto de sesion, y recupera el `user_id` que genero la IDENTITY de Oracle.
        entidad.save()
        return entidad

    def existe_rol_vigente(self, role_code: str) -> bool:
        """
        Indica si el codigo de rol pertenece al catalogo `cat_rol` y sigue activo.

        EL CATALOGO SE LEE DE LA BASE, NUNCA SE CODIFICA EN PYTHON
        ----------------------------------------------------------
        Los roles son datos, no codigo: las filas de `cat_rol` las aportan las semillas del
        changelog y son su UNICA fuente de verdad. Replicarlos aqui como un enum o una tupla de
        constantes crearia una segunda fuente que se desalinearia en cuanto se diese de alta o de
        baja un rol, y el alta aceptaria roles que ya no existen -o rechazaria los nuevos- sin que
        nadie tocase este fichero. Por eso la comprobacion es una consulta y el literal del rechazo
        (`mensajes.ROL_NO_VALIDO`) no enumera ningun codigo.

        La baja de un elemento de catalogo es LOGICA (REQ-047), asi que no basta con que la fila
        exista: `is_active = 'Y'` es lo que distingue un rol asignable de uno retirado que se
        conserva para no romper el historico de quienes lo tuvieron.

        `RolEntity` se importa DENTRO del metodo por la misma razon que el modelo del constructor:
        a nivel de modulo reventaria con `AppRegistryNotReady`.

        Args:
            role_code: codigo de rol recibido en la peticion de alta.

        Returns:
            `True` si el rol existe en `cat_rol` y esta vigente.
        """

        from apps.core.models import RolEntity

        return RolEntity.objects.filter(role_code=role_code, is_active=ROL_VIGENTE).exists()


__all__ = [
    "ESTADO_ACTIVO",
    "INDICADOR_SI",
    "ROL_VIGENTE",
    "RepositorioAltaUsuario",
]
