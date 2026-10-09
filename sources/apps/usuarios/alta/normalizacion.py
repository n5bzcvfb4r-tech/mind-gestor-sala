"""
Normalizacion de los datos de identidad del usuario (REQ-037, REQ-045, RN-01).

Funciones PURAS: no tocan la base de datos, no leen `settings`, no miran el reloj y no lanzan
excepciones de dominio. Reciben texto y devuelven texto. Esa pureza es lo que permite que PBT-001
las ejercite con entradas generadas sin levantar nada, y tambien lo que permite que el modulo de
CONSULTA del censo (EP-008) las reutilice tal cual: si el alta normalizase de una manera y la
busqueda de otra, un administrador no encontraria al usuario que acaba de crear.

NORMALIZAR NO ES VALIDAR
------------------------
Aqui no se decide si un nombre es demasiado corto ni si un correo tiene forma de correo: eso es
del serializador, que es quien puede atribuir el fallo a un campo concreto y devolver el literal
de `mensajes`. Lo que si se declaran aqui son los LIMITES numericos, porque son los del esquema
T.5 y tienen que existir en un solo sitio: el serializador los importa en vez de reescribirlos.
El orden correcto es siempre normalizar PRIMERO y validar DESPUES, para que un nombre que solo
tenia espacios se rechace por vacio y para que un correo no se rechace por una longitud que iba a
desaparecer con el recorte.

AQUI NO SE PLIEGAN ACENTOS NI SE TOCA UNICODE
---------------------------------------------
No se usa `unicodedata` a proposito. Plegar tildes o pasar a NFKD cambiaria el correo que el
administrador escribio y, sobre todo, lo apartaria de la expresion del indice unico funcional
`ux_usuario_email_ci`, que es `LOWER(TRIM(corporate_email))` y nada mas: la comprobacion previa y
la restriccion de la base dejarian de ver lo mismo. En los nombres, ademas, borrar la tilde seria
corromper el dato personal de la persona (RN-06).
"""

from __future__ import annotations


#: REQ-037: `full_name` tiene entre 2 y 120 caracteres. El maximo es la anchura real de
#: `usuario.full_name` en el esquema T.5; el minimo descarta iniciales sueltas y los nombres que
#: han quedado vacios -o casi- despues de recortar espacios.
LONGITUD_MINIMA_NOMBRE = 2
LONGITUD_MAXIMA_NOMBRE = 120

#: REQ-037: anchura real de `usuario.corporate_email` en el esquema T.5. Se mide SOBRE LA FORMA YA
#: NORMALIZADA, que es la que se persiste: medir el valor en bruto rechazaria correos validos cuyo
#: exceso eran espacios destinados a desaparecer.
LONGITUD_MAXIMA_CORREO = 150


def normalizar_nombre(valor: str | None) -> str:
    """
    Devuelve el nombre con los extremos recortados y los espacios internos colapsados a uno solo.

    REQ-037 pide explicitamente «normaliza espacios» en `full_name`. No basta con `strip()`: el
    pegado desde una hoja de calculo o un tabulador perdido dejan huecos dobles en mitad del
    nombre, y entonces «Ana  Lopez» y «Ana Lopez» serian dos textos distintos para cualquier
    busqueda del censo (EP-008) aunque nombren a la misma persona. `" ".join(valor.split())`
    resuelve los dos problemas a la vez y trata cualquier blanco -espacio, tabulador, salto de
    linea- como separador.

    `None` devuelve `""` y NO revienta: quien llama es un serializador que esta construyendo la
    lista de errores del formulario, y un campo ausente debe acabar en el literal
    `mensajes.NOMBRE_REQUERIDO` igual que un campo con solo espacios, no en un `AttributeError`.

    Ejemplos:
        >>> normalizar_nombre("  Ana   Lopez  Diaz ")
        'Ana Lopez Diaz'
        >>> normalizar_nombre("\\tMarta\\nRuiz")
        'Marta Ruiz'
        >>> normalizar_nombre("   ")
        ''
        >>> normalizar_nombre(None)
        ''

    Args:
        valor: nombre tal y como llego en la peticion, o `None` si el campo no venia.

    Returns:
        El nombre normalizado, o cadena vacia si no habia contenido.
    """

    if valor is None:
        return ""
    return " ".join(valor.split())


def normalizar_correo(valor: str | None) -> str:
    """
    Devuelve el correo corporativo en MINUSCULAS y SIN NINGUN espacio: su forma canonica.

    ESTA ES LA FORMA CANONICA, Y ES UNA SOLA (REQ-045, RN-01)
    ---------------------------------------------------------
    El valor que devuelve esta funcion es a la vez el que se COMPARA para decidir la unicidad y el
    que se PERSISTE en `usuario.corporate_email`. Son el mismo, no dos derivados del original, y
    esa igualdad es exactamente la invariante que acredita PBT-001: si se comparase la forma
    normalizada pero se guardase el texto original, dos altas que solo difieren en mayusculas
    chocarian la primera vez y, despues de cualquier reescritura del original, dejarian de chocar.
    Guardar la forma canonica tambien hace que `LOWER(TRIM(corporate_email))` -la expresion del
    indice unico funcional `ux_usuario_email_ci`- sea la IDENTIDAD sobre lo ya almacenado, de modo
    que la comprobacion previa del repositorio y la restriccion de la base no pueden discrepar.

    Se quitan TODOS los espacios, no solo los de los extremos (`"".join(valor.split())`): un
    espacio en mitad de una direccion de correo nunca es parte de ella, siempre es basura del
    pegado desde un cliente de correo o desde un listado. `strip()` dejaria «ana lopez@x.com»
    intacto y acabaria persistido como la identidad de alguien.

    `None` devuelve `""` y NO revienta, por el mismo motivo que en `normalizar_nombre`: el campo
    ausente tiene que desembocar en el literal `mensajes.CORREO_REQUERIDO`.

    Ejemplos:
        >>> normalizar_correo("  Ana.Lopez@Empresa.COM  ")
        'ana.lopez@empresa.com'
        >>> normalizar_correo("ana.lopez @ empresa.com")
        'ana.lopez@empresa.com'
        >>> normalizar_correo("ANA.LOPEZ@EMPRESA.COM") == normalizar_correo("ana.lopez@empresa.com")
        True
        >>> normalizar_correo(None)
        ''

    Args:
        valor: correo tal y como llego en la peticion, o `None` si el campo no venia.

    Returns:
        La forma canonica del correo, o cadena vacia si no habia contenido.
    """

    if valor is None:
        return ""
    return "".join(valor.split()).lower()


__all__ = [
    "LONGITUD_MAXIMA_CORREO",
    "LONGITUD_MAXIMA_NOMBRE",
    "LONGITUD_MINIMA_NOMBRE",
    "normalizar_correo",
    "normalizar_nombre",
]
