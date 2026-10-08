from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

urlpatterns = [
    # Seguridad de sesion (ARC-012): aporta EP-001, la UNICA ruta publica del backend.
    path("api/", include("apps.core_security.urls")),
    path("api/", include("apps.identidad.urls")),
    path("api/", include("apps.usuarios.urls")),
    path("api/", include("apps.catalogos.urls")),
    path("api/", include("apps.incidencias.urls")),
    path("api/", include("apps.ciclo_vida.urls")),
    path("api/", include("apps.trazabilidad.urls")),
    path("api/", include("apps.avisos.urls")),
    path("docs/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger"),
]

if settings.LOCAL_ENVIRONMENT:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
