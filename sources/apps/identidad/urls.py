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

from apps.identidad.sesiones.views import SesionActualView


app_name = "identidad"

urlpatterns = [
    path("auth/sessions/current", SesionActualView.as_view(), name="auth-sessions-current"),
]
