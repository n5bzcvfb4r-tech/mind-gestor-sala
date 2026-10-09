"""
Punto de entrada de la app `identidad` a las entidades del esquema T.5 que utiliza.

AQUI NO SE DECLARA NINGUNA TABLA NI NINGUN MODELO. El esquema T.5 completo (21 tablas) vive
en `apps.core.models`, todas las entidades son `managed = False` y su DDL lo gobierna
Liquibase (ARC-016): Django no crea, no altera y no borra esas tablas. Declarar aqui una
segunda `UsuarioEntity`, `SesionUsuarioEntity` o `RolEntity` -aunque fuese "igual"- romperia
el registro de modelos de Django con etiquetas duplicadas y partiria la fuente unica del
esquema en dos definiciones que divergirian con el tiempo.

Por eso este modulo es EXCLUSIVAMENTE una re-exportacion: la app `identidad` importa desde
`apps.identidad.models` las tres entidades que necesita para autenticar (el censo de
usuarios, la sesion opaca y el catalogo de roles), y esas entidades son LAS MISMAS clases
que publica `apps.core.models`, no copias.

Si esta app necesitase una entidad mas del esquema, se anade a esta lista de re-exportacion;
nunca se redeclara.
"""

from apps.core.models import RolEntity, SesionUsuarioEntity, UsuarioEntity

__all__ = [
    "RolEntity",
    "SesionUsuarioEntity",
    "UsuarioEntity",
]
