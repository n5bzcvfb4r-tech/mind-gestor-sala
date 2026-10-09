"""
Fixtures compartidas de las pruebas de `apps.identidad`.

El motor de prueba de esta app es el Oracle Database 23ai Free REAL del proyecto
(`gvenzl/oracle-free:23-slim`), con el esquema aplicado por Liquibase
(`liquibase/liquibase:4.31-alpine`) sobre el changelog `sources/facilities/master.xml`.
Esta PROHIBIDO sustituirlo por H2, SQLite o cualquier doble en memoria: un `dict` en memoria
no acredita persistencia, y un test verde contra un sustituto del motor no demuestra que el
mapeo ORM case con el DDL que gobierna Liquibase (ARC-016) ni que las CHECK y los indices
funcionales del esquema T.5 (`ux_usuario_email_ci`, `ck_usuario_coherencia_baja`) se cumplan
de verdad al autenticar.

AQUI NO SE REIMPLEMENTA NINGUNA FIXTURE. Todo se REUTILIZA de `apps.core_security.tests.conftest`
-que a su vez reexporta la cadena de Oracle de `apps.core.tests.test_persistencia_oracle`-, de
modo que la cadena `red_docker` -> `oracle_contenedor` -> `esquema_aplicado` -> `django_db_setup`
no pueda desincronizarse del DDL ni de las credenciales reales, y la semilla de datos
(`usuario_activo`, `sesion_vigente`) siga siendo LA MISMA que ya ejercita ARC-012.

Si no hay engine Docker alcanzable, las pruebas que tocan Oracle se SALTAN (`SALTAR_SIN_DOCKER`):
nunca se degradan a otro backend ni se desactivan.
"""

from apps.core_security.tests.conftest import (  # noqa: F401
    CLAVE_DE_PRUEBA,
    MOTIVO_SIN_DOCKER,
    SALTAR_SIN_DOCKER,
    cabecera_bearer,
    cliente_api,
    contexto_de_sesion_del_usuario,
    django_db_setup,
    esquema_aplicado,
    oracle_contenedor,
    red_docker,
    sesion_vigente,
    usuario_activo,
)
