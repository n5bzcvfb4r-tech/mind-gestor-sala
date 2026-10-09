"""
Servicio de consulta del estado de credencial de los usuarios (EP-019, REQ-074).

QUE RESUELVE
------------
Es la capa que convierte filas del ORM en una PROYECCION cerrada y publicable. La vista no recibe
`UsuarioEntity`: recibe `EstadoCredencialUsuario`, que solo tiene los diez campos de datos de
REQ-074. Esa frontera es la que garantiza AC-RST-04 por construccion y no por disciplina: aunque un
serializer futuro se escribiera con `fields = "__all__"`, no habria ningun `password_hash` que
publicar porque el objeto que llega no lo lleva.

UN SOLO INSTANTE PARA TODA LA OPERACION
---------------------------------------
`listar` sella `ahora` UNA vez con `utc_now()` y ese mismo valor viaja al filtro SQL de bloqueo y al
calculo del campo `locked` de cada fila. Si la proyeccion volviera a leer el reloj, una cuenta cuyo
bloqueo vence entre ambas lecturas podria salir en el filtro «bloqueado» publicada como `locked:
false`: la respuesta se contradiria a si misma.

CONVERSIONES EN LA FRONTERA, NO EN EL DOMINIO
---------------------------------------------
`must_change_password` es CHAR(1) 'Y'/'N' en Oracle. La traduccion a `bool` se hace aqui, una sola
vez, al proyectar: ni el repositorio ni la vista vuelven a razonar sobre el literal.

TRAZAS (REQ-063, REQ-076, REQ-079)
----------------------------------
La traza de este servicio lleva SOLO contadores y los filtros expresados como booleanos. No se
registra `corporate_email`, ni nombres, ni identificadores de usuario listados, ni por supuesto
material de credencial: el listado de estado de credencial es informacion sensible y volcarlo al log
lo duplicaria en un soporte sin control de acceso.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from apps.core.contexto import utc_now
from apps.identidad.reposicion.repositorio import (
    INDICADOR_SI,
    TAMANIO_PAGINA,
    CriteriosEstadoCredencial,
    RepositorioEstadoCredencial,
)


if TYPE_CHECKING:  # pragma: no cover - solo tipado: evita importar modelos antes de django.setup()
    from apps.core.models import UsuarioEntity


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EstadoCredencialUsuario:
    """
    Estado de credencial publicable de UN usuario (REQ-074, campos de datos).

    Es la proyeccion EXACTA de los campos de datos de REQ-074 y NADA MAS. No lleva `password_hash`,
    `password_salt` ni `password_algorithm`, y por eso un hash de contrasenia no puede llegar a la
    respuesta ni por descuido: el test de AC-RST-04 exige 0 ocurrencias de `password_hash` y aqui no
    hay atributo donde pudiera alojarse. Anadir uno seria el unico modo de romper esa garantia, y
    quedaria a la vista en el diff.

    Los nombres son los FISICOS del esquema T.5 (`full_name`, `corporate_email`, `role_code`,
    `password_updated_at`, `must_change_password`, `locked_until`, `last_login_at`) y NO se traducen:
    un renombrado cosmetico obligaria a mantener un diccionario de equivalencias entre la respuesta
    HTTP, el modelo y el DDL, y cualquier desincronizacion seria silenciosa.

    `must_change_password` y `locked` ya son `bool`: la conversion desde el CHAR(1) 'Y'/'N' y la
    comparacion de `locked_until` contra el instante de la consulta se hacen al construir el objeto.
    """

    user_id: int
    full_name: str
    corporate_email: str
    role_code: str
    status: str
    password_updated_at: datetime | None
    must_change_password: bool
    locked: bool
    locked_until: datetime | None
    last_login_at: datetime | None


@dataclass(frozen=True, slots=True)
class PaginaEstadoCredencial:
    """
    Una pagina del listado con su contexto de paginacion (REQ-074 regla 2).

    `total_count` es el numero de usuarios que cumplen los criterios en la CONSULTA FILTRADA
    COMPLETA, no los de esta pagina: es el dato con el que el cliente decide cuantas paginas hay y
    si tiene sentido pedir la siguiente. `len(items)` responde a otra pregunta -cuantos ha recibido
    ahora- y vale como mucho `page_size`.

    `items` es una tupla y no una lista: la pagina es un resultado ya cerrado y nadie debe poder
    anadirle filas despues de que el recuento se haya calculado.
    """

    items: tuple[EstadoCredencialUsuario, ...]
    page: int
    page_size: int
    total_count: int
    total_pages: int

    @classmethod
    def de(
        cls,
        *,
        items: tuple[EstadoCredencialUsuario, ...],
        page: int,
        page_size: int,
        total_count: int,
    ) -> PaginaEstadoCredencial:
        """
        Construye la pagina derivando `total_pages` del total y del tamanio.

        La division es ENTERA HACIA ARRIBA (`-(-total // size)`): con 26 coincidencias y paginas de
        25 hay 2 paginas, no 1. Una division truncada dejaria la ultima pagina fuera del paginador y
        los usuarios de la cola serian invisibles aunque la consulta los encuentre.

        Sin coincidencias el resultado es 0 paginas y no 1: una pagina vacia que el cliente pudiera
        pedir no aporta nada, y `total_pages = 0` deja claro que la busqueda no encontro a nadie.
        `page_size` no positivo se trata igual, para no dividir por cero ante un tamanio absurdo.
        """

        total_pages = -(-total_count // page_size) if total_count > 0 and page_size > 0 else 0
        return cls(items=items, page=page, page_size=page_size, total_count=total_count, total_pages=total_pages)


class ServicioEstadoCredencial:
    """
    Caso de uso de lectura del estado de credencial de los usuarios (EP-019, REQ-074).

    Una sola operacion publica, `listar`. No escribe nada, no abre transaccion -no hay nada que
    confirmar en una lectura- y no emite auditoria de negocio: consultar un listado no es un evento
    que cambie el estado del sistema.

    El repositorio se recibe por constructor y se resuelve de forma PEREZOSA, igual que hace
    `ServicioCustodiaCredenciales`: construir el repositorio en `__init__` obligaria a tener el
    registro de aplicaciones cargado solo para instanciar el servicio, y una prueba puede inyectar
    su propio doble sin tocar `settings`.
    """

    def __init__(self, repositorio: RepositorioEstadoCredencial | None = None) -> None:
        self._repositorio = repositorio

    @property
    def _repo(self) -> RepositorioEstadoCredencial:
        """Repositorio de la consulta, instanciado en su primer uso (los modelos exigen `django.setup()`)."""

        if self._repositorio is None:
            self._repositorio = RepositorioEstadoCredencial()
        return self._repositorio

    def listar(self, criterios: CriteriosEstadoCredencial) -> PaginaEstadoCredencial:
        """
        Devuelve la pagina de estado de credencial que corresponde a los criterios (REQ-074).

        Sella el instante UNA sola vez y lo usa para las dos cosas que dependen de el: el filtro de
        bloqueos vigentes en la consulta y el campo `locked` de cada fila. Ver la nota del modulo
        sobre por que no puede haber dos lecturas del reloj.

        El filtrado, el orden, el recuento y el troceo los hace integramente la base de datos; aqui
        solo se proyecta lo que llega. Una pagina mas alla del total devuelve `items` vacio, que no
        es un error: la consulta es valida y simplemente no quedan filas.

        Args:
            criterios: filtros, texto de busqueda y pagina pedida, ya validados por la vista.

        Returns:
            La pagina con las filas proyectadas y el contexto de paginacion.
        """

        ahora = utc_now()
        pagina = max(1, criterios.pagina)

        usuarios, total = self._repo.buscar(criterios, ahora=ahora)
        items = tuple(self._proyectar(usuario, ahora=ahora) for usuario in usuarios)

        # SOLO contadores y filtros como booleanos: ni correos, ni nombres, ni identificadores.
        logger.info(
            "Listado de estado de credencial resuelto",
            extra={
                "total": total,
                "pagina": pagina,
                "devueltos": len(items),
                "filtro_rol": bool(criterios.role_code),
                "filtro_bloqueo": criterios.estado_bloqueo is not None,
                "filtro_cambio_pendiente": criterios.must_change_password is not None,
                "filtro_busqueda": bool(criterios.texto),
            },
        )

        return PaginaEstadoCredencial.de(items=items, page=pagina, page_size=TAMANIO_PAGINA, total_count=total)

    def _proyectar(self, usuario: UsuarioEntity, *, ahora: datetime) -> EstadoCredencialUsuario:
        """
        Convierte la fila del ORM en la proyeccion publicable.

        Copia directa salvo tres casos que exigen decision:

        * `role_code` toma `role_code_id`, el CODIGO literal de la columna, no el objeto del
          ForeignKey: lo que publica la API es el codigo del rol, y pasar la entidad obligaria al
          serializer a conocer el modelo relacionado.
        * `must_change_password` traduce el CHAR(1) 'Y'/'N' a `bool` aqui, en la frontera, para que
          ninguna capa posterior tenga que recordar que el valor real es una letra.
        * `locked` es `locked_until` EN EL FUTURO respecto al mismo `ahora` que filtro la consulta.
          Una marca vencida significa que el bloqueo ya expiro y la cuenta esta operativa, asi que
          publicar `locked = true` solo porque la columna tiene valor seria un dato falso.
        """

        return EstadoCredencialUsuario(
            user_id=usuario.user_id,
            full_name=usuario.full_name,
            corporate_email=usuario.corporate_email,
            role_code=usuario.role_code_id,
            status=usuario.status,
            password_updated_at=usuario.password_updated_at,
            must_change_password=usuario.must_change_password == INDICADOR_SI,
            locked=usuario.locked_until is not None and usuario.locked_until > ahora,
            locked_until=usuario.locked_until,
            last_login_at=usuario.last_login_at,
        )


__all__ = [
    "EstadoCredencialUsuario",
    "PaginaEstadoCredencial",
    "ServicioEstadoCredencial",
]
