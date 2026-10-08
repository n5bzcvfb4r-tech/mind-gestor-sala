"""
Comando de gestion que verifica las semillas de los catalogos maestros (CI y despliegue).
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.arranque import verificar_catalogos_al_arranque
from apps.core.catalogos import CATALOGOS_OBLIGATORIOS
from apps.core.errores import CatalogosVaciosError


class Command(BaseCommand):
    help = "Verifica que los catalogos maestros tienen las semillas aplicadas (ARC-016) y falla si alguno esta vacio."

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            verificar_catalogos_al_arranque(forzar=True)
        except CatalogosVaciosError as error:
            raise CommandError(f"{error.mensaje} Catalogos sin datos: {', '.join(error.tablas_vacias)}.") from error

        self.stdout.write(self.style.SUCCESS(f"Catalogos maestros verificados: {len(CATALOGOS_OBLIGATORIOS)} tablas con datos"))
