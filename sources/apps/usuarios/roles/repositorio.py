"""
Acceso a datos del CAMBIO DE ROL de un usuario (REQ-005, AC-ROL-01..AC-ROL-05, tabla `usuario_historico`).

Lee y escribe tres cosas y ninguna mas: la fila del usuario (`usuario.role_code` y su sello de
cambio), las entradas de rol del historico append-only y la MARCA DE RECARGA DE PERMISOS de las
sesiones vivas. No valida roles contra reglas de negocio, no decide si el cambio procede, no
construye respuestas y NO abre ni cierra transacciones: la frontera transaccional es del servicio.

LAS SESIONES VIVAS SE MARCAN, NO SE REVOCAN
--------------------------------------------
El cambio de rol NO cierra la sesion de la persona afectada: le sella
`sesion_usuario.permissions_refreshed_at` para que su siguiente peticion recargue rol y
capacidades (ver `refrescar_permisos_de_sesiones`). Por eso este modulo no publica -ni debe
publicar- ninguna operacion de revocacion por cambio de rol.

EL HISTORICO ES APPEND-ONLY: LA VIGENCIA SE LEE, NO SE REESCRIBE
-----------------------------------------------------------------
`usuario_historico` lleva `RegistroInmutableMixin` y, en la base, el trigger de sentencia
`trg_usuario_historico_inmutable`, que rechaza con ORA-20001 CUALQUIER `UPDATE` o `DELETE` sobre la
tabla -incluso los que no afectan a ninguna fila-. Por eso aqui NO existe ninguna operacion que
cierre la asignacion anterior reescribiendo su fila: no podria funcionar contra Oracle. La cadena de
vigencias se reconstruye LEYENDO `valid_from` / `valid_to`, tal y como hace la consulta de
solapamiento del changeset `ddl-0.0.1-07-06`.

La semantica temporal la fija `ck_usuario_hist_coherencia` (`07-usuario-historico.xml`) y este
modulo la respeta literalmente en las dos unicas formas de fila de rol que admite:

* ALTA (asignacion inicial): `previous_role_code` nulo, `new_role_code` informado y `valid_to` NULO.
* CAMBIO: `previous_role_code` y `new_role_code` informados y distintos, con
  `valid_to = valid_from`, porque el cierre de la asignacion anterior y la apertura de la nueva
  ocurren EN EL MISMO INSTANTE.

`previous_status`, `new_status` y `reason_code` van siempre vacios en las filas de rol: lo exigen
`ck_usuario_hist_coherencia` y `ck_usuario_hist_motivo`.

El indice unico `ux_usuario_hist_rol_sin_cierre` deja pasar UNA sola fila de rol por usuario con
`valid_to` nulo -la del alta-, que es la forma en que la base materializa el «exactamente un rol
vigente» de AC-ROL-01 y de REQ-005 RN-02.

LOS PREDICADOS, EL ORDEN Y EL TROCEO VIAJAN A ORACLE
-----------------------------------------------------
Ni el historico, ni el recuento de asignaciones vigentes, ni la marca de recarga de las sesiones se
resuelven en memoria. La paginacion es un troceo del queryset (`OFFSET .. FETCH NEXT` en Oracle), el
recuento de asignaciones abiertas es un `COUNT(*)` con un subconsulta correlacionada (`EXISTS`) y la
marca de recarga es un unico `UPDATE` en bloque: traer las filas a Python y recorrerlas creceria con
el volumen y abriria ventanas entre la lectura y la escritura.

EL BORRADO NO EXISTE
--------------------
Hereda de `RepositorioBase`, que no publica -ni puede publicar- ninguna operacion de borrado fisico
(REQ-047). Las sesiones permanecen como evidencia del acceso.

Los modelos se resuelven de forma PEREZOSA dentro de los metodos porque importarlos a nivel de
modulo revienta con `AppRegistryNotReady` antes de `django.setup()`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

from django.db import models
from django.db.models import Exists, OuterRef, Q

from apps.core.repositorios import RepositorioBase


if TYPE_CHECKING:  # pragma: no cover - solo tipado: evita importar modelos antes de django.setup()
    from apps.core.models import UsuarioEntity, UsuarioHistoricoEntity


#: Estado de cuenta operativa del CHECK de `usuario.status`. El cambio de rol solo tiene sentido
#: sobre una cuenta ACTIVO; quien lo HACE CUMPLIR es el servicio, aqui es solo el literal del
#: enumerado para que no se escriba a mano en dos sitios distintos.
ESTADO_USUARIO_ACTIVO = "ACTIVO"

#: Valor de `cat_rol.is_active` que marca un rol vigente. La baja de un elemento de catalogo es
#: LOGICA (REQ-047): que la fila exista no basta, tiene que seguir activa para ser asignable.
ROL_VIGENTE = "Y"

#: Valor de `usuario_historico.change_type` de las entradas de rol, del CHECK cerrado
#: `ck_usuario_hist_change_type` (`'ROL'` | `'ESTADO'`).
CAMBIO_DE_ROL = "ROL"


class RepositorioRolesUsuario(RepositorioBase):
    """
    Lectura y escritura del rol vigente de un usuario y de su traza historica (REQ-005, AC-ROL-01).

    El modelo llega por parametro solo para los tests; en produccion se resuelve solo.
    """

    def __init__(self, modelo: type[models.Model] | None = None) -> None:
        if modelo is None:
            from apps.core.models import UsuarioEntity

            modelo = UsuarioEntity
        super().__init__(modelo)

    def obtener_usuario(self, user_id: int) -> UsuarioEntity | None:
        """
        Devuelve el usuario por su identificador, o `None` si no existe.

        No lleva `select_related` del rol a proposito: quien necesite la DENOMINACION del rol la pide
        con `nombre_de_rol`, que resuelve una unica fila de catalogo. Arrastrar el JOIN en todas las
        lecturas solo para que a veces haga falta el nombre encarece tambien las que no lo usan.

        Args:
            user_id: identificador del usuario destino del cambio de rol.

        Returns:
            La entidad, o `None` si el identificador no corresponde a ningun usuario (404 del
            servicio, que es quien puede decidirlo).
        """

        return self.modelo.objects.filter(user_id=user_id).first()

    def obtener_usuario_para_actualizar(self, user_id: int) -> UsuarioEntity | None:
        """
        Devuelve el usuario con su fila BLOQUEADA (`SELECT ... FOR UPDATE`), o `None` si no existe.

        SERIALIZA LOS CAMBIOS DE ROL CONCURRENTES
        ------------------------------------------
        Dos peticiones simultaneas sobre el MISMO usuario leerian el mismo rol anterior y escribirian
        dos entradas de historico con el mismo `previous_role_code`: la cadena de vigencias quedaria
        bifurcada y el rol vigente de `usuario` dependeria de cual escribiese la ultima. El bloqueo
        de fila hace que la segunda espere a que la primera confirme y lea YA el rol nuevo.

        DEBE INVOCARSE SIEMPRE DENTRO DE `transaction.atomic()`
        -------------------------------------------------------
        El bloqueo vive y muere con la transaccion; fuera de una, Django lanza
        `TransactionManagementError`. Abrirla es del SERVICIO, que es quien conoce la unidad de
        trabajo completa (historico + usuario + sesiones): este repositorio no hace `commit` ni
        `rollback`.

        Args:
            user_id: identificador del usuario destino del cambio de rol.

        Returns:
            La entidad bloqueada hasta el final de la transaccion, o `None` si no existe.
        """

        return self.modelo.objects.select_for_update().filter(user_id=user_id).first()

    def existe_rol_vigente(self, role_code: str) -> bool:
        """
        Indica si el codigo de rol pertenece al catalogo `cat_rol` y sigue activo.

        EL CATALOGO SE LEE DE LA BASE, NUNCA SE CODIFICA EN PYTHON. Las filas de `cat_rol` las
        aportan las semillas del changelog y son su UNICA fuente de verdad: replicarlas aqui como un
        enum crearia una segunda fuente que se desalinearia en cuanto se diese de alta o de baja un
        rol, y el cambio aceptaria roles retirados -o rechazaria los nuevos- sin que nadie tocase
        este fichero.

        La baja de catalogo es LOGICA (REQ-047), asi que `is_active = 'Y'` es lo que distingue un rol
        asignable de uno retirado que se conserva para no romper el historico de quienes lo tuvieron.

        `.exists()` se resuelve en SQL y no materializa ninguna fila.

        Args:
            role_code: codigo de rol recibido en la peticion.

        Returns:
            `True` si el rol existe en `cat_rol` y esta vigente.
        """

        from apps.core.models import RolEntity

        return RolEntity.objects.filter(role_code=role_code, is_active=ROL_VIGENTE).exists()

    def nombre_de_rol(self, role_code: str) -> str | None:
        """
        Devuelve la denominacion del rol en el catalogo, o `None` si el codigo no existe.

        Es un dato de PRESENTACION para la respuesta del cambio de rol: el valor persistido en
        `usuario.role_code` y en el historico es siempre el CODIGO, nunca el nombre, de modo que
        renombrar un rol en `cat_rol` no reescribe ni una sola fila transaccional.

        No filtra por `is_active`: el nombre tambien hay que poder leerlo de un rol ya retirado, por
        ejemplo para rotular el `previous_role_code` de una entrada antigua del historico.

        `values_list(...).first()` proyecta UNA columna: no instancia la entidad entera.

        Args:
            role_code: codigo de rol del catalogo `cat_rol`.

        Returns:
            La denominacion, o `None` si el codigo no esta en el catalogo.
        """

        from apps.core.models import RolEntity

        return RolEntity.objects.filter(role_code=role_code).values_list("role_name", flat=True).first()

    def rol_vigente_en_historico(self, user_id: int) -> str | None:
        """
        Devuelve el codigo de rol que la cadena del historico deja ABIERTO para el usuario.

        Es el `new_role_code` de la ULTIMA entrada de rol, leyendo la cadena por
        `(valid_from, history_id)` DESCENDENTE. El desempate por `history_id` no es decorativo: dos
        cambios escritos en el mismo instante comparten `valid_from` -la semantica de T.5 es que el
        cierre de la asignacion anterior y la apertura de la nueva ocurren a la vez- y sin el
        desempate el «ultimo» dependeria del orden fisico de las filas, que Oracle no garantiza. Es
        exactamente el mismo criterio de orden que usa la consulta de solapamiento del changeset
        `ddl-0.0.1-07-06`.

        Solo se consideran las entradas de ROL: las de ESTADO (baja y reactivacion) viven en la
        misma tabla y no mueven el rol.

        `values_list(...).first()` proyecta UNA columna y trae UNA fila: no instancia la entidad.

        Args:
            user_id: identificador del usuario.

        Returns:
            El codigo del rol vigente segun el historico, o `None` si el usuario no tiene ninguna
            entrada de rol (nunca se le asigno uno por este camino).
        """

        from apps.core.models import UsuarioHistoricoEntity

        return (
            UsuarioHistoricoEntity.objects.filter(user_id=user_id, change_type=CAMBIO_DE_ROL)
            .order_by("-valid_from", "-history_id")
            .values_list("new_role_code", flat=True)
            .first()
        )

    def contar_asignaciones_sin_cierre(self, user_id: int) -> int:
        """
        Cuenta las asignaciones de rol del usuario que NINGUNA fila posterior ha cerrado.

        ES EL ORACULO DE AC-ROL-01: «una consulta a BD devuelve 1 y solo 1 rol vigente para ese
        usuario». Para un usuario dado de alta por el sistema el resultado es SIEMPRE 1; es 0 solo
        si nunca se le asigno rol, y un valor MAYOR QUE 1 significa que la cadena de vigencias se ha
        roto y que hay dos asignaciones coexistiendo.

        LA MISMA SEMANTICA QUE LA CONSULTA DE SOLAPAMIENTO DE `ddl-0.0.1-07-06`
        -----------------------------------------------------------------------
        En `usuario_historico` una fila de rol ABRE la asignacion de su `new_role_code` en su
        `valid_from`, y esa asignacion la CIERRA el `valid_to` de la SIGUIENTE fila de rol del mismo
        usuario (lo que el changeset reconstruye con `LEAD(valid_to) OVER (PARTITION BY user_id
        ORDER BY valid_from, history_id)`). Por tanto una asignacion esta VIGENTE si y solo si su
        fila NO tiene ninguna fila de rol posterior. Eso es lo que se cuenta aqui, y por eso NO basta
        con mirar `valid_to IS NULL`: esa lectura responde a «cuantas filas no cierran a ninguna
        anterior» -la del alta-, que es una pregunta distinta.

        EL DESEMPATE POR `history_id` ES OBLIGATORIO
        --------------------------------------------
        «Posterior» no es solo `valid_from` mayor: dos cambios en el mismo instante comparten
        `valid_from` y, sin desempatar por la clave, NINGUNA de las dos filas tendria posterior y las
        DOS se contarian como abiertas, dando un falso 2. De ahi el predicado
        `valid_from > OUTER.valid_from OR (valid_from = OUTER.valid_from AND history_id >
        OUTER.history_id)`, que es el mismo orden total del changeset.

        TODO SE RESUELVE EN LA BASE. El `EXISTS` correlacionado y el `COUNT(*)` viajan a Oracle en
        una sola sentencia: no se materializa ni una fila en Python.

        Args:
            user_id: identificador del usuario.

        Returns:
            Numero de asignaciones de rol del usuario que siguen vigentes.
        """

        from apps.core.models import UsuarioHistoricoEntity

        filas_de_rol = UsuarioHistoricoEntity.objects.filter(user_id=user_id, change_type=CAMBIO_DE_ROL)

        # Subconsulta correlacionada: ¿hay alguna fila de rol del MISMO usuario estrictamente
        # posterior a la de fuera, en el orden total `(valid_from, history_id)`?
        posterior = filas_de_rol.filter(
            Q(valid_from__gt=OuterRef("valid_from")) | Q(valid_from=OuterRef("valid_from"), history_id__gt=OuterRef("history_id"))
        )

        return filas_de_rol.annotate(tiene_posterior=Exists(posterior)).filter(tiene_posterior=False).count()

    def registrar_asignacion_inicial(
        self,
        *,
        user_id: int,
        role_code: str,
        actor_user_id: int,
        instante: datetime,
    ) -> UsuarioHistoricoEntity:
        """
        Escribe la entrada de ALTA de rol: abre la primera asignacion y no cierra ninguna anterior.

        LA FORMA DE LA FILA LA IMPONE LA BASE
        --------------------------------------
        `ck_usuario_hist_coherencia` solo admite esta combinacion para una entrada de rol sin valor
        anterior: `previous_role_code` NULO y `valid_to` NULO. Informar `valid_to` aqui -aunque fuese
        con el mismo instante- rompe la CHECK, y dejar `previous_role_code` a algo distinto de nulo
        exigiria cerrar una asignacion que todavia no existe. `previous_status`, `new_status` y
        `reason_code` van vacios porque son la dimension de ESTADO, no la de rol.

        Esta es ademas la UNICA fila de rol del usuario que puede quedar con `valid_to` nulo
        (`ux_usuario_hist_rol_sin_cierre`): un segundo intento de alta sobre el mismo usuario lo
        rechaza la base con violacion de indice unico, no esta funcion.

        LAS CLAVES AJENAS SE ASIGNAN POR SU `attname`
        ----------------------------------------------
        `user_id=`, `new_role_code_id=` y `changed_by_id=` escriben la columna directamente. Pasar la
        entidad relacionada obligaria a leerla antes, anadiendo un `SELECT` por cada entrada que no
        aporta nada: la existencia del usuario y la vigencia del rol ya se comprobaron.

        LA ATRIBUCION LA RESELLA EL MIXIN (DESVIACION DOCUMENTADA)
        -----------------------------------------------------------
        `changed_by` y `changed_at` se informan aqui con el actor y el instante recibidos, pero
        `AtribucionMixin.save()` los SOBRESCRIBE con el actor del contexto de sesion y con su propia
        lectura de `utc_now()` (REQ-048, REQ-064), y ese mixin no se toca. En la practica el actor
        coincide -el servicio pasa el de la sesion-, mientras que `changed_at` puede diferir del
        `instante` en milisegundos. Es INOCUO para las invariantes del esquema: las CHECK de
        coherencia comparan `valid_to` con `valid_from`, y esas dos columnas si conservan exactamente
        el `datetime` recibido, que es lo que mantiene contigua la cadena de vigencias.

        Args:
            user_id: usuario al que se le asigna el rol.
            role_code: codigo del rol asignado, ya comprobado como vigente.
            actor_user_id: usuario de la sesion que realiza la asignacion.
            instante: marca temporal UTC naive comun a toda la operacion.

        Returns:
            La entrada persistida, con su `history_id` ya asignado por la IDENTITY de Oracle.
        """

        from apps.core.models import UsuarioHistoricoEntity

        entrada = UsuarioHistoricoEntity(
            user_id=user_id,
            change_type=CAMBIO_DE_ROL,
            previous_role_code_id=None,
            new_role_code_id=role_code,
            previous_status=None,
            new_status=None,
            reason_code_id=None,
            valid_from=instante,
            valid_to=None,
            changed_by_id=actor_user_id,
            changed_at=instante,
        )
        entrada.save()
        return entrada

    def registrar_cambio_de_rol(
        self,
        *,
        user_id: int,
        previous_role_code: str,
        new_role_code: str,
        actor_user_id: int,
        instante: datetime,
    ) -> UsuarioHistoricoEntity:
        """
        Escribe la entrada de CAMBIO de rol: cierra la asignacion anterior y abre la nueva.

        `valid_to = valid_from = instante` NO ES UNA REDUNDANCIA
        --------------------------------------------------------
        Es exactamente lo que exige `ck_usuario_hist_coherencia` para una entrada de rol con valor
        anterior, y traduce la semantica de T.5: `valid_to` cierra la asignacion de
        `previous_role_code` y `valid_from` abre la de `new_role_code`, y ambas cosas ocurren en el
        MISMO instante. Por eso los dos campos reciben el MISMO objeto `datetime` y no dos lecturas
        de reloj: con dos lecturas distintas la cadena tendria un hueco (o un solapamiento) de unos
        microsegundos y la consulta de solapamiento del changeset `ddl-0.0.1-07-06`, que es guardia
        permanente del despliegue, dejaria de devolver 0 filas.

        La fila anterior NO se toca: el historico es append-only (ver el docstring del modulo). Como
        esta entrada si lleva `valid_to`, el indice `ux_usuario_hist_rol_sin_cierre` la ignora y la
        unica fila sin cierre sigue siendo la del alta.

        `ck_usuario_hist_valor_distinto` rechaza ademas que el rol nuevo coincida con el anterior:
        quien debe devolver el error de usuario en ese caso es el servicio, antes de llegar aqui.

        Sobre la atribucion vale, termino a termino, lo dicho en `registrar_asignacion_inicial`: la
        sella `AtribucionMixin.save()` desde el contexto de sesion.

        Args:
            user_id: usuario al que se le cambia el rol.
            previous_role_code: codigo del rol que tenia hasta este instante.
            new_role_code: codigo del rol nuevo, ya comprobado como vigente y distinto del anterior.
            actor_user_id: usuario de la sesion que realiza el cambio.
            instante: marca temporal UTC naive comun a toda la operacion.

        Returns:
            La entrada persistida, con su `history_id` ya asignado por la IDENTITY de Oracle.
        """

        from apps.core.models import UsuarioHistoricoEntity

        entrada = UsuarioHistoricoEntity(
            user_id=user_id,
            change_type=CAMBIO_DE_ROL,
            previous_role_code_id=previous_role_code,
            new_role_code_id=new_role_code,
            previous_status=None,
            new_status=None,
            reason_code_id=None,
            # El MISMO `datetime` en las dos columnas: lo exige `ck_usuario_hist_coherencia`.
            valid_from=instante,
            valid_to=instante,
            changed_by_id=actor_user_id,
            changed_at=instante,
        )
        entrada.save()
        return entrada

    def actualizar_rol_vigente(
        self,
        usuario: UsuarioEntity,
        *,
        new_role_code: str,
        actor_user_id: int,
        instante: datetime,
    ) -> None:
        """
        Pone el rol nuevo en la fila del usuario y sella quien lo cambio y cuando.

        `usuario.role_code` es el rol VIGENTE desnormalizado: es lo que leen la autorizacion y el
        censo sin tener que reconstruir la cadena del historico en cada peticion. El historico sigue
        siendo la fuente de verdad de la TRAZA; esta columna, la del estado actual.

        Las columnas se llaman `role_changed_at` y `role_changed_by`: son los nombres FISICOS del
        esquema T.5 para el `assigned_at` / `assigned_by_user_id` de AC-ROL-01. No existen columnas
        con esos ultimos nombres y no se inventan.

        `update_fields` ACOTA EL `UPDATE` Y EL MIXIN LO RESPETA
        -------------------------------------------------------
        `AtribucionMixin.save()` fija `updated_at` y `updated_by` (rama de modificacion) y delega en
        `Model.save(*args, **kwargs)`, de modo que el `update_fields` recibido viaja intacto a
        Django. Por eso la lista INCLUYE `updated_at` y `updated_by`: si no estuvieran, el mixin los
        asignaria en memoria pero la sentencia no los escribiria y la fila quedaria con la atribucion
        de modificacion caducada. Los nombres son los de los CAMPOS (`role_code`, `role_changed_by`),
        no sus `attname`: Django no acepta `role_code_id` en `update_fields`.

        Args:
            usuario: entidad del usuario, idealmente la devuelta por
                `obtener_usuario_para_actualizar` dentro de la transaccion.
            new_role_code: codigo del rol nuevo, ya comprobado como vigente.
            actor_user_id: usuario de la sesion que realiza el cambio.
            instante: el MISMO instante con el que se escribio la entrada del historico.
        """

        usuario.role_code_id = new_role_code
        usuario.role_changed_at = instante
        usuario.role_changed_by_id = actor_user_id
        usuario.save(update_fields=["role_code", "role_changed_at", "role_changed_by", "updated_at", "updated_by"])

    def sellar_asignacion_inicial_en_usuario(
        self,
        usuario: UsuarioEntity,
        *,
        actor_user_id: int,
        instante: datetime,
    ) -> None:
        """
        Sella en la fila del usuario quien le asigno su rol INICIAL y cuando (AC-ROL-01).

        El alta ya escribio `role_code`, asi que aqui no se vuelve a tocar: lo unico que falta es la
        atribucion de la asignacion, que es el `assigned_at` / `assigned_by_user_id` del criterio de
        aceptacion sobre las columnas FISICAS `role_changed_at` y `role_changed_by` del esquema T.5.
        Dejarlas nulas haria que un usuario recien dado de alta no pudiera decir quien le puso su rol
        hasta su primer cambio.

        Sobre `update_fields` y el mixin vale lo dicho en `actualizar_rol_vigente`.

        Args:
            usuario: entidad del usuario recien creado.
            actor_user_id: usuario de la sesion que realizo el alta.
            instante: el MISMO instante con el que se escribio la entrada de alta del historico.
        """

        usuario.role_changed_at = instante
        usuario.role_changed_by_id = actor_user_id
        usuario.save(update_fields=["role_changed_at", "role_changed_by", "updated_at", "updated_by"])

    def historico_de_rol(
        self,
        user_id: int,
        *,
        desde: date | None,
        hasta: date | None,
        pagina: int,
        tamanio_pagina: int,
    ) -> tuple[list[UsuarioHistoricoEntity], int]:
        """
        Devuelve la pagina pedida del historico de ROL del usuario y el total: `(filas, total)`.

        El `total` es el `COUNT(*)` del conjunto YA FILTRADO y ANTES de trocear, porque lo que el
        consumidor necesita para pintar el paginador es cuantas entradas cumplen el filtro, no
        cuantas caben en la pagina.

        SOLO ENTRADAS DE ROL. El filtro por `change_type = 'ROL'` deja fuera las de ESTADO (baja y
        reactivacion), que viven en la misma tabla y responden a otro caso de uso.

        `hasta` ES INCLUSIVO POR DIA
        -----------------------------
        El filtro acota `changed_at`, que es un instante, con fechas de calendario. Usar
        `changed_at__lte=hasta` compararia contra las 00:00:00 del dia indicado y PERDERIA todas las
        entradas de ese ultimo dia, que es justo el error que el usuario percibe como «faltan las de
        hoy». Por eso el limite superior es `changed_at__lt` del dia SIGUIENTE a medianoche. Las
        marcas son `datetime` naive en UTC (`USE_TZ = False`, convencion del proyecto), asi que los
        extremos se construyen tambien naive.

        EL ORDEN ES DETERMINISTA Y DESCENDENTE
        ---------------------------------------
        AC-ROL-05 exige orden cronologico descendente, y el desempate por `history_id` descendente es
        OBLIGATORIO: dos entradas con el mismo `changed_at` -perfectamente posibles, la resolucion es
        de microsegundos- bailarian entre paginas sin un criterio estable, y la paginacion repetiria
        u omitiria entradas. El indice `ix_usuario_hist_user_changed_at` cubre filtro y orden.

        EL TROCEO VA SOBRE EL QUERYSET
        -------------------------------
        `queryset[inicio:inicio + tamanio]` lo traduce el backend de Oracle a `OFFSET .. FETCH NEXT`.
        Trocear una lista ya materializada leeria el historico completo en cada pagina.

        El `select_related` resuelve en el MISMO `SELECT` el actor y los dos roles de cada entrada:
        sin el, proyectar el listado dispararia una consulta por fila (N+1) para leer el nombre de
        quien hizo el cambio y la denominacion de los roles.

        Args:
            user_id: usuario cuyo historico de rol se consulta.
            desde: primer dia incluido, o `None` para no acotar por abajo.
            hasta: ultimo dia INCLUIDO, o `None` para no acotar por arriba.
            pagina: numero de pagina pedido, base 1.
            tamanio_pagina: numero de entradas por pagina.

        Returns:
            Tupla con las entradas de la pagina (lista vacia si la pagina excede el total, que NO es
            un error) y el total de entradas de la consulta filtrada completa.
        """

        from apps.core.models import UsuarioHistoricoEntity

        queryset = UsuarioHistoricoEntity.objects.select_related("changed_by", "previous_role_code", "new_role_code").filter(
            user_id=user_id,
            change_type=CAMBIO_DE_ROL,
        )

        if desde is not None:
            queryset = queryset.filter(changed_at__gte=datetime.combine(desde, time.min))
        if hasta is not None:
            # `__lt` de la medianoche del dia SIGUIENTE: asi el ultimo dia entra ENTERO.
            queryset = queryset.filter(changed_at__lt=datetime.combine(hasta + timedelta(days=1), time.min))

        total = queryset.count()

        queryset = queryset.order_by("-changed_at", "-history_id")

        # Acotado defensivo: este repositorio no valida entrada (eso es del serializador), pero
        # tampoco le traslada a Oracle un desplazamiento negativo ni un tamanio de pagina nulo.
        tamanio = max(1, tamanio_pagina)
        inicio = (max(1, pagina) - 1) * tamanio

        return list(queryset[inicio : inicio + tamanio]), total

    def refrescar_permisos_de_sesiones(self, user_id: int, *, instante: datetime) -> int:
        """
        Marca EN BLOQUE las sesiones vivas del usuario para que recarguen rol y capacidades.

        EL CAMBIO DE ROL NO REVOCA LA SESION: LA MARCA PARA RECARGA
        ------------------------------------------------------------
        Es una decision de diseno deliberada, no un olvido. La columna
        `sesion_usuario.permissions_refreshed_at` existe en el esquema T.5 aprobado (ARC-112,
        «Última recarga de rol y capacidades tras un cambio de rol») precisamente para esto.

        Lo exige AC-PERM-05 de REQ-005: el usuario afectado NO cierra ni reabre sesion y, en su
        SIGUIENTE peticion, sus permisos ya son los del rol nuevo. Ese criterio se verifica con
        respuestas 200/403 segun lo que el rol nuevo autorice; revocar la sesion devolveria 401 y lo
        haria FALLAR.

        Y satisface REQ-043 RN-05 -«no conserva sesion vigente con los permisos anteriores»- porque
        la guardia de autorizacion del servicio RELEE el rol vigente de la base en cada peticion y
        nunca el rol embebido en la sesion: la sesion sigue viva, pero no con los permisos antiguos.

        UN SOLO `UPDATE`, RESUELTO POR LA BASE
        ---------------------------------------
        No es un recorrido de filas en Python: entre la lectura y la escritura podria emitirse una
        sesion nueva y quedaria sin marcar. El filtro `revoked_at IS NULL` acota a las sesiones
        VIVAS: resellar una ya revocada no cambiaria nada y ensuciaria la evidencia del acceso.

        Las filas NO se borran ni se revocan (REQ-047): la sesion permanece como evidencia.

        Args:
            user_id: usuario cuyas sesiones vivas se marcan para recarga de permisos.
            instante: el MISMO instante con el que se escribio la entrada del historico.

        Returns:
            Numero de sesiones marcadas. Puede ser 0 y no es un error: un usuario sin sesiones
            abiertas en el momento del cambio.
        """

        from apps.core.models import SesionUsuarioEntity

        return SesionUsuarioEntity.objects.filter(user_id=user_id, revoked_at__isnull=True).update(
            permissions_refreshed_at=instante,
        )


__all__ = [
    "CAMBIO_DE_ROL",
    "ESTADO_USUARIO_ACTIVO",
    "ROL_VIGENTE",
    "RepositorioRolesUsuario",
]
