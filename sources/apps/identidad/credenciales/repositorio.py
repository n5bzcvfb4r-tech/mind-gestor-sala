"""
Acceso a datos del historico de contrasenias (REQ-069, AC-PWD-04, tabla `usuario_password_historico`).

Este modulo SOLO construye consultas y persiste filas. No decide reglas, no evalua la politica, no
abre transacciones y no hace `commit` ni `rollback`: la frontera transaccional es el metodo del
servicio (`servicio.ServicioCustodiaCredenciales.establecer`), que es quien sabe que el archivado del
hash anterior y la actualizacion de `usuario` tienen que caer juntos o no caer. Si Oracle falla, la
excepcion sube intacta para que la decision se tome una sola vez y en un unico sitio.

LA TABLA ES APPEND-ONLY Y EL RECORTE LO HACE LA BASE
----------------------------------------------------
`UsuarioPasswordHistoricoEntity` hereda de `RegistroInmutableMixin`: se INSERTA y nunca se reescribe
ni se borra (REQ-047, REQ-069). El recorte a las 3 ultimas entradas por usuario lo ejecuta el trigger
de sentencia `trg_usuario_pwd_hist_purga` del DDL, no la aplicacion. Reimplementar esa purga aqui
duplicaria la invariante en dos sitios y la dejaria dependiendo de que la aplicacion se acuerde de
llamarla; viviendo en la base, se cumple siempre, tambien para cualquier escritura que no pase por
este repositorio.

SECRETOS (REQ-063, REQ-076)
---------------------------
`password_hash` es material criptografico: este modulo no lo escribe en logs, ni en `repr`, ni en
mensajes de error, y no registra ninguna traza por ese motivo. La contrasenia en claro no entra
jamas en este modulo: aqui solo circulan hashes ya calculados por el servicio de hashing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

from apps.core.contexto import utc_now
from apps.core.repositorios import RepositorioBase
from apps.identidad.credenciales.politica import MAXIMO_CONTRASENIAS_HISTORICAS

if TYPE_CHECKING:  # pragma: no cover - solo para el tipado, evita importar modelos antes de django.setup()
    from apps.core.models import UsuarioPasswordHistoricoEntity


class RepositorioHistoricoPassword(RepositorioBase):
    """
    Lectura y escritura del historico de hashes de contrasenia de un usuario (ARC-111).

    Como el resto de repositorios del servicio hereda de `RepositorioBase` y por tanto NO publica
    ninguna operacion de borrado (REQ-047): aqui no hay nada que borrar, el historico es append-only
    y su unico recorte lo hace el trigger de la base.

    SIN CACHE, y es deliberado: la comprobacion de no reutilizacion debe leer el historico vigente en
    el instante del cambio de contrasenia. Una copia en memoria aceptaria una contrasenia que acaba
    de archivarse en otra peticion.
    """

    def __init__(self, modelo: type[models.Model] | None = None) -> None:
        if modelo is None:
            from apps.core.models import UsuarioPasswordHistoricoEntity

            modelo = UsuarioPasswordHistoricoEntity
        super().__init__(modelo)

    def hashes_recientes(self, user_id: int, limite: int = MAXIMO_CONTRASENIAS_HISTORICAS) -> list[str]:
        """
        Hashes archivados del usuario, del mas reciente al mas antiguo.

        `created_at` es el instante en que cada contrasenia DEJO de ser vigente, de modo que ordenar
        por el descendente da las ultimas usadas. Se desempata por `-password_history_id` porque dos
        archivados pueden compartir marca temporal al microsegundo: sin ese segundo criterio el orden
        no seria estable entre consultas y la ventana de «las N ultimas» cambiaria de contenido.

        El recorte `[:limite]` se aplica SOBRE EL QUERYSET para que Oracle lo traduzca a
        `OFFSET .. FETCH NEXT` y solo viajen las filas pedidas; trocear en Python traeria todo el
        historico del usuario a memoria para tirar la mayor parte, que es justo lo que no se hace con
        material criptografico (REQ-063). `values_list(..., flat=True)` ademas deja fuera de la
        consulta cualquier otra columna: lo que no se selecciona no puede filtrarse por error.

        El predicado y la ordenacion encajan exactamente con el indice `ix_usuario_pwd_hist_user_fecha`
        del DDL (`user_id`, `created_at DESC`).

        Args:
            user_id: identificador del usuario cuyo historico se consulta.
            limite: cuantas entradas se traen como maximo; por defecto las
                `MAXIMO_CONTRASENIAS_HISTORICAS` que fija la politica, el mismo numero que conserva
                el trigger de purga.

        Returns:
            Lista de `password_hash`, vacia si el usuario no tiene historico.
        """

        consulta = (
            self.modelo.objects.filter(user_id=user_id)
            .order_by("-created_at", "-password_history_id")
            .values_list("password_hash", flat=True)
        )
        return list(consulta[:limite])

    def archivar(self, user_id: int, password_hash: str) -> UsuarioPasswordHistoricoEntity:
        """
        Inserta en el historico el hash que DEJA de ser vigente (AC-PWD-04).

        Se llama con el `password_hash` ANTERIOR del usuario, en el mismo `transaction.atomic()` en
        el que se escribe el nuevo: asi la contrasenia sustituida se «desplaza al historial» y la
        ventana de no reutilizacion queda completa. Nunca se archiva la contrasenia entrante, que es
        la que pasa a ser vigente en la fila de `usuario`.

        `created_at` se sella con `utc_now()` -el mismo reloj UTC naive que el resto de marcas del
        servicio- porque esta columna no es la fecha de alta de la contrasenia sino el instante en
        que caduco.

        El historico NO se recorta aqui: el trigger de sentencia `trg_usuario_pwd_hist_purga` deja
        como maximo las 3 ultimas entradas por usuario despues de cada INSERT (REQ-069).

        Args:
            user_id: identificador del usuario al que pertenece la contrasenia archivada.
            password_hash: hash ya calculado por el servicio de hashing. Jamas una contrasenia en claro.

        Returns:
            La entidad insertada, con su `password_history_id` ya asignado.
        """

        return self.crear(user_id=user_id, password_hash=password_hash, created_at=utc_now())


__all__ = ["RepositorioHistoricoPassword"]
