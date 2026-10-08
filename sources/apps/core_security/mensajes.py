"""
Literales de usuario de la seguridad de sesion.

Todos los mensajes estan en espanol (REQ-050). Los identificadores van sin tildes por
convencion del proyecto; el texto visible si lleva la ortografia correcta.

MENSAJE UNIFORME DE 401 (AC-SES-04)
-----------------------------------
`SESION_REQUERIDA` es el UNICO texto que el servicio devuelve ante cualquier fallo de
sesion: credencial ausente, credencial manipulada, sesion revocada y sesion caducada
responden exactamente lo mismo. El motivo concreto queda en la traza interna y NUNCA en la
respuesta, para no confirmar al cliente que una sesion existio ni que el recurso existe.

`CREDENCIALES_INVALIDAS` cumple la misma regla en el inicio de sesion: usuario inexistente,
usuario inactivo y contrasenia incorrecta comparten respuesta, de modo que la API no sirve
de oraculo para enumerar cuentas.
"""

# --- Sesion --------------------------------------------------------------
SESION_REQUERIDA = "Debes iniciar sesión para continuar"

# --- Inicio de sesion ----------------------------------------------------
CREDENCIALES_INVALIDAS = "Usuario o contraseña incorrectos"
CUENTA_BLOQUEADA = "Tu cuenta está bloqueada temporalmente, inténtalo más tarde"

# --- Envolvente de error de la superficie de enrutado --------------------
# Textos que devuelve el manejador unico de excepciones (`manejadores.py`). El detalle
# tecnico del fallo NUNCA viaja en el mensaje: se queda en la traza, junto al `traceId`.
DATOS_INVALIDOS = "Revisa los datos introducidos"
PERMISO_DENEGADO = "No tienes permisos para realizar esta acción"
METODO_NO_PERMITIDO = "El método HTTP no está permitido para este recurso"
FORMATO_NO_ACEPTABLE = "No se puede atender la petición en el formato solicitado"
MEDIO_NO_SOPORTADO = "El formato del contenido enviado no está soportado"
DEMASIADAS_PETICIONES = "Has realizado demasiadas peticiones, inténtalo más tarde"
ERROR_INESPERADO = "No se ha podido completar la operación, inténtalo de nuevo más tarde"
