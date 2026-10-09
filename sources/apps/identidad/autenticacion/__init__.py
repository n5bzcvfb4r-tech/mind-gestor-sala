"""
Autenticacion de credenciales de la app `identidad` (ARC-013).

Punto de entrada unico del paquete: todo consumidor importa `ServicioAutenticacion` desde
`apps.identidad.autenticacion`, nunca desde el submodulo, para que la implementacion pueda
reorganizarse sin tocar a quien la usa.
"""

from apps.identidad.autenticacion.servicio import ServicioAutenticacion

__all__ = ["ServicioAutenticacion"]
