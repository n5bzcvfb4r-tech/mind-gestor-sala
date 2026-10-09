"""
Literales de usuario del bloqueo temporal de cuenta (REQ-055, REQ-072, REQ-075).

Todos los mensajes estan en espanol (REQ-050). Los identificadores van sin tildes por
convencion del proyecto; el texto visible si lleva la ortografia correcta.

LOS TEXTOS SON LITERALES DEL REQUISITO, NO REDACCION PROPIA
-----------------------------------------------------------
REQ-072 y REQ-075 fijan palabra por palabra lo que ve el usuario cuando su cuenta queda
bloqueada y lo que ve el administrador cuando intenta desbloquear una cuenta que no lo esta.
Viven aqui y NUNCA en linea dentro del servicio, de la vista o del serializador: un literal
escrito dos veces se desalinea a la primera correccion, y el mismo estado del sistema
pasaria a explicarse de dos maneras distintas segun la puerta por la que se entre.

NINGUN MENSAJE SIRVE DE ORACULO EN AUTENTICACION (REQ-072, RN-05)
------------------------------------------------------------------
`CUENTA_BLOQUEADA_HASTA` se devuelve en el flujo de login y por eso solo habla del BLOQUEO y
de la hora en que expira: no dice si el usuario existe, si la contrasenia era correcta ni
cuantos intentos quedan. El 423 se emite unicamente cuando ya hay un bloqueo VIGENTE, que es
un estado que el propio atacante ha provocado y ya conoce; cualquier otra combinacion
-usuario inexistente, usuario inactivo, contrasenia incorrecta- comparte el 401 generico de
`apps.core_security.mensajes.CREDENCIALES_INVALIDAS` para no permitir enumerar cuentas.

Los literales de REQ-075 (`CUENTA_NO_BLOQUEADA`, `USUARIO_NO_ENCONTRADO`, `CUENTA_INACTIVA`)
pertenecen al flujo ADMINISTRATIVO de desbloqueo, donde el solicitante ya esta autenticado y
autorizado: alli distinguir «no existe» de «no esta bloqueada» no filtra nada a un anonimo y
si es informacion que el administrador necesita para actuar.

NUNCA SE INTERPOLAN CON LA CONTRASENIA NI CON EL HASH (REQ-063, REQ-076)
-------------------------------------------------------------------------
El unico valor que admite interpolacion en todo el catalogo es `{hora}` de
`CUENTA_BLOQUEADA_HASTA`, formateada como `HH:MM`. Ningun texto de este modulo acepta -ni
puede aceptar- la contrasenia intentada, su longitud, su `password_hash`, ningun fragmento
de ellos ni el contador de intentos fallidos: el mensaje describe el ESTADO de la cuenta, no
el material de la credencial que lo provoco.
"""

# --- Bloqueo vigente en autenticacion (423, REQ-072) ---------------------
# Literal exacto de REQ-072. `{hora}` lo formatea `errores.CuentaBloqueadaTemporalmenteError`
# a partir de `locked_until` en `HH:MM`: se da la hora de desbloqueo y no los minutos
# restantes porque un contador relativo caduca en cuanto el usuario tarda en leerlo, y porque
# es el dato que el propio requisito exige mostrar (AC-PWD-09).
CUENTA_BLOQUEADA_HASTA = "Cuenta bloqueada temporalmente. Inténtalo de nuevo a las {hora} o contacta con el administrador"

# --- Desbloqueo administrativo (REQ-075) ---------------------------------
# Literal del 409 de REQ-075: se pide desbloquear una cuenta que no tiene bloqueo vigente. Es
# conflicto y no exito silencioso a proposito: el administrador debe saber que su accion no ha
# cambiado nada, en vez de creer que ha resuelto una incidencia que nunca existio (AC-RST-07).
CUENTA_NO_BLOQUEADA = "La cuenta no está bloqueada"

# Literal del 404 de REQ-075: el identificador no corresponde a ningun usuario.
USUARIO_NO_ENCONTRADO = "Usuario no encontrado"

# Escenario de error 4 de REQ-075: la cuenta existe pero esta inactiva (baja logica, REQ-047).
# Una cuenta inactiva no admite desbloqueo porque desbloquearla no le devolveria el acceso:
# reactivarla es otra operacion, con su propio permiso y su propia traza.
CUENTA_INACTIVA = "La cuenta no está activa"
