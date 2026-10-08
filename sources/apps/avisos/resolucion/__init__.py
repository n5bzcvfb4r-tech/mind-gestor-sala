"""
Resolucion de destinatarios de aviso (REQ-089, REQ-140).

Decide a quien se le puede enviar un aviso por correo antes de componer nada: resuelve el
colectivo de mantenimiento y la notificabilidad de un destinatario individual contra el
directorio de usuarios, y deja constancia del intento para poder auditarlo despues.

El paquete se publica por modulos (`resultados`, `errores`, `mensajes`); la fachada de
imports se completa cuando el repositorio y el servicio esten disponibles.
"""
