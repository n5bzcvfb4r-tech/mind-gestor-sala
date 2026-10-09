"""
Servicio de consulta del censo de usuarios (EP-008, REQ-006, REQ-039).

QUE RESUELVE
------------
Es la capa que convierte filas del ORM en una PROYECCION cerrada y publicable. La vista no recibe
`UsuarioEntity`: recibe `UsuarioDelCenso`, que solo lleva los campos que el contrato publica. Esa
frontera es la que garantiza por CONSTRUCCION que la respuesta no contiene credenciales, y no por
disciplina: aunque un serializador futuro se escribiera con `fields = "__all__"`, no habria ningun
`password_hash` que publicar porque el objeto que llega no lo lleva.

NO HAY LOGICA DE FILTRADO AQUI
------------------------------
Filtro, busqueda, orden, recuento y troceo los hace integramente la base de datos. Este servicio
pide, proyecta y envuelve en una pagina. Cualquier `if` que decidiera aqui si una fila entra o no
en el resultado estaria contradiciendo al `total_count` que la propia consulta ha calculado, y el
paginador publicaria un numero de paginas que no corresponde con lo que luego se devuelve.

CERO COINCIDENCIAS NO ES UN ERROR (AC-USR-05)
----------------------------------------------
Una busqueda que no encuentra a nadie es una consulta CORRECTA: responde 200 con `items` vacio y un
literal informativo en `empty_message`. Lanzar una excepcion obligaria a la vista a distinguir
«fallo» de «no hay nadie», y el cliente tendria que tratar como error algo que no lo es.

TRAZAS (REQ-063, REQ-079)
-------------------------
La traza de este servicio lleva SOLO contadores e indicadores de paginacion. No se registra
`corporate_email`, ni nombres, ni los identificadores de los usuarios listados, ni por supuesto
material de credencial: el censo es informacion personal y volcarlo al log lo duplicaria en un
soporte sin control de acceso. Ni siquiera se registra el TEXTO buscado, que puede ser el correo o
el nombre de una persona concreta.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from apps.usuarios.consulta import mensajes
from apps.usuarios.consulta.repositorio import TAMANIO_PAGINA_MAXIMO, CriteriosCenso, RepositorioCensoUsuarios


if TYPE_CHECKING:  # pragma: no cover - solo tipado: evita importar modelos antes de django.setup()
    from apps.core.models import UsuarioEntity


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UsuarioDelCenso:
    """
    Datos publicables de UN usuario del censo (EP-008, REQ-006).

    NO HAY NINGUN ATRIBUTO DE CREDENCIAL Y NO PUEDE HABERLO. `password_hash`, `password_salt` y
    `password_algorithm` no estan declarados aqui, y `slots=True` cierra la clase a atributos
    nuevos: ni siquiera un `setattr` al vuelo podria colarlos. La ausencia de material de credencial
    en la respuesta es por tanto ESTRUCTURAL y no una omision que haya que recordar en cada revision
    (REQ-063, regla de negocio de REQ-006). Publicar uno exigiria anadirlo antes a esta proyeccion,
    lo que quedaria a la vista en el diff.

    Los nombres son los FISICOS del esquema T.5 (`full_name`, `corporate_email`, `role_code`,
    `created_at`, `last_login_at`, `role_changed_at`) y NO se traducen: un renombrado cosmetico
    obligaria a mantener un diccionario de equivalencias entre la respuesta HTTP, el modelo y el
    DDL, y cualquier desincronizacion seria silenciosa. El camelCase lo pone el serializador.

    `role_code` es el CODIGO literal del rol y `role_name` su etiqueta legible, que puede faltar si
    la fila de catalogo no se pudo resolver: `null` es el dato honesto, inventar un nombre no.

    `last_login_at` y `role_changed_at` admiten nulo porque un usuario recien dado de alta no ha
    entrado todavia ni se le ha cambiado nunca el rol. `created_at`, en cambio, siempre existe: toda
    fila del censo tiene fecha de alta.
    """

    user_id: int
    full_name: str
    corporate_email: str
    role_code: str
    role_name: str | None
    status: str
    created_at: datetime
    last_login_at: datetime | None
    role_changed_at: datetime | None


@dataclass(frozen=True, slots=True)
class PaginaCenso:
    """
    Una pagina del censo con su contexto de paginacion (REQ-039).

    `total_count` es el numero de usuarios que cumplen los criterios en la CONSULTA FILTRADA
    COMPLETA, no los de esta pagina: es el dato con el que el cliente decide cuantas paginas hay y
    si tiene sentido pedir la siguiente. `len(items)` responde a otra pregunta -cuantos ha recibido
    ahora- y vale como mucho `page_size`.

    `items` es una tupla y no una lista: la pagina es un resultado ya cerrado y nadie debe poder
    anadirle filas despues de que el recuento se haya calculado.

    `empty_message` solo esta informado cuando la pagina no trae filas. Es un literal INFORMATIVO
    que acompania a un 200 correcto, nunca un error (AC-USR-05).
    """

    items: tuple[UsuarioDelCenso, ...]
    page: int
    page_size: int
    total_count: int
    total_pages: int
    empty_message: str | None

    @classmethod
    def de(
        cls,
        *,
        items: tuple[UsuarioDelCenso, ...],
        page: int,
        page_size: int,
        total_count: int,
    ) -> PaginaCenso:
        """
        Construye la pagina derivando `total_pages` y el literal de listado vacio.

        La division es ENTERA HACIA ARRIBA (`-(-total // size)`): con 26 coincidencias y paginas de
        25 hay 2 paginas, no 1. Una division truncada dejaria la ultima pagina fuera del paginador y
        los usuarios de la cola serian invisibles aunque la consulta los encuentre.

        Sin coincidencias el resultado es 0 paginas y no 1: una pagina vacia que el cliente pudiera
        pedir no aporta nada, y `total_pages = 0` deja claro que la busqueda no encontro a nadie.
        `page_size` no positivo se trata igual, para no dividir por cero ante un tamanio absurdo.

        EL LITERAL SE DECIDE POR `items`, NO POR `total_count`. Son dos situaciones distintas que
        merecen el mismo aviso: que no haya nadie que cumpla los filtros, y que si lo haya pero la
        pagina pedida este mas alla del final. En ambos casos quien consulta recibe una respuesta
        vacia y necesita que se le diga por que no ve nada, en vez de quedarse mirando un hueco.
        """

        total_pages = -(-total_count // page_size) if total_count > 0 and page_size > 0 else 0
        return cls(
            items=items,
            page=page,
            page_size=page_size,
            total_count=total_count,
            total_pages=total_pages,
            empty_message=mensajes.SIN_RESULTADOS if not items else None,
        )


class ServicioCensoUsuarios:
    """
    Caso de uso de lectura del censo de usuarios (EP-008, REQ-006, REQ-039).

    Una sola operacion publica, `listar`. No escribe nada, no abre transaccion -no hay nada que
    confirmar en una lectura- y no emite auditoria de negocio: consultar un listado no es un evento
    que cambie el estado del sistema.

    El repositorio se recibe por constructor y se resuelve de forma PEREZOSA, igual que hace
    `ServicioEstadoCredencial`: construirlo en `__init__` obligaria a tener el registro de
    aplicaciones cargado solo para instanciar el servicio, y una prueba puede inyectar su propio
    doble sin tocar `settings`.
    """

    def __init__(self, repositorio: RepositorioCensoUsuarios | None = None) -> None:
        self._repositorio = repositorio

    @property
    def _repo(self) -> RepositorioCensoUsuarios:
        """Repositorio de la consulta, instanciado en su primer uso (los modelos exigen `django.setup()`)."""

        if self._repositorio is None:
            self._repositorio = RepositorioCensoUsuarios()
        return self._repositorio

    def listar(self, criterios: CriteriosCenso) -> PaginaCenso:
        """
        Devuelve la pagina del censo que corresponde a los criterios (REQ-006, REQ-039).

        El filtrado, el orden, el recuento y el troceo los hace integramente la base de datos; aqui
        solo se proyecta lo que llega. Una pagina mas alla del total devuelve `items` vacio, que no
        es un error: la consulta es valida y simplemente no quedan filas.

        `page` y `page_size` se publican ACOTADOS con las mismas reglas que aplico el repositorio
        -minimo 1 y tope `TAMANIO_PAGINA_MAXIMO`-, para que el contexto de paginacion describa la
        consulta que de verdad se ejecuto y no la que se pidio.

        Args:
            criterios: filtros, texto de busqueda y paginacion, ya validados por la vista.

        Returns:
            La pagina con las filas proyectadas, el contexto de paginacion y, si no hay filas, el
            literal informativo de listado vacio.
        """

        tamanio = min(max(1, criterios.tamanio_pagina), TAMANIO_PAGINA_MAXIMO)
        pagina = max(1, criterios.pagina)

        usuarios, total = self._repo.buscar(criterios)
        items = tuple(self._proyectar(usuario) for usuario in usuarios)

        # SOLO contadores e indicadores: ni correos, ni nombres, ni identificadores, ni el texto
        # buscado (REQ-079). Los filtros se registran como booleanos de «se aplico o no».
        logger.info(
            "Censo de usuarios consultado",
            extra={
                "total_count": total,
                "page": pagina,
                "page_size": tamanio,
                "devueltos": len(items),
                "filtro_rol": bool(criterios.role_code),
                "filtro_estado": criterios.status is not None,
                "filtro_busqueda": bool(criterios.texto),
                "outcome": "OK",
            },
        )

        return PaginaCenso.de(items=items, page=pagina, page_size=tamanio, total_count=total)

    def _proyectar(self, usuario: UsuarioEntity) -> UsuarioDelCenso:
        """
        Convierte la fila del ORM en la proyeccion publicable.

        Copia directa salvo el rol, que exige decision:

        * `role_code` toma `role_code_id`, el CODIGO literal de la columna, y no el objeto del
          ForeignKey: lo que publica la API es el codigo, y pasar la entidad obligaria al
          serializador a conocer el modelo relacionado.
        * `role_name` se lee con `getattr` sobre la entidad relacionada, que el `select_related` de
          la consulta ya trajo en el mismo SELECT. El valor por defecto `None` cubre el caso de una
          fila huerfana sin romper el listado entero: un censo que no se puede consultar porque a un
          usuario le falta la etiqueta de su rol seria un remedio peor que la enfermedad.

        NINGUNA COLUMNA DE CREDENCIAL SE COPIA AQUI, y el dataclass de destino no tiene donde
        alojarla: ver su docstring.
        """

        return UsuarioDelCenso(
            user_id=usuario.user_id,
            full_name=usuario.full_name,
            corporate_email=usuario.corporate_email,
            role_code=usuario.role_code_id,
            role_name=getattr(usuario.role_code, "role_name", None),
            status=usuario.status,
            created_at=usuario.created_at,
            last_login_at=usuario.last_login_at,
            role_changed_at=usuario.role_changed_at,
        )


__all__ = [
    "PaginaCenso",
    "ServicioCensoUsuarios",
    "UsuarioDelCenso",
]
