"""
Literales de usuario del restablecimiento administrativo de credencial (REQ-073, REQ-074).

Todos los mensajes estan en espanol (REQ-050). Los identificadores van sin tildes por
convencion del proyecto; el texto visible si lleva la ortografia correcta.

LOS TEXTOS SON LITERALES DEL REQUISITO, NO REDACCION PROPIA
-----------------------------------------------------------
REQ-073 (restablecimiento de contrasenia por administrador, EP-017) y REQ-074 (consulta y
busqueda de usuarios) fijan palabra por palabra lo que ve el administrador cuando su peticion
no prospera. Viven aqui y NUNCA en linea dentro del servicio, de la vista o del serializador:
un literal escrito dos veces se desalinea a la primera correccion, y el mismo estado del
sistema pasaria a explicarse de dos maneras distintas segun la puerta por la que se entre.

AQUI NO HAY RIESGO DE ORACULO, PERO SI DE FILTRACION
----------------------------------------------------
A diferencia del login (REQ-072, RN-05), estos textos los recibe un actor YA autenticado y
autorizado, cuyo alcance ha validado antes la capa de permisos: distinguir «no existe» (404)
de «esta inactivo» (409) es informacion que necesita para actuar y no permite enumerar
cuentas a quien no pudiera listarlas de todas formas. Lo que si esta prohibido es que un
literal hable de la CREDENCIAL: ningun texto de este catalogo admite interpolacion con la
contrasenia temporal generada, su longitud, su `password_hash` ni fragmento alguno de ellos
(REQ-063, REQ-076). De hecho NINGUN mensaje de este modulo lleva marcador de formato.

EL LITERAL DEL 502 NO ESTA AQUI
-------------------------------
Cuando falla la entrega del correo con el acceso temporal, el texto visible lo publica el
modulo de avisos (`apps.avisos.credenciales.entrega.MENSAJE_502_POR_TIPO`), que es quien sabe
por que tipo de aviso se ha fallado. Copiarlo aqui crearia una segunda version del mismo
mensaje, condenada a divergir. `CREDENCIAL_NO_GENERABLE`, en cambio, si vive aqui: describe un
fallo del generador de este paquete, no de la entrega.
"""

# --- Autorizacion (REQ-073, REQ-074) -------------------------------------
# Literal del 403 de REQ-073: el actor autenticado no tiene el permiso de restablecimiento.
SIN_PERMISOS_RESTABLECER = "No tienes permisos para restablecer contraseñas"

# Literal del 403 de REQ-074: el actor autenticado no tiene el permiso de consulta de usuarios.
SIN_PERMISOS_CONSULTAR = "No tienes permisos para consultar usuarios"

# --- Estado del usuario objetivo (REQ-073, REQ-074) ----------------------
# Literal del 404: el identificador recibido no corresponde a ningun usuario.
USUARIO_NO_ENCONTRADO = "Usuario no encontrado"

# Literal del 409: el usuario existe pero esta inactivo (baja logica, REQ-047). Restablecerle la
# credencial no le devolveria el acceso, asi que responder 200 seria mentir sobre el efecto de la
# operacion; reactivar la cuenta es otra operacion distinta, con su permiso y su traza propios.
CUENTA_INACTIVA = "El usuario está inactivo"

# Literal del 409 de la validacion 3 de REQ-073: el administrador se restablece a si mismo. El
# camino correcto es el cambio de contrasenia propio (EP-005), que exige la contrasenia actual.
AUTORRESTABLECIMIENTO_NO_PERMITIDO = "No puedes restablecer tu propia contraseña; usa el cambio de contraseña propio"

# --- Validacion de la peticion (REQ-073, REQ-074) ------------------------
# Literal del 422 de REQ-073: el motivo que justifica el restablecimiento excede el campo que lo
# persiste. El numero del texto y el del modelo son el mismo contrato: 250 caracteres.
MOTIVO_DEMASIADO_LARGO = "El motivo del restablecimiento no puede superar los 250 caracteres"

# Literal del 422 de REQ-074: busquedas de uno o dos caracteres devolverian practicamente el
# censo entero y obligarian a un recorrido completo de tabla sin aportar nada al administrador.
BUSQUEDA_DEMASIADO_CORTA = "El texto de búsqueda debe tener al menos 3 caracteres"

# Literal informativo de REQ-074 para el listado vacio. NO es un error: acompania a una respuesta
# correcta con cero resultados, porque «no hay nadie que cumpla los filtros» es un desenlace
# legitimo de una busqueda y no un fallo de la peticion.
SIN_RESULTADOS = "No hay usuarios que cumplan los filtros"

# Literal del fallo del generador de credencial temporal de este paquete (REQ-073 regla 4). Es
# deliberadamente opaco: no dice cuantos intentos se hicieron, ni con que alfabeto, ni que regla
# de politica se incumplio, porque cualquiera de esos datos acotaria el espacio de busqueda del
# secreto generado. El detalle tecnico va a la traza, nunca al usuario.
CREDENCIAL_NO_GENERABLE = "No se ha podido generar la credencial temporal"


__all__ = [
    "AUTORRESTABLECIMIENTO_NO_PERMITIDO",
    "BUSQUEDA_DEMASIADO_CORTA",
    "CREDENCIAL_NO_GENERABLE",
    "CUENTA_INACTIVA",
    "MOTIVO_DEMASIADO_LARGO",
    "SIN_PERMISOS_CONSULTAR",
    "SIN_PERMISOS_RESTABLECER",
    "SIN_RESULTADOS",
    "USUARIO_NO_ENCONTRADO",
]
