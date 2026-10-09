"""
Literales de usuario de la consulta del censo (EP-008, REQ-006, REQ-039).

Todos los mensajes estan en espanol (REQ-050). Los identificadores van sin tildes por convencion del
proyecto; el texto visible si lleva la ortografia correcta.

LOS TEXTOS VIVEN AQUI Y NUNCA EN LINEA
--------------------------------------
Serializador, servicio y vista los IMPORTAN. Un literal escrito dos veces se desalinea a la primera
correccion y el mismo estado del sistema pasaria a explicarse de dos maneras distintas segun la
puerta por la que se entre; con un unico catalogo, cambiar el texto es cambiar un sitio.

UN FILTRO MAL FORMADO SE EXPLICA IGUAL VENGA DE DONDE VENGA
-----------------------------------------------------------
`FILTRO_NO_VALIDO` lo comparten el filtro de rol (que se comprueba contra el catalogo `cat_rol`
leido de la base) y el de estado (dominio cerrado ACTIVO/INACTIVO). Son dos comprobaciones
tecnicamente distintas pero el mismo desenlace para quien consulta: el valor que ha pedido no es un
filtro que el sistema sepa aplicar. El detalle de CUAL de los dos campos fallo lo pone DRF, que
devuelve el error asociado a su campo.

CERO COINCIDENCIAS NO ES UN ERROR
---------------------------------
`SIN_RESULTADOS` acompania a una respuesta 200 CORRECTA con `items` vacio (AC-USR-05): «no hay nadie
que cumpla los filtros» es un desenlace legitimo de una busqueda, no un fallo de la peticion. Por
eso este literal no viaja nunca por el camino de error, sino dentro del cuerpo de la pagina.

NINGUN MENSAJE LLEVA MARCADOR DE FORMATO
----------------------------------------
No hay interpolacion posible, asi que ningun texto puede acabar revelando el valor concreto que se
busco, un correo del censo ni material de credencial (REQ-063, REQ-079).
"""

# --- Autorizacion (REQ-006) ----------------------------------------------
# Literal del 403: el actor autenticado no tiene el permiso de consulta del censo de usuarios.
SIN_PERMISOS_CONSULTA = "No tienes permisos para consultar los usuarios"

# --- Validacion de los filtros (REQ-006, REQ-039) ------------------------
# Literal del 400 compartido por el filtro de rol fuera del catalogo `cat_rol` y por el filtro de
# estado fuera del dominio cerrado ACTIVO/INACTIVO. Ver la nota del modulo sobre por que es el mismo.
FILTRO_NO_VALIDO = "Filtro no válido"

# Literal del 400 cuando la pagina pedida no es un entero positivo. La numeracion de paginas empieza
# en 1 y no en 0: es la que ve el usuario en el paginador, no un indice de array.
PAGINA_NO_VALIDA = "La página solicitada debe ser un entero mayor o igual que 1"

# Literal del 400 cuando el tamanio de pagina se sale del rango admitido. El tope de 100 no es
# cosmetico: protege a la base y al proceso de una peticion que pida el censo entero de una vez.
TAMANIO_PAGINA_NO_VALIDO = "El tamaño de página debe ser un entero entre 1 y 100"

# Literal del 400 cuando el texto de busqueda excede lo que cabe en las columnas sobre las que se
# busca: un termino mas largo que el propio dato no puede coincidir con nada.
BUSQUEDA_DEMASIADO_LARGA = "El texto de búsqueda no puede superar los 150 caracteres"

# --- Listado vacio (AC-USR-05) -------------------------------------------
# Literal INFORMATIVO que viaja dentro de la respuesta 200 cuando ningun usuario cumple los filtros.
# No es un error: ver la nota del modulo.
SIN_RESULTADOS = "Ningún usuario coincide con los filtros aplicados"


__all__ = [
    "BUSQUEDA_DEMASIADO_LARGA",
    "FILTRO_NO_VALIDO",
    "PAGINA_NO_VALIDA",
    "SIN_PERMISOS_CONSULTA",
    "SIN_RESULTADOS",
    "TAMANIO_PAGINA_NO_VALIDO",
]
