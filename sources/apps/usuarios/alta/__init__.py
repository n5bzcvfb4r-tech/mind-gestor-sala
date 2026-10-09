"""
Alta de usuario por el ADMINISTRADOR (EP-007 `POST /users`, REQ-037, REQ-045).

Reune lo que hace falta para incorporar a una persona al censo: normalizacion del nombre y del
correo corporativo, comprobacion de la unicidad global del correo en minusculas y sin espacios
(REQ-045, RN-01) y la escritura de la fila en `usuario` con estado ACTIVO y credencial inicial
pendiente de cambio.

Aqui NO vive la consulta del censo (EP-008) mas alla de las funciones de normalizacion, que se
comparten a proposito para que el texto con el que se busca y el que se persistio sean el mismo.

Este paquete NO re-exporta nada a proposito: el repositorio importa modelos del ORM y colgarlo de
la fachada forzaria la carga del registro de aplicaciones al importar `apps.usuarios.alta`, que es
justo lo que revienta con `AppRegistryNotReady` antes de `django.setup()`. Cada consumidor importa
el submodulo que necesita por su ruta completa.
"""
