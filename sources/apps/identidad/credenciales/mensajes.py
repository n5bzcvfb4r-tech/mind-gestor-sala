"""
Literales de usuario de la custodia de credenciales (REQ-054, REQ-069).

Todos los mensajes estan en espanol (REQ-050). Los identificadores van sin tildes por
convencion del proyecto; el texto visible si lleva la ortografia correcta.

UN SOLO CATALOGO DE TEXTOS PARA TRES FLUJOS
-------------------------------------------
Cambio de contrasenia propio (EP-005), primer acceso (EP-006) y restablecimiento por
administrador (EP-017) establecen contrasenia y, por REQ-069, comparten UNA politica. Si
cada flujo redactase su propio texto de rechazo, la politica dejaria de percibirse como
unica: el mismo incumplimiento se explicaria de tres maneras distintas y el usuario no
sabria que la contrasenia rechazada en uno lo es en todos. Por eso los literales viven
aqui y NUNCA en linea dentro del servicio, de la vista o del serializador.

LOS MENSAJES DESCRIBEN LA REGLA, JAMAS EL SECRETO
-------------------------------------------------
Regla de negocio explicita: «un mensaje de rechazo por politica no revela el hash ni pistas
sobre la contrasenia anterior». Por eso ningun texto de este modulo admite interpolar la
contrasenia propuesta, la anterior, su hash, su longitud real ni fragmento alguno de ella:
los unicos valores que se interpolan son los PARAMETROS DE LA POLITICA (el minimo de
caracteres y cuantas contrasenias historicas se comparan), que son publicos por definicion
porque el propio requisito los publica. `REUTILIZADA` es el caso mas delicado: dice que la
contrasenia coincide con una de las ultimas usadas, y ni insinua con cual ni que aspecto
tenia; saber que una propuesta ya se uso no aporta al atacante nada que no supiera al
proponerla.

EL 500 NO EXPLICA NADA (REQ-054)
--------------------------------
`CUSTODIA_NO_COMPLETADA` es el texto LITERAL que fija REQ-054 para cualquier fallo de la
custodia (cifrado/hashing caido, material no hasheado llegando a la persistencia). Es el
mismo para todos esos casos a proposito: el detalle tecnico se queda en el log junto al
`traceId` y no viaja en la respuesta.
"""

# --- Reglas de la politica de contrasenia (REQ-069) ----------------------
# Plantillas con los PARAMETROS de la politica, nunca con datos de la contrasenia. Las
# formatea `politica.py`, que es quien posee las constantes `LONGITUD_MINIMA` y
# `MAXIMO_CONTRASENIAS_HISTORICAS`; asi el numero se escribe una sola vez en el codigo y el
# texto no se desalinea del valor que de verdad se evalua.
POLITICA_LONGITUD_MINIMA = "La contraseña debe tener al menos {minimo} caracteres"
POLITICA_SIN_MAYUSCULA = "La contraseña debe contener al menos una letra mayúscula"
POLITICA_SIN_MINUSCULA = "La contraseña debe contener al menos una letra minúscula"
POLITICA_SIN_DIGITO = "La contraseña debe contener al menos un dígito"
POLITICA_CONTIENE_IDENTIFICADOR = "La contraseña no puede contener tu nombre de usuario ni tu dirección de correo corporativo"
POLITICA_REUTILIZADA = "La contraseña no puede coincidir con ninguna de las {maximo} últimas contraseñas utilizadas"

# --- Cabecera del rechazo por politica (422) -----------------------------
# Texto generico de la respuesta 422. El QUE ha fallado viaja en la lista de reglas
# incumplidas (`details`), no en esta cabecera, que es identica para los tres flujos.
POLITICA_NO_CUMPLIDA = "La contraseña propuesta no cumple la política de seguridad"

# --- Fallo de custodia (500, REQ-054) ------------------------------------
# Literal exacto de REQ-054: «500 'No ha sido posible completar la operacion' si falla el
# cifrado, sin detalle tecnico al usuario». No se reescribe al gusto.
CUSTODIA_NO_COMPLETADA = "No ha sido posible completar la operación"

# Motivo INTERNO del rechazo de persistir material no hasheado (REQ-054, validacion 1). Va a
# la traza tecnica, nunca al cuerpo de la respuesta: hacia fuera ese fallo se ve como el 500
# generico de arriba, porque contarle al cliente que algo llego sin hashear describiria el
# estado interno de la custodia sin ayudarle en nada.
CONTRASENIA_SIN_HASHEAR = "Se ha rechazado persistir una contraseña que no procede del servicio de hashing"
