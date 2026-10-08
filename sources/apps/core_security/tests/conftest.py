"""
Fixtures compartidas de las pruebas de `apps.core_security`.

El motor de prueba de esta app es el Oracle Database 23ai Free REAL del proyecto
(`gvenzl/oracle-free:23-slim`), con el esquema aplicado por Liquibase
(`liquibase/liquibase:4.31-alpine`) sobre el changelog `sources/facilities/master.xml`.
Esta PROHIBIDO sustituirlo por H2, SQLite o cualquier doble en memoria: un `dict` en
memoria no es evidencia de persistencia, y un test verde contra un sustituto del motor
no acredita que el mapeo ORM case con el DDL que gobierna Liquibase (ARC-016).

La cadena de fixtures (`red_docker` -> `oracle_contenedor` -> `esquema_aplicado` ->
`django_db_setup`) NO se duplica aqui: se REUTILIZA del modulo hermano
`apps.core.tests.test_persistencia_oracle`, de modo que no pueda desincronizarse del DDL
ni de las credenciales reales. Se reexportan tambien `SALTAR_SIN_DOCKER` y
`MOTIVO_SIN_DOCKER` para que las pruebas de esta app los importen de un solo sitio: si no
hay engine Docker alcanzable, las pruebas que tocan Oracle se SALTAN, nunca se degradan a
otro backend.
"""

from apps.core.tests.conftest import MOTIVO_SIN_DOCKER  # noqa: F401
from apps.core.tests.test_persistencia_oracle import (  # noqa: F401
    SALTAR_SIN_DOCKER,
    django_db_setup,
    esquema_aplicado,
    oracle_contenedor,
    red_docker,
)
