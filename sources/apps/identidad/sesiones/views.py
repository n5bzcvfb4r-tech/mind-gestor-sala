"""
Vista del recurso de sesion en curso (EP-002 `DELETE` y EP-003 `GET`, `/api/auth/sessions/current`).

RUTA PROTEGIDA. Ninguno de los dos verbos se declara en `rutas_publicas`, de modo que el
guardia de sesion (`SesionRequeridaMiddleware`) los exige por defecto: sin sesion valida no se
llega aqui.

UN SOLO RECURSO, UNA SOLA VISTA. El contrato monta los dos endpoints sobre el MISMO path, y
Django casa por path: dos `path()` con el mismo string harian que la primera entrada ganase
para ambos verbos y el segundo endpoint quedase inalcanzable. Por eso `auth/sessions/current`
lo sirve UNA clase, `SesionActualView`, con `get` (EP-003) y `delete` (EP-002), cada uno con
su propio esquema.

La vista no implementa ciclo de vida de sesion: resuelve la identidad del contexto de sesion y
delega la revocacion en `ServicioCicloVidaSesion`, que es el UNICO dueno de `sesion_usuario`.
"""

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.exceptions import NotAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.contexto import ContextoSesion
from apps.core.models import SesionUsuarioEntity
from apps.identidad.sesiones import ServicioCicloVidaSesion
from apps.identidad.sesiones.serializers import ContextoSesionVigente, SessionContextSerializer


# Indicador booleano de Oracle: 'Y' es verdadero, cualquier otro valor es falso.
INDICADOR_VERDADERO = "Y"


def _contexto_de_la_sesion(request: Request) -> ContextoSesion:
    """
    Devuelve el `ContextoSesion` que dejo el guardia de sesion, o falla con 401.

    La identidad se lee SOLO de la peticion ya autenticada: primero de
    `request.contexto_sesion`, que es donde la publica `SesionRequeridaMiddleware`, y en su
    defecto de `request.auth`, que es donde la deja `AutenticacionSesionOpaca` cuando la vista
    se ejerce sin pasar por el middleware. Jamas del cuerpo, de la query string ni de una
    cabecera: el actor no se autodeclara (REQ-064).

    Sin contexto se lanza `NotAuthenticated`, que el manejador global traduce al 401 uniforme
    (`AUTH_SESSION_INVALID`, `mensajes.SESION_REQUERIDA`); la vista no compone ningun cuerpo de
    error propio.

    Es una funcion de modulo y no un metodo para que los dos verbos del recurso compartan
    EXACTAMENTE la misma resolucion de identidad, sin copias que puedan divergir.
    """

    contexto = getattr(request, "contexto_sesion", None)
    if not isinstance(contexto, ContextoSesion):
        contexto = request.auth if isinstance(request.auth, ContextoSesion) else None

    if contexto is None:
        raise NotAuthenticated()

    return contexto


class SesionActualView(APIView):
    """
    Recurso de la sesion en curso: consulta su contexto (EP-003) y la cierra (EP-002).

    SOLO LA SESION PROPIA (REQ-057 RN-03, REQ-020). El recurso NO admite ningun identificador
    de sesion ni de usuario: ni en la URL, ni en la query string, ni en el cuerpo. El
    `session_id` sale SIEMPRE del contexto de sesion que dejo el guardia, de modo que es
    ESTRUCTURALMENTE imposible consultar o cerrar la sesion de otra persona desde aqui; no hay
    rama que lea un identificador del cliente.

    SOLO DOS VERBOS. `http_method_names` se acota a `get`, `delete` y `options`: cualquier otro
    verbo es 405. El recurso no crea sesiones (eso es EP-001) ni las modifica parcialmente.
    """

    http_method_names = ["get", "delete", "options"]
    # La autenticacion la fija el default del proyecto (`AutenticacionSesionOpaca` en
    # `REST_FRAMEWORK`); el permiso se declara EXPLICITO para que leer la vista baste para
    # saber que exige sesion.
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="EP-003",
        summary="Devuelve el contexto del usuario autenticado con su identidad y rol vigente",
        tags=["ARC-012"],
        responses={200: SessionContextSerializer},
    )
    def get(self, request: Request) -> Response:
        """
        Devuelve con 200 el contexto del usuario de la sesion en curso (EP-003).

        La fila de sesion se RELEE de la base en cada peticion junto con su usuario: la
        respuesta describe el estado actual del servidor, no una copia que el cliente traiga
        consigo. Si la fila no existe se responde el 401 uniforme, nunca un cuerpo a medias.

        NADA DE LA CREDENCIAL (REQ-063). Del usuario solo se proyectan nombre, correo
        corporativo y el indicador de cambio de contrasenia; el hash, la sal y el algoritmo no
        salen por esta frontera.
        """

        contexto = _contexto_de_la_sesion(request)
        sesion = SesionUsuarioEntity.objects.select_related("user", "role_code").filter(pk=contexto.session_id).first()
        if sesion is None:
            raise NotAuthenticated()

        usuario = sesion.user
        vigente = ContextoSesionVigente(
            session_id=sesion.session_id,
            user_id=sesion.user_id,
            full_name=usuario.full_name,
            corporate_email=usuario.corporate_email,
            # El rol se toma del CONTEXTO, que es el rol VIGENTE que el guardia ya releyo de
            # `usuario`, y NO el `role_code` congelado en `sesion_usuario` al emitir la sesion:
            # un cambio de rol surte efecto en la siguiente peticion (REQ-011, REQ-018).
            role_code=contexto.role_code,
            issued_at=sesion.issued_at,
            expires_at=sesion.expires_at,
            last_activity_at=sesion.last_activity_at,
            must_change_password=usuario.must_change_password == INDICADOR_VERDADERO,
        )
        return Response(SessionContextSerializer(vigente).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="EP-002",
        summary="Cierra la sesión del usuario y la revoca en servidor",
        tags=["ARC-012"],
        responses={204: None},
    )
    def delete(self, request: Request) -> Response:
        """
        Cierra la sesion en curso revocandola en SERVIDOR y responde 204 sin cuerpo (EP-002).

        REVOCACION EN SERVIDOR (REQ-057). El cierre no es que el cliente olvide la credencial:
        el servicio sella `revoked_at` y `revocation_reason = "logout"` en `sesion_usuario`
        dentro de una transaccion, asi que la credencial deja de valer para TODAS las
        peticiones posteriores, vengan de donde vengan. La fila no se borra (REQ-047, REQ-070).

        SOLO LA SESION PROPIA (REQ-057 RN-03). El `session_id` sale del contexto de sesion,
        nunca del payload ni de la URL.

        IDEMPOTENCIA (AC-SES-03). La idempotencia es del SERVICIO de revocacion: repetir el
        cierre sobre la misma sesion no lanza, no pisa la marca original y deja el mismo
        estado. Repetir la llamada HTTP con la MISMA credencial responde 401, porque la sesion
        ya esta revocada y el guardia la rechaza ANTES de llegar a esta vista; eso es el
        comportamiento correcto y esperado, no un fallo de idempotencia.
        """

        contexto = _contexto_de_la_sesion(request)
        with transaction.atomic():
            ServicioCicloVidaSesion().cerrar(contexto.session_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
