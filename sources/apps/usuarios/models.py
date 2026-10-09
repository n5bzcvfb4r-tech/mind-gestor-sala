"""
Punto de entrada de la app `usuarios` a las entidades del esquema T.5 que utiliza.

AQUI NO SE DECLARA NINGUNA TABLA NI NINGUN MODELO. El esquema T.5 completo (21 tablas) lo
gobierna Liquibase (ARC-016) y vive en `apps.core.models`, que es el PUNTO DE ENTRADA UNICO del
proyecto a los modelos del ORM. Declarar aqui una segunda `UsuarioEntity` sobre la misma tabla
`usuario` -aunque fuese "igual"- partiria el modelo en dos clases distintas para la misma fila:
Django las registraria como entidades diferentes, y las claves ajenas que el resto del servicio
(identidad, incidencias, avisos, trazabilidad) ya resuelve contra la de `apps.core.models`
dejarian de apuntar a la misma clase que esta app usa. Dos definiciones del mismo esquema
divergen con el tiempo; una sola, no.

Por eso este modulo es EXCLUSIVAMENTE una re-exportacion. Existe por dos motivos concretos:

* para que el paquete de la app tenga el punto de importacion que Django espera en
  `<app>/models.py`, sin duplicar absolutamente nada;
* para que quien abra `apps.usuarios` vea de inmediato QUE entidades toca esta app -el censo de
  usuarios y el catalogo de roles al que pertenecen- sin tener que rastrear los imports de los
  modulos de alta y de consulta.

TODAS SON `managed = False`: Django no crea, no altera y no borra esas tablas. Si esta app
necesitase una entidad mas del esquema, se anade a esta lista de re-exportacion; nunca se
redeclara.
"""

from apps.core.models import RolEntity, UsuarioEntity

__all__ = [
    "RolEntity",
    "UsuarioEntity",
]
