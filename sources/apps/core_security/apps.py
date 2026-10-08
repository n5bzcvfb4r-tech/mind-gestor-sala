from django.apps import AppConfig


class CoreSecurityConfig(AppConfig):
    name = "apps.core_security"
    verbose_name = "Seguridad de sesión"

    def ready(self) -> None:
        # La extension de drf-spectacular se registra por el mero hecho de importarse (lo hace
        # su metaclase). Sin esta importacion nadie carga el modulo, la extension no existe para
        # el generador y el contrato OpenAPI sale sin esquema de seguridad.
        from apps.core_security import esquema  # noqa: F401
