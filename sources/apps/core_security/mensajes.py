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

MENSAJE UNIFORME DE 403 (REQ-009, REQ-078, AC-ROL-02)
-----------------------------------------------------
"No tiene permisos para realizar esta accion" es el texto que REQ-009 fija LITERALMENTE para
toda denegacion por rol o permiso. No es una preferencia de estilo ni un texto negociable de
interfaz: es LEY del requisito, y cualquier variacion de persona, tiempo verbal o matiz
("tu usuario...", "no tienes...") incumple el DoD aunque se lea igual de bien.

Por eso `SIN_PERMISOS` y `PERMISO_DENEGADO` comparten EXACTAMENTE el mismo valor. REQ-078
exige DENEGACION UNIFORME: rol ausente, rol fuera de `cat_rol`, usuario inactivo, par
rol x operacion sin fila en la matriz y denegacion de la envolvente de enrutado de DRF
responden TODOS 403 con el MISMO texto. Que sigan siendo dos constantes es una cuestion de
procedencia -una la usan los errores de dominio, la otra el manejador unico de excepciones-,
nunca una excusa para que diverjan los literales.

El cuerpo de la respuesta no debe revelar CUAL de los motivos se ha dado ni si el recurso
existe: con un unico texto, el cliente no puede distinguir "no tienes permiso" de "no esta",
y la API deja de ser un oraculo de enumeracion (fail-closed indistinguible). El motivo
concreto se queda en la traza interna, junto al `traceId`.
"""

# --- Sesion --------------------------------------------------------------
SESION_REQUERIDA = "Debes iniciar sesión para continuar"

# --- Inicio de sesion ----------------------------------------------------
CREDENCIALES_INVALIDAS = "Usuario o contraseña incorrectos"
CUENTA_BLOQUEADA = "Tu cuenta está bloqueada temporalmente, inténtalo más tarde"

# --- Envolvente de error de la superficie de enrutado --------------------
# Textos que devuelve el manejador unico de excepciones (`manejadores.py`). El detalle
# tecnico del fallo NUNCA viaja en el mensaje: se queda en la traza, junto al `traceId`.
# `PERMISO_DENEGADO` repite palabra por palabra el literal de REQ-009 que tambien usa
# `SIN_PERMISOS`: un 403 nacido en la envolvente de DRF y un 403 nacido en la matriz de
# permisos tienen que ser indistinguibles para el cliente (REQ-078).
DATOS_INVALIDOS = "Revisa los datos introducidos"
PERMISO_DENEGADO = "No tiene permisos para realizar esta acción"
METODO_NO_PERMITIDO = "El método HTTP no está permitido para este recurso"
FORMATO_NO_ACEPTABLE = "No se puede atender la petición en el formato solicitado"
MEDIO_NO_SOPORTADO = "El formato del contenido enviado no está soportado"
DEMASIADAS_PETICIONES = "Has realizado demasiadas peticiones, inténtalo más tarde"
ERROR_INESPERADO = "No se ha podido completar la operación, inténtalo de nuevo más tarde"

# --- Autorizacion (REQ-009, REQ-078, REQ-018, REQ-011) -------------------
# Mensaje UNIFORME de denegacion por rol/permiso: el mismo texto para rol ausente, rol fuera
# de `cat_rol`, usuario inactivo y par rol x operacion sin fila en la matriz. El literal lo
# fija REQ-009 palabra por palabra, asi que no se reescribe al gusto: quien lo cambie rompe
# el DoD. Coincide con `PERMISO_DENEGADO` a proposito (REQ-078), para que la denegacion de
# la envolvente de enrutado sea indistinguible de la del dominio y el cuerpo no delate ni el
# motivo ni la existencia del recurso.
SIN_PERMISOS = "No tiene permisos para realizar esta acción"
PERMISOS_CAMBIADOS = "Sus permisos han cambiado; la acción solicitada ya no está autorizada"
