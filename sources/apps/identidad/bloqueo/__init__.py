"""
Bloqueo temporal de cuenta por intentos fallidos (REQ-055, REQ-072, REQ-075).

QUE RESUELVE ESTE PAQUETE
-------------------------
La reaccion del sistema a una racha de verificaciones de contrasenia fallidas: contar los
fallos consecutivos de una cuenta, bloquearla temporalmente al alcanzar el umbral (REQ-055),
responder el 423 con la hora de desbloqueo cuando el bloqueo esta vigente (REQ-072) y
levantarlo por decision administrativa (REQ-075). No verifica contrasenias -eso es
`apps.identidad.autenticacion`- ni las establece -eso es `apps.identidad.credenciales`-:
aqui solo vive QUE le pasa a la cuenta segun el resultado de esos intentos.

EL BLOQUEO SE DESHACE SOLO
--------------------------
El vencimiento no depende de ninguna tarea programada: el estado se normaliza en el siguiente
intento, dejando el contador a cero cuando `locked_until` ya paso (REQ-055 RN-04, AC-PWD-10).
Lo que si requiere actor humano es el atajo administrativo de REQ-075, que levanta el bloqueo
antes de tiempo sin borrar la traza del ultimo fallo (AC-RST-06).

UMBRAL Y DURACION SON CONFIGURACION
-----------------------------------
El RFP no fija cuantos intentos ni cuantos minutos (gap declarado en REQ-055/REQ-072): el
valor inferido -5 intentos, 15 minutos- vive en `settings` y se ajusta por entorno, no se
escribe en el codigo.

Este modulo es la FACHADA del paquete: los consumidores importan desde
`apps.identidad.bloqueo` y no desde los submodulos, para que la reorganizacion interna no
rompa a nadie. Aqui NO vive logica alguna: solo re-exportacion.

Importar el paquete es seguro ANTES de `django.setup()`: la politica es dominio puro y lee
`settings` de forma PEREZOSA dentro del constructor, nunca al importar.
"""

from apps.identidad.bloqueo.errores import (
    CuentaBloqueadaTemporalmenteError,
    CuentaInactivaError,
    CuentaNoBloqueadaError,
    UsuarioNoEncontradoError,
)
from apps.identidad.bloqueo.politica import EstadoBloqueo, PoliticaBloqueo


__all__ = [
    "CuentaBloqueadaTemporalmenteError",
    "CuentaInactivaError",
    "CuentaNoBloqueadaError",
    "EstadoBloqueo",
    "PoliticaBloqueo",
    "UsuarioNoEncontradoError",
]
