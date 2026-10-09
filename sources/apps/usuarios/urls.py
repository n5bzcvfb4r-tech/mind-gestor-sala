"""
Rutas de la app de usuarios (contrato `openapi.yaml`).

El proyecto monta esta app bajo `api/` en `config/urls.py`, que es el `servers[0].url` del
contrato, asi que los paths se declaran SIN barra inicial ni final: las URL publicas resultantes son
EXACTAMENTE `/api/users` (EP-007 y EP-008), `/api/users/{userId}/role` (EP-011) y
`/api/users/{userId}/role-history` (EP-012), las que publica el contrato.

`users` es UNA sola ruta servida por UNA sola vista: el contrato publica ahi DOS operaciones
(EP-007 `POST` y EP-008 `GET`) y Django casa por path, de modo que dos `path()` sobre el mismo
string dejarian el segundo inalcanzable. Los dos verbos los atiende `CensoUsuariosView`, igual
que `SesionActualView` atiende EP-002 y EP-003 sobre `auth/sessions/current`.
"""

from django.urls import path

from apps.usuarios.roles.views import HistoricoRolUsuarioView, RolUsuarioView
from apps.usuarios.views import CensoUsuariosView


app_name = "usuarios"

urlpatterns = [
    # QUE ENDPOINTS DEL ARBOL `/users/...` NO DECLARA ESTA APP, Y POR QUE. EP-017
    # (`users/<user_id>/password-reset`), EP-018 (`users/<user_id>/unlock`) y EP-019
    # (`users/credential-status`) cuelgan del mismo arbol del contrato, pero los sirve
    # `apps.identidad`, que es su DUENO SEMANTICO: la credencial -generacion de la temporal,
    # entrega, revocacion de sesiones y consulta del estado resultante- y el bloqueo de cuenta
    # -politica, contador de intentos y atajo administrativo- viven enteros en los modulos de
    # reposicion y bloqueo de ARC-013, tal y como documenta `apps/identidad/urls.py`. Declararlos
    # tambien aqui los DUPLICARIA: un mismo path en dos URLconf deja el segundo inalcanzable y
    # repartiria la responsabilidad entre dos apps.
    path("users", CensoUsuariosView.as_view(), name="users"),
    # EP-011 y EP-012 SI los declara `usuarios`, a diferencia de EP-017, EP-018 y EP-019: su dueno
    # semantico es el modulo de ROLES de ARC-013, que vive aqui (`apps.usuarios.roles`), de modo que
    # la ruta se declara en la app que tiene el caso de uso. Van DESPUES de `users` por el mismo
    # criterio de orden que documenta `apps/identidad/urls.py`: los paths mas literales y generales
    # primero y los que llevan convertidor `<int:user_id>` despues, para que la intencion quede
    # explicita y ninguna ruta generica pueda engullir a otra si algun dia se relaja el convertidor.
    path("users/<int:user_id>/role", RolUsuarioView.as_view(), name="users-role"),
    path("users/<int:user_id>/role-history", HistoricoRolUsuarioView.as_view(), name="users-role-history"),
]
