"""
Literales de usuario del alta de usuario (EP-007, REQ-037, REQ-045).

Todos los mensajes estan en espanol (REQ-050). Los identificadores van sin tildes por convencion
del proyecto; el texto visible si lleva la ortografia correcta, porque lo lee una persona.

LOS TEXTOS SON LITERALES DEL REQUISITO, NO REDACCION PROPIA
-----------------------------------------------------------
REQ-037 fija palabra por palabra lo que ve el administrador cuando su alta no prospera: los tres
«Indica...»/«Selecciona...» de los campos obligatorios, el 409 del correo repetido y el 403 de
permisos. Viven aqui y NUNCA en linea dentro del servicio, del serializador o de la vista: un
literal escrito dos veces se desalinea a la primera correccion, y el mismo estado del sistema
pasaria a explicarse de dos maneras distintas segun la puerta por la que se entre.

LOS NUMEROS DEL TEXTO Y LOS DEL MODELO SON EL MISMO CONTRATO
------------------------------------------------------------
«entre 2 y 120 caracteres» y «no puede superar los 150 caracteres» no son adornos: son las
longitudes reales de `usuario.full_name` y `usuario.corporate_email` en el esquema T.5. Si una
cambiase, el literal tiene que cambiar con ella; por eso los limites numericos se declaran una
sola vez en `apps.usuarios.alta.normalizacion` y este catalogo solo los narra.

AQUI NO HAY RIESGO DE ORACULO
-----------------------------
A diferencia del login (REQ-072, RN-05), estos textos los recibe un actor YA autenticado y
autorizado como ADMINISTRADOR: decirle que el correo ya esta registrado es informacion que
necesita para corregir el alta y no permite enumerar cuentas a quien no pudiera listarlas de
todas formas por EP-008. Lo que si esta prohibido es que un literal de este catalogo hable de la
credencial inicial generada o de su `password_hash` (REQ-063): ningun mensaje admite
interpolacion, de hecho NINGUNO lleva marcador de formato.
"""

# --- Obligatoriedad de los datos del alta (REQ-037, validaciones) --------
# Literales del 400 de REQ-037: el formulario llega sin alguno de los tres datos del alta. Cada
# campo tiene su propio texto para que el administrador sepa CUAL falta sin tener que adivinarlo;
# un unico «faltan datos» le obligaria a revisar los tres.
NOMBRE_REQUERIDO = "Indica el nombre del usuario"

CORREO_REQUERIDO = "Indica el correo corporativo"

# REQ-037 regla 2 (PRE-03): no se admite un usuario sin rol, y el rol se elige en el MISMO acto de
# alta. No hay valor por defecto que poner aqui: un rol implicito concederia permisos que nadie ha
# decidido conscientemente.
ROL_REQUERIDO = "Selecciona un rol"

# --- Formato y longitud (REQ-037, datos) ---------------------------------
# Limites de `usuario.full_name` (varchar(120)). El minimo de 2 descarta iniciales sueltas y
# nombres vacios que han sobrevivido al recorte de espacios.
NOMBRE_LONGITUD_NO_VALIDA = "El nombre debe tener entre 2 y 120 caracteres"

CORREO_NO_VALIDO = "El correo corporativo no tiene un formato válido"

# Limite de `usuario.corporate_email` (varchar(150)). Se comprueba sobre la forma YA normalizada,
# que es la que se va a persistir: medir el valor en bruto rechazaria correos validos que solo
# excedian por espacios que iban a desaparecer.
CORREO_DEMASIADO_LARGO = "El correo corporativo no puede superar los 150 caracteres"

# REQ-037 validacion 3: el rol debe ser un valor del catalogo `cat_rol`, que se consulta en la
# base. No se enumeran aqui los codigos: el catalogo es su unica fuente de verdad y copiarlos
# crearia una segunda que se desalinearia en cuanto se diese de baja o se anadiese un rol.
ROL_NO_VALIDO = "El rol indicado no pertenece al catálogo de roles disponibles"

# --- Unicidad del correo (REQ-037 regla 3, REQ-045, RN-01) ---------------
# Literal del 409. Es el MISMO texto tanto si lo decide la comprobacion previa como si lo decide
# la violacion del indice unico funcional `ux_usuario_email_ci` en una carrera entre dos altas
# simultaneas: para el administrador el hecho es identico -ese correo ya identifica a alguien- y
# distinguir ambos caminos solo expondria un detalle de implementacion.
CORREO_DUPLICADO = "Ya existe un usuario con ese correo corporativo"

# --- Autorizacion (REQ-037, seguridad) -----------------------------------
# Literal del 403 de REQ-037: el actor autenticado no es ADMINISTRADOR. EMPLEADO y
# TECNICO-DE-MANTENIMIENTO reciben exactamente este texto.
SIN_PERMISOS_ALTA = "No tienes permisos para dar de alta usuarios"


__all__ = [
    "CORREO_DEMASIADO_LARGO",
    "CORREO_DUPLICADO",
    "CORREO_NO_VALIDO",
    "CORREO_REQUERIDO",
    "NOMBRE_LONGITUD_NO_VALIDA",
    "NOMBRE_REQUERIDO",
    "ROL_NO_VALIDO",
    "ROL_REQUERIDO",
    "SIN_PERMISOS_ALTA",
]
