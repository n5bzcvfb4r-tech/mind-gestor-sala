"""
Fachada HTTP del censo de usuarios: alta de usuario (EP-007) y listado del censo (EP-008).

Las dos operaciones del contrato cuelgan del MISMO path (`/api/users`) y las sirve UNA sola
vista, `CensoUsuariosView`; el porque esta en su docstring.

RUTA PROTEGIDA Y RESERVADA AL ADMINISTRADOR. `users` no se declara en
`apps.core_security.rutas_publicas`, de modo que el guardia de sesion
(`SesionRequeridaMiddleware`) exige sesion valida por defecto, y encima la vista lleva la guardia
declarativa `requiere_administrador("USER_MANAGE")`, que ademas de exigir el rol consulta la
matriz `permiso_rol_operacion`. La autorizacion se declara AQUI, junto a la vista, y NUNCA dentro
del cuerpo del endpoint: un `if actor.role_code != ...` es el anti-patron que prohibe la guia del
proyecto, porque convierte el codigo en una segunda fuente de verdad que se desalinea de la matriz.

ERRORES. Esta vista no compone cuerpo de error propio: deja PROPAGAR los `ErrorDominio` del
dominio y las `ValidationError` de DRF, y el manejador unico (`apps.core_security.manejadores`)
los traduce al cuerpo uniforme del servicio (`code`, `message`, `details`, `traceId`).
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.exceptions import NotAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.contexto import ContextoSesion, obtener_contexto
from apps.core_security.permisos import requiere_administrador

# `OPERACION_GESTION_USUARIOS` se IMPORTA del modulo de bloqueo en vez de redeclararse: es el
# MISMO `operation_code` del catalogo `cat_operacion` (`USER_MANAGE`), ya sembrado por Liquibase y
# ya asociado unicamente al rol ADMINISTRADOR en `permiso_rol_operacion`. Dar de alta un usuario y
# consultar el censo son dos operaciones de la misma potestad -la gestion del censo de usuarios-,
# exactamente igual que el restablecimiento de credencial y el desbloqueo de cuenta que ya la
# reutilizan desde `apps/identidad/reposicion/views.py`. Una segunda constante con el mismo
# literal solo crearia dos sitios donde corregir el dia que el catalogo cambie, con el riesgo de
# corregir uno y olvidar el otro.
from apps.identidad.bloqueo.views import OPERACION_GESTION_USUARIOS
from apps.usuarios.alta.serializers import UserCreateRequestSerializer, UserDetailSerializer
from apps.usuarios.alta.servicio import ServicioAltaUsuario
from apps.usuarios.consulta.serializers import UserListPageSerializer, UserListQuerySerializer
from apps.usuarios.consulta.servicio import ServicioCensoUsuarios


__all__ = ["CensoUsuariosView"]


class CensoUsuariosView(APIView):
    """
    Sirve las DOS operaciones que el contrato publica sobre `/api/users`: EP-007 (`POST`, alta de
    usuario) y EP-008 (`GET`, listado del censo).

    UNA SOLA VISTA PARA LOS DOS VERBOS, Y ES OBLIGATORIO QUE LO SEA. Django casa las peticiones
    POR PATH, no por verbo: dos `path("users", ...)` sobre el mismo string dejarian el segundo
    inalcanzable, porque el resolutor se queda con el primero que encaja y no vuelve atras aunque
    la vista elegida no atienda ese metodo -devolveria 405 en lugar de llegar a la otra-. Es
    exactamente el criterio que ya documenta `apps/identidad/urls.py` para `auth/sessions/current`,
    donde EP-003 (`GET`) y EP-002 (`DELETE`) los atiende una unica `SesionActualView`.

    RUTA PROTEGIDA Y RESERVADA AL ADMINISTRADOR. `users` NO esta en
    `apps.core_security.rutas_publicas`, asi que el `SesionRequeridaMiddleware` responde 401 a
    cualquier peticion sin sesion valida. De ahi se sigue algo que conviene dejar escrito: NO
    EXISTE NINGUNA VIA DE AUTORREGISTRO NI DE CREACION DE CUENTA PROPIA en el servicio (REQ-036,
    AC-ROL-05, AC-XFN-02); el unico camino por el que nace un usuario es este `POST`, y para
    llegar a el hay que estar ya dentro con una sesion de administrador. Encima del middleware va
    la guardia declarativa `requiere_administrador("USER_MANAGE")`, que exige el rol Y consulta la
    matriz `permiso_rol_operacion`: un EMPLEADO o un TECNICO_MANTENIMIENTO reciben 403 ANTES de
    que el cuerpo de la vista se ejecute y, por tanto, antes de tocar la base de datos (AC-ROL-06,
    AC-USR-02). No hay consulta que filtrar despues ni respuesta parcial que recortar.

    EL ROL NO SE COMPRUEBA EN EL CUERPO DEL ENDPOINT. Ni `post` ni `get` miran `actor.role_code`
    en ningun punto: ese `if` es el anti-patron que prohibe la guia del proyecto, porque duplica
    en codigo una decision que ya vive en la matriz y acaba divergiendo de ella.

    NINGUNA RESPUESTA DE ESTA VISTA LLEVA CREDENCIALES (REQ-063, REQ-076, REQ-079, AC-USR-01).
    Ni `UserDetailSerializer` ni `UserSummarySerializer` declaran campo alguno de credencial, y
    los dataclasses que proyectan (`UsuarioCreado`, `UsuarioDelCenso`) tampoco los transportan ni
    pueden hacerlo, porque `slots=True` cierra esas clases a atributos nuevos. La garantia es
    ESTRUCTURAL en las tres capas -repositorio, dominio y serializador- y no una omision que haya
    que recordar en cada revision: publicar material de credencial exigiria anadirlo primero al
    dominio, lo que quedaria a la vista en el diff. La credencial inicial existe en un unico
    sitio: el correo que recibe el usuario dado de alta en su buzon corporativo.

    ERRORES: ESTA VISTA NO COMPONE CUERPOS DE ERROR. Deja PROPAGAR los `ErrorDominio` del dominio
    y las `ValidationError` de DRF, y el manejador unico (`apps.core_security.manejadores`) los
    traduce al cuerpo uniforme `{code, message, details, traceId}`:

    * `ValidationError` de los serializadores -> 400, con el CAMPO concreto que esta mal.
    * `CorreoDuplicadoError` -> 409: ese correo ya identifica a otra persona (REQ-037 regla 3).
    * `EntregaCredencialInicialFallidaError` -> 502: el correo con la credencial inicial no salio.
      EL USUARIO QUEDA CREADO IGUALMENTE (REQ-038 regla 6); la via de reparacion es reemitir la
      credencial (EP-017), nunca repetir el alta.
    * `PermisoDenegadoError` -> 403 y la ausencia de sesion -> 401: los produce la guardia ANTES
      de entrar aqui.

    `http_method_names` se acota a los dos verbos del contrato mas `options`: cualquier otro es
    405. Sobre el censo no hay ni borrado ni modificacion masiva publicados en `openapi.yaml`.
    """

    http_method_names = ["get", "post", "options"]
    # La autenticacion la fija el default del proyecto (`AutenticacionSesionOpaca` en
    # `REST_FRAMEWORK`); los permisos se declaran EXPLICITOS para que leer la vista baste para
    # saber que exige sesion valida Y rol ADMINISTRADOR con `USER_MANAGE` en la matriz.
    permission_classes = [permissions.IsAuthenticated, requiere_administrador(OPERACION_GESTION_USUARIOS)]

    @extend_schema(
        operation_id="EP-007",
        summary="Da de alta un usuario con nombre, correo corporativo y rol, y emite su credencial inicial",
        tags=["ARC-012"],
        request=UserCreateRequestSerializer,
        responses={201: UserDetailSerializer},
    )
    def post(self, request: Request) -> Response:
        """
        Da de alta al usuario descrito en el cuerpo y devuelve con 201 su ficha (EP-007, REQ-037).

        EL `created_by` SALE DEL CONTEXTO DE SESION Y JAMAS DEL PAYLOAD (REQ-048, REQ-064). Del
        cuerpo se leen exactamente los tres datos de REQ-037 -nombre, correo corporativo y rol-,
        que son los unicos que `UserCreateRequestSerializer` declara; el administrador que ejecuta
        el alta llega por otro camino, el `ContextoSesion` que resuelve `_actor_de`. Aceptar un
        `createdBy` del payload seria dejar que el cliente firme la auditoria con el nombre de otro.

        AQUI NO SE ABRE TRANSACCION. La frontera transaccional es del servicio, y su orden es la
        propia regla de negocio: `ServicioAltaUsuario` CONFIRMA el alta -usuario mas solicitud de
        aviso, en una unica transaccion- y solo DESPUES, ya fuera de toda transaccion, abre la
        conexion SMTP. Envolver el caso de uso desde la vista mantendria la fila del usuario recien
        creado bloqueada durante todo el timeout del servidor de correo, que es el anti-patron que
        el handbook prohibe, y ademas romperia REQ-038 regla 6: una entrega fallida tiene que
        devolver 502 CON EL USUARIO YA CREADO, no deshacer el alta.

        La validacion ocurre en el serializador y no aqui porque es la unica capa que puede
        atribuir el fallo a un CAMPO concreto y devolver su literal en el 400.

        Args:
            request: peticion ya autenticada; su cuerpo es el `UserCreateRequest` del contrato.

        Returns:
            Response: 201 con el `UserDetail` del usuario creado, SIN ningun dato de credencial.
            El 201 es el de la columna Status del contrato para EP-007; la vista no elige codigo
            propio.
        """

        entrada = UserCreateRequestSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        actor = self._actor_de(request)

        resultado = ServicioAltaUsuario().crear(entrada.a_datos(), actor=actor)
        return Response(UserDetailSerializer(resultado).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        operation_id="EP-008",
        summary="Lista el censo de usuarios con su rol y estado, con búsqueda, filtros y paginación",
        tags=["ARC-012"],
        parameters=[UserListQuerySerializer],
        responses={200: UserListPageSerializer},
    )
    def get(self, request: Request) -> Response:
        """
        Valida la query string y devuelve con 200 la pagina del censo pedida (EP-008, REQ-039).

        LA VALIDACION DE LOS FILTROS OCURRE AQUI y no en el repositorio: esta es la unica capa que
        puede responder un 400 de CAMPO -el literal «Filtro no valido»- diciendo QUE parametro
        esta mal, por ejemplo un `roleCode` que no pertenece al catalogo o un `pageSize` fuera de
        rango. Aguas abajo, `CriteriosCenso` viaja ya validado e inmutable y ninguna capa lo retoca.

        SIN FILTROS, LO QUE SE DEVUELVE NO ES «TODO» (AC-USR-05): la respuesta trae solo los
        usuarios en estado `ACTIVO`, ordenados por `created_at` descendente, de 25 en 25 y con un
        maximo de 100 por pagina. Un `status` explicito SUSTITUYE al ACTIVO por defecto, no se
        suma a el.

        CERO COINCIDENCIAS ES UN 200 CON `items` VACIO Y `emptyMessage`, NUNCA UN ERROR. La
        peticion es correcta y simplemente no hay filas que mostrar -tampoco cuando la pagina
        pedida esta por encima del total-; un 404 ahi obligaria al cliente a distinguir «no existe
        el recurso» de «no hay resultados», que son cosas distintas.

        Args:
            request: peticion ya autenticada; su query string es la de EP-008 en el contrato.

        Returns:
            Response: 200 con el `UserListPage` correspondiente a los criterios recibidos.
        """

        consulta = UserListQuerySerializer(data=request.query_params)
        consulta.is_valid(raise_exception=True)

        pagina = ServicioCensoUsuarios().listar(consulta.a_criterios())
        return Response(UserListPageSerializer(pagina).data)

    @staticmethod
    def _actor_de(request: Request) -> ContextoSesion:
        """
        Devuelve el `ContextoSesion` del administrador que ejecuta la operacion, o falla con 401.

        La identidad se lee SOLO de la peticion ya autenticada: primero de `request.auth`, que es
        donde la deja la clase de autenticacion de DRF, y en su defecto del contexto publicado en
        la peticion en curso (`apps.core.contexto.obtener_contexto`), que es donde la republica la
        guardia de autorizacion con el alcance de datos de la operacion. Jamas del cuerpo, de la
        query string ni de una cabecera: de ahi sale el `created_by` del alta (REQ-064), y si el
        cliente pudiera influir en el, la auditoria dejaria de valer.

        Sin contexto se lanza `NotAuthenticated`, que el manejador global traduce al 401 uniforme;
        en la practica no deberia ocurrir, porque `IsAuthenticated` y la guardia de administrador
        ya han denegado antes, pero el caso de uso no asume su propia proteccion.
        """

        actor = request.auth if isinstance(request.auth, ContextoSesion) else obtener_contexto()
        if actor is None:
            raise NotAuthenticated()

        return actor
