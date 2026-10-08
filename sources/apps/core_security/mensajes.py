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
