"""
Literales de usuario del modulo de roles (ARC-013, REQ-005, REQ-043).

Todos los mensajes estan en espanol (REQ-050). Los identificadores van sin tildes por convencion
del proyecto; el texto visible si lleva la ortografia correcta, porque lo lee una persona.

POR QUE LOS LITERALES VIVEN CENTRALIZADOS
-----------------------------------------
El mismo hecho del sistema -«ese rol no existe», «ese usuario ya tiene rol»- se alcanza por mas de
una puerta: el serializador que valida la peticion, el servicio que decide y la excepcion de
dominio que viaja hasta el manejador unico. Si cada una redactase su propio texto, el catalogo se
desalinearia a la primera correccion y el administrador leeria dos explicaciones distintas para un
unico estado. Por eso se declaran aqui una sola vez y NUNCA en linea dentro de la vista, del
serializador o del servicio. Ningun mensaje admite interpolacion: ninguno lleva marcador de
formato, de modo que no puede filtrarse por accidente un dato del usuario afectado.

EL 403 NO ESTA EN ESTE CATALOGO
-------------------------------
Falta a proposito el literal de la denegacion por permisos. Lo compone el manejador unico de
excepciones (`apps.core_security.manejadores`) con su texto uniforme, el mismo para la denegacion
por rol y para la denegacion por la matriz de autorizacion. REQ-078 exige justamente eso: que
ambas sean INDISTINGUIBLES desde fuera. Redactar aqui un 403 propio del modulo de roles crearia un
segundo texto que, por el solo hecho de ser distinto, revelaria cual de las dos reglas corto la
peticion y delataria la existencia del recurso al que se intentaba llegar (AC-ROL-06).
"""

# --- Eleccion del rol (AC-ROL-02, AC-ROL-03 de REQ-043) ------------------
# El rol llega ausente, vacio o con un valor que no esta en `cat_rol`. El texto enumera los dos
# roles asignables para que el administrador corrija sin tener que consultar el catalogo.
ROL_NO_VALIDO = "Debe seleccionar un rol válido: empleado o técnico de mantenimiento"

# --- Conflictos con el estado ya existente (AC-ROL-02, REQ-005 RN-01) ----
# El alta de rol no puede dejar dos filas de rol vigente para la misma persona.
ROL_YA_ASIGNADO = "El usuario ya tiene un rol asignado"

# El cambio pide el rol que el usuario ya ostenta: no hay movimiento que historiar.
MISMO_ROL = "El usuario ya tiene ese rol"

# --- Reglas de negocio del cambio (REQ-043 RN-03) ------------------------
# El administrador intenta retirarse a si mismo el rol de administracion.
AUTORRETIRADA_ROL_ADMINISTRACION = "No puedes retirarte tu propio rol de administrador"

# --- Sujeto de la operacion ----------------------------------------------
# Texto uniforme para el usuario inexistente: no distingue «nunca existio» de «no es visible».
USUARIO_NO_ENCONTRADO = "No hemos encontrado ese usuario"

# REQ-005, escenario de error 5: sobre una baja no se mueve el rol.
USUARIO_DE_BAJA = "El usuario está dado de baja y no admite cambio de rol"

# --- Consulta del historico de rol ---------------------------------------
# Los limites del rango llegan cruzados; se rechaza antes de tocar la base.
RANGO_FECHAS_NO_VALIDO = "La fecha inicial no puede ser posterior a la final"

# Criterio de filtrado desconocido o con valor no admitido.
FILTRO_NO_VALIDO = "Filtro no válido"

# El usuario existe pero no acumula ningun movimiento de rol: no es un error, es un resultado.
HISTORICO_VACIO = "Este usuario no tiene movimientos de rol registrados"


__all__ = [
    "AUTORRETIRADA_ROL_ADMINISTRACION",
    "FILTRO_NO_VALIDO",
    "HISTORICO_VACIO",
    "MISMO_ROL",
    "RANGO_FECHAS_NO_VALIDO",
    "ROL_NO_VALIDO",
    "ROL_YA_ASIGNADO",
    "USUARIO_DE_BAJA",
    "USUARIO_NO_ENCONTRADO",
]
