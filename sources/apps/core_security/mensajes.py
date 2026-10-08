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

MENSAJE UNIFORME DE 403 (AC-ROL-02)
-----------------------------------
`SIN_PERMISOS` es el texto literal que exige REQ-018 / AC-ROL-02 para toda denegacion por
rol o permiso. Rol ausente, rol fuera del catalogo, usuario inactivo y par rol x operacion
sin fila en la matriz responden exactamente lo mismo, de modo que el cuerpo no revela cual
de los motivos se ha dado (fail-closed indistinguible). Es un literal DISTINTO de
`PERMISO_DENEGADO`, que sigue sirviendo a la envolvente de enrutado de DRF.
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

# --- Autorizacion (REQ-018, REQ-011) -------------------------------------
# Mensaje UNIFORME de denegacion por rol/permiso: el mismo texto para rol ausente, rol
# desconocido, usuario inactivo y par rol x operacion sin fila en la matriz. No revela
# cual de los tres motivos se ha dado (fail-closed indistinguible).
SIN_PERMISOS = "Tu usuario no tiene permisos para realizar esta acción"
PERMISOS_CAMBIADOS = "Sus permisos han cambiado; la acción solicitada ya no está autorizada"
