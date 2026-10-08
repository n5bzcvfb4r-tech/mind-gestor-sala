"""
Extension de drf-spectacular que publica en el contrato OpenAPI el esquema de seguridad real.

drf-spectacular no sabe inspeccionar una clase de autenticacion propia: al generar el
contrato encuentra `AutenticacionSesionOpaca`, no halla ninguna `OpenApiAuthenticationExtension`
registrada para ella, avisa con `could not resolve authenticator` y deja el esquema SIN
documentar como se autentica el servicio. Esta extension cierra ese hueco.

El registro es por IMPORTACION: la metaclase de `OpenApiAuthenticationExtension` da de alta la
clase en cuanto el modulo se importa, y de eso se encarga el `ready()` de `CoreSecurityConfig`.

SECRETOS (REQ-063, REQ-076). Aqui solo se describe la FORMA de la credencial; ningun valor de
sesion ni ejemplo real aparece en el contrato publicado.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


# Texto que se publica en el contrato. Va en espanol y, por convencion del proyecto, el texto
# VISIBLE si lleva la ortografia correcta aunque los identificadores vayan sin tildes.
DESCRIPCION_SESION_OPACA = (
    "Sesión opaca gestionada en servidor. El cliente envía en la cabecera `Authorization: Bearer <session_id>` "
    "el identificador opaco de la sesión (columna `session_id` de la tabla `sesion_usuario`). No es un JWT: la "
    "credencial no es autocontenida y no transporta identidad ni rol, por lo que el servidor la revalida contra "
    "la base de datos en CADA petición y una sesión revocada o caducada deja de servir de inmediato. La "
    "credencial NUNCA viaja en la URL (REQ-056): las peticiones que la envíen como parámetro de consulta se "
    "rechazan con 401 sin llegar a usarla."
)


class EsquemaAutenticacionSesionOpaca(OpenApiAuthenticationExtension):
    """
    Traduce `AutenticacionSesionOpaca` al esquema de seguridad `http`/`bearer` de OpenAPI.

    `target_class` se declara como cadena a proposito: evita importar el modulo de
    autenticacion (y con el los servicios y el acceso a datos) durante la carga de las apps.
    """

    target_class = "apps.core_security.autenticacion.AutenticacionSesionOpaca"
    name = "sesionOpaca"

    def get_security_definition(self, auto_schema) -> dict[str, str]:
        """Devuelve la definicion que se publica en `components.securitySchemes.sesionOpaca`."""

        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "opaque session id",
            "description": DESCRIPCION_SESION_OPACA,
        }
