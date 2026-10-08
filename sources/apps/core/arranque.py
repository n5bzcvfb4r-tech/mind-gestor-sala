"""
Gate de arranque del proceso servidor: sin catalogos maestros poblados no se levanta.

Lo invoca `config/wsgi.py` justo despues de `get_wsgi_application()`. Si faltan semillas,
`verificar_catalogos_al_arranque()` lanza `CatalogosVaciosError` y el proceso muere en el
arranque en lugar de servir trafico con catalogos vacios (comportamiento exigido por el DoD).
"""

import logging

from django.conf import settings

from apps.core.catalogos import CATALOGOS_OBLIGATORIOS, catalogos_vacios
from apps.core.errores import CatalogosVaciosError, VerificacionCatalogosError


logger = logging.getLogger(__name__)


def verificar_catalogos_al_arranque(*, forzar: bool = False) -> None:
    """
    Verifica que los catalogos maestros tienen datos y aborta el arranque si no es asi.

    Args:
        forzar: ejecuta la verificacion aunque `settings.VERIFICAR_CATALOGOS_AL_ARRANQUE` este
            desactivada (lo usa el comando de gestion `verificar_catalogos`).

    Raises:
        CatalogosVaciosError: si alguno de los catalogos obligatorios no tiene ninguna fila.
        VerificacionCatalogosError: si la base de datos no responde y no se puede verificar.
    """

    if not settings.VERIFICAR_CATALOGOS_AL_ARRANQUE and not forzar:
        logger.info(
            "Verificacion de catalogos maestros desactivada por configuracion (VERIFICAR_CATALOGOS_AL_ARRANQUE=False).",
            extra={"data": {"forzar": forzar}},
        )
        return

    try:
        vacios = catalogos_vacios()
    except Exception as error:  # BBDD inalcanzable, esquema ausente o configuracion incompleta
        logger.error(
            "Arranque abortado: no se ha podido consultar el estado de los catalogos maestros.",
            extra={"data": {"detalle": str(error)}},
        )
        raise VerificacionCatalogosError(str(error)) from error

    if vacios:
        logger.error(
            "Arranque abortado: hay catalogos maestros sin semillas.",
            extra={"data": {"tablas_vacias": vacios}},
        )
        raise CatalogosVaciosError(vacios)

    logger.info(
        "Catalogos maestros verificados: todas las tablas obligatorias tienen datos.",
        extra={"data": {"tablas_verificadas": len(CATALOGOS_OBLIGATORIOS)}},
    )
