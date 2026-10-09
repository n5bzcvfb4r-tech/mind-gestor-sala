"""
Custodia de credenciales de la app `identidad` (REQ-054, REQ-063, REQ-069, REQ-076).

QUE RESUELVE ESTE PAQUETE
-------------------------
Todo lo que rodea al secreto de un usuario cuando se ESTABLECE, no cuando se verifica (eso es
`apps.identidad.autenticacion`): como se guarda (hashing con sal por usuario, REQ-054, de modo
que dos usuarios con la misma contrasenia no compartan hash y una tabla robada no sea
reutilizable), que contrasenias se aceptan (politica UNICA, REQ-069) y cuales se rechazan por
haberse usado ya (historial de no reutilizacion contra `usuario_password_historico`).

UNA POLITICA, TRES PUERTAS
--------------------------
Cambio de contrasenia propio (EP-005), primer acceso (EP-006) y restablecimiento por
administrador (EP-017) establecen contrasenia. REQ-069 exige que la politica sea la misma por
las tres puertas: una contrasenia rechazada en un flujo lo es en todos. Por eso la politica se
evalua en UN solo sitio (`politica.PoliticaContrasenia`) y los tres flujos la consumen, en vez
de repetir las comprobaciones donde cada una se necesita.

LO QUE NUNCA SALE DE AQUI (REQ-063, REQ-076)
--------------------------------------------
La contrasenia en claro y el `password_hash` no se escriben en logs, `repr`, mensajes de error
ni trazas. Los rechazos por politica describen la REGLA incumplida y jamas el valor propuesto,
el anterior ni su hash; los fallos tecnicos de custodia responden el 500 generico de REQ-054 y
dejan el detalle en el log, localizable por el `traceId`.

Este modulo es la FACHADA del paquete: los consumidores importan desde
`apps.identidad.credenciales` y no desde los submodulos, para que la reorganizacion interna no
rompa a nadie. Aqui NO vive logica alguna: solo re-exportacion.

Importar el paquete es seguro ANTES de `django.setup()`: la politica y sus errores son dominio
puro, y el servicio y el repositorio resuelven los modelos de forma PEREZOSA (dentro del metodo
o bajo `TYPE_CHECKING`), de modo que la fachada no fuerza la carga del registro de aplicaciones.
"""

from apps.identidad.credenciales.errores import (
    ContraseniaSinHashearError,
    CustodiaCredencialError,
    PoliticaContraseniaError,
)
from apps.identidad.credenciales.politica import (
    CAMPO_CONTRASENIA,
    LONGITUD_MINIMA,
    MAXIMO_CONTRASENIAS_HISTORICAS,
    MENSAJE_POR_CODIGO,
    CodigoRegla,
    PoliticaContrasenia,
    ReglaIncumplida,
)
from apps.identidad.credenciales.repositorio import RepositorioHistoricoPassword
from apps.identidad.credenciales.servicio import (
    ALGORITMO_POR_DEFECTO,
    ServicioCustodiaCredenciales,
    Verificador,
)


__all__ = [
    "ALGORITMO_POR_DEFECTO",
    "CAMPO_CONTRASENIA",
    "LONGITUD_MINIMA",
    "MAXIMO_CONTRASENIAS_HISTORICAS",
    "MENSAJE_POR_CODIGO",
    "CodigoRegla",
    "ContraseniaSinHashearError",
    "CustodiaCredencialError",
    "PoliticaContrasenia",
    "PoliticaContraseniaError",
    "ReglaIncumplida",
    "RepositorioHistoricoPassword",
    "ServicioCustodiaCredenciales",
    "Verificador",
]
