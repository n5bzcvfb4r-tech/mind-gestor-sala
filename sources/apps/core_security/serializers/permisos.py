"""
Serializadores de los permisos efectivos (EP-004).

CONVENIO DE NOMBRES. El JSON del contrato va en camelCase y el modelo Python en snake_case:
los campos de salida se declaran con el nombre camelCase del contrato y apuntan al atributo
real del dataclass con `source=`. Asi el contrato manda en la frontera HTTP sin contaminar
los nombres del dominio, igual que en `SessionDetailSerializer`.

EL ESQUEMA `EffectivePermissions` LO FIJA ESTE SERIALIZADOR. En el `openapi.yaml` el esquema
esta todavia como germen (`x-mind-placeholder`), sin propiedades: el modelo real de respuesta
es el que REQ-020 exige y el que se declara aqui, de modo que la documentacion generada por
drf-spectacular y la respuesta real no puedan divergir.

LO QUE NO SE PUBLICA (AC-ROL-04). La respuesta se limita a los codigos de `cat_operacion` que
la matriz `permiso_rol_operacion` autoriza al rol VIGENTE del usuario de la sesion y al
alcance de datos (`OWN` | `ALL`) de ese mismo rol, leidos de la base de datos. NO se publica
el identificador del usuario, NI la matriz completa del sistema, NI permiso alguno de
terceros: quien consulta solo puede ver lo suyo, y solo en forma de capacidades.
"""

from rest_framework import serializers


class EffectivePermissionsSerializer(serializers.Serializer):
    """
    Permisos efectivos que devuelve EP-004 (`EffectivePermissions`).

    Proyecta el dataclass `PermisosEfectivos` de `apps.core_security.servicios.permisos`. Los
    tres campos son de SOLO lectura y los tres viajan SIEMPRE en la respuesta: un cliente que
    reciba este cuerpo no tiene que distinguir entre "sin permisos" y "campo ausente", porque
    un rol sin ninguna fila en la matriz devuelve `allowedOperations` vacio, no omitido.

    `roleCode` es el rol VIGENTE releido de la base por el guardia de sesion, no el congelado
    al emitir la sesion. `allowedOperations` son codigos del catalogo `cat_operacion`, en
    orden alfabetico estable para que la respuesta sea comparable entre peticiones.
    `dataScope` es el alcance (`OWN` | `ALL`) con el que ese rol ve los datos.
    """

    roleCode = serializers.CharField(source="role_code", read_only=True)
    allowedOperations = serializers.ListField(child=serializers.CharField(), source="allowed_operations", read_only=True)
    dataScope = serializers.CharField(source="data_scope", read_only=True)
