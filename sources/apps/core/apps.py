from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"
    verbose_name = "Nucleo transversal"

    def ready(self) -> None:
        from apps.core import checks  # noqa: F401
