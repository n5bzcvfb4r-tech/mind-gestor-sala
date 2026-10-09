"""
Consulta del censo de usuarios por el ADMINISTRADOR (EP-008 `GET /users`, REQ-006, REQ-039).

Reune el acceso a datos, el caso de uso de lectura y los serializadores del listado paginado: filtro
por rol y por estado, busqueda libre insensible a mayusculas y acentos, orden por antiguedad y
troceo en paginas. Aqui NO vive el alta del censo (EP-007), que es `apps.usuarios.alta`.

Este paquete NO re-exporta nada a proposito: el repositorio importa modelos del ORM y colgarlo de la
fachada forzaria la carga del registro de aplicaciones al importar `apps.usuarios.consulta`, que es
justo lo que revienta con `AppRegistryNotReady` antes de `django.setup()`. Cada consumidor importa
el submodulo que necesita por su ruta completa.
"""
