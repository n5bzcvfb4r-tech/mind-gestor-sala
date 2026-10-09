"""
Entrega de los avisos de credencial de acceso (ARC-014, REQ-038, REQ-073).

Cubre los dos avisos que llevan un secreto al buzon del usuario: `CREDENTIAL_ISSUED`, la credencial
inicial que se emite al dar de alta una cuenta (REQ-038), y `PASSWORD_RESET`, el acceso temporal que
genera un ADMINISTRADOR al restablecer la contrasena de otro usuario (REQ-073). Los dos comparten el
mismo problema y por eso comparten paquete: la credencial NO se persiste en ninguna parte, de modo
que el correo no lo puede redactar el despachador de fondo a partir de la fila, sino que se compone
en memoria y se entrega de forma sincrona en la misma peticion que creo el secreto.

Este modulo es la FACHADA del paquete: los consumidores importan desde `apps.avisos.credenciales` y
no desde los submodulos, para que la reorganizacion interna no rompa a nadie.

Importar el paquete es seguro ANTES de `django.setup()`: ningun submodulo importa modelos del ORM a
nivel de modulo (los resuelve de forma perezosa o solo bajo `TYPE_CHECKING`), de modo que la fachada
no fuerza la carga del registro de aplicaciones.
"""
