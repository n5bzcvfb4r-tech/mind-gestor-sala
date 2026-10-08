"""
Literales de usuario y de error del nucleo transversal.

Todos los mensajes estan en espanol (REQ-050). Los identificadores van sin tildes
por convencion del proyecto; el texto visible si lleva la ortografia correcta cuando
procede, salvo en los terminos tecnicos normalizados sin tilde del glosario interno.
"""

# --- Integridad de datos -------------------------------------------------
BORRADO_FISICO_NO_PERMITIDO = "No se permite el borrado fisico de registros: las bajas son logicas (REQ-047)."
REGISTRO_HISTORICO_INMUTABLE = (
    "Las entradas de historico y auditoria son inmutables y no se pueden modificar ni eliminar (REQ-015, REQ-065)."
)

# --- Contexto de sesion y atribucion -------------------------------------
SIN_CONTEXTO_DE_SESION = "No hay contexto de sesion activo: la atribucion requiere un usuario autenticado."

# --- Catalogos maestros --------------------------------------------------
CATALOGO_VACIO = "El catalogo «{tabla}» no tiene filas: las semillas del changelog (ARC-016) no se han aplicado."
CATALOGOS_VACIOS_ARRANQUE = "El servicio no puede arrancar: faltan las semillas de los catalogos maestros."
ERROR_VERIFICANDO_CATALOGOS = "No se ha podido verificar el estado de los catalogos maestros: {detalle}"

# --- Recursos ------------------------------------------------------------
RECURSO_NO_ENCONTRADO = "No se ha encontrado el recurso solicitado."
