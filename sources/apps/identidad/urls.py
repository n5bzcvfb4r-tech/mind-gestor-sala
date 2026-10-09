"""
Rutas de la app de identidad (contrato `openapi.yaml`).

El proyecto monta esta app bajo `api/` en `config/urls.py`, que es el `servers[0].url` del
contrato, asi que los paths se declaran SIN barra inicial ni final.

`auth/sessions/current` es UNA sola ruta servida por UNA sola vista: el contrato publica ahi
dos operaciones (EP-003 `GET` y EP-002 `DELETE`) y Django casa por path, de modo que dos
`path()` sobre el mismo string dejarian el segundo inalcanzable. Los dos verbos los atiende
`SesionActualView`.
"""

from django.urls import path

from apps.identidad.bloqueo.views import DesbloqueoCuentaView
from apps.identidad.reposicion.views import EstadoCredencialView, RestablecimientoPasswordView
from apps.identidad.sesiones.views import SesionActualView


app_name = "identidad"

urlpatterns = [
    path("auth/sessions/current", SesionActualView.as_view(), name="auth-sessions-current"),
    # EP-018 cuelga del arbol `/users/...` del contrato, pero lo sirve el modulo de bloqueo de
    # `identidad` (ARC-013), que es su dueno semantico: el estado de bloqueo de una cuenta -la
    # politica, el contador de intentos y el atajo administrativo que lo levanta- vive entero
    # ahi, y partir el endpoint entre dos apps obligaria a `usuarios` a importar ese dominio
    # solo para declarar una ruta. Por eso `apps.usuarios.urls` NO declara este path: un path
    # declarado en dos URLconf dejaria el segundo inalcanzable y repartiria la responsabilidad.
    #
    # EP-017 y EP-019 siguen EXACTAMENTE EL MISMO CRITERIO: tambien cuelgan de `/users/...` en el
    # contrato y tambien los sirve `identidad`, porque su dueno semantico es el modulo de
    # reposicion de credenciales de ARC-013 -generacion de la credencial temporal, entrega,
    # revocacion de sesiones y consulta del estado resultante-. `apps.usuarios.urls` NO declara
    # ninguno de los dos, por la misma razon que no declara EP-018.
    #
    # ORDEN DELIBERADO: `users/credential-status` va ANTES que cualquier `users/<int:user_id>/...`.
    # Hoy el convertidor `<int:>` no casaria nunca con el literal `credential-status`, asi que el
    # orden no cambia el resultado; se declara primero igualmente para que la intencion quede
    # explicita y para que la ruta siga siendo alcanzable si alguien relajase el convertidor a
    # `<str:>`, caso en el que la ruta generica engulliria el literal y lo dejaria inalcanzable.
    path("users/credential-status", EstadoCredencialView.as_view(), name="users-credential-status"),
    path("users/<int:user_id>/password-reset", RestablecimientoPasswordView.as_view(), name="users-password-reset"),
    path("users/<int:user_id>/unlock", DesbloqueoCuentaView.as_view(), name="users-unlock"),
]
