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
    path("users/<int:user_id>/unlock", DesbloqueoCuentaView.as_view(), name="users-unlock"),
]
