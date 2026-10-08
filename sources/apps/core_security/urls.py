"""
Rutas de la seguridad de sesion.

El `config/urls.py` incluye este modulo bajo el prefijo `api/`, de modo que la URL publica
resultante es EXACTAMENTE `/api/auth/sessions` (base `servers[0].url` del `openapi.yaml` mas
el path del contrato). Por eso el path se declara SIN barra inicial ni barra final.
"""

from django.urls import path

from apps.core_security.views.sesiones import IniciarSesionView

app_name = "core_security"

urlpatterns = [
    # EP-001: unica ruta publica del backend (REQ-058).
    path("auth/sessions", IniciarSesionView.as_view(), name="auth-sessions"),
]
