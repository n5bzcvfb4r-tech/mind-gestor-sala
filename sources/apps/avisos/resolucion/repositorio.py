"""
Acceso a datos de la resolucion de destinatarios de aviso (REQ-089, REQ-140, ARC-120).

Dos repositorios con responsabilidades separadas: uno LEE el directorio de usuarios para saber a
quien se puede avisar, y otro ESCRIBE la traza inmutable de cada resolucion. Ninguno decide reglas
de negocio, abre transacciones, hace `commit` ni captura errores de base de datos: el modo degradado
(REQ-089) lo gobierna el servicio, que es quien conoce la politica. Si Oracle falla, la excepcion
sube intacta para que la decision se tome una sola vez y en un unico sitio.

Como el resto de repositorios del servicio, heredan de `RepositorioBase` y por tanto NO publican
ninguna operacion de borrado (REQ-047): aqui no hay nada que borrar, el log es append-only.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

from django.db import models

from apps.avisos.resolucion.resultados import (
    ESTADO_USUARIO_ACTIVO,
    ROL_EQUIPO_MANTENIMIENTO,
    ResultadoResolucion,
    TipoResolucion,
)
from apps.core.repositorios import RepositorioBase

if TYPE_CHECKING:  # pragma: no cover - solo para el tipado, evita importar modelos antes de django.setup()
    from apps.core.models import ResolucionDestinatarioLogEntity, UsuarioEntity


# Campos que este caso de uso necesita del usuario. Se enumeran a proposito para que la consulta NO
# traiga `password_hash` ni `password_salt`: es material criptografico (REQ-063) que no pinta en una
# resolucion de destinatarios, y lo que no se carga en memoria no se puede filtrar por error a un log.
CAMPOS_DESTINATARIO: tuple[str, ...] = ("user_id", "full_name", "corporate_email", "role_code", "status")

# Indicador booleano de Oracle: CHAR(1) con 'Y' o 'N'; el esquema no usa NUMBER(1) ni BOOLEAN.
INDICADOR_SI = "Y"
INDICADOR_NO = "N"

# Tope por defecto de `listar_recientes`: el recorte lo aplica Oracle, no Python.
LIMITE_RECIENTES_POR_DEFECTO = 50


class RepositorioDirectorioDestinatarios(RepositorioBase):
    """
    Lectura del directorio de usuarios para resolver destinatarios de aviso (REQ-089, REQ-140).

    SIN CACHE, y es deliberado: cada llamada ejecuta su propia consulta contra la base de datos.
    La composicion del equipo de mantenimiento y el estado de una cuenta cambian en caliente, y
    REQ-089 (RN-03) exige que una desactivacion surta efecto en el aviso SIGUIENTE. Una copia en
    memoria, por corta que fuese su vida, mandaria correos a personas ya dadas de baja.

    Por eso queda PROHIBIDO en este modulo cualquier tipo de memoizacion: `lru_cache`,
    `cached_property`, variables de modulo con resultados o atributos de instancia reutilizados
    entre llamadas. Si alguna consulta llega a doler, se resuelve con indices en la base, no con
    cache en el proceso.
    """

    def __init__(self, modelo: type[models.Model] | None = None) -> None:
        if modelo is None:
            from apps.core.models import UsuarioEntity

            modelo = UsuarioEntity
        super().__init__(modelo)

    def listar_equipo_mantenimiento(self) -> list[UsuarioEntity]:
        """
        Usuarios ACTIVOS del rol de mantenimiento, ordenados por `user_id` ascendente.

        Los dos predicados (rol y estado) viajan como WHERE a la consulta SQL; jamas se traen todos
        los usuarios para descartarlos despues en Python (REQ-089). Es a la vez correccion -el filtro
        lo evalua el motor sobre el dato vigente- y proteccion de datos: lo que no se selecciona no
        se materializa en memoria.

        El orden explicito por `user_id` hace la lista determinista, de modo que el JSON de
        identificadores que acaba en `resolucion_destinatario_log` sea reproducible entre ejecuciones.
        """

        return list(
            self.modelo.objects.filter(
                role_code_id=ROL_EQUIPO_MANTENIMIENTO,
                status=ESTADO_USUARIO_ACTIVO,
            )
            .only(*CAMPOS_DESTINATARIO)
            .order_by("user_id")
        )

    def obtener_usuario(self, user_id: int) -> UsuarioEntity | None:
        """
        Ficha de un usuario por clave primaria, o `None` si no existe.

        Devuelve la fila tal cual esta en la base, sin juzgar si es notificable: decidir si un
        destinatario puede recibir el aviso (REQ-140) es regla de negocio y vive en el servicio.
        Carga los mismos campos acotados que el listado, por la misma razon (REQ-063).
        """

        return self.modelo.objects.filter(pk=user_id).only(*CAMPOS_DESTINATARIO).first()


class RepositorioResolucionDestinatarioLog(RepositorioBase):
    """
    Escritura y lectura de la traza inmutable de resoluciones de destinatarios (ARC-120).

    La tabla es append-only: solo se inserta. No hay actualizacion ni borrado, y el propio modelo
    (`RegistroInmutableMixin`) y los triggers del DDL rechazan cualquier intento.
    """

    def __init__(self, modelo: type[models.Model] | None = None) -> None:
        if modelo is None:
            from apps.core.models import ResolucionDestinatarioLogEntity

            modelo = ResolucionDestinatarioLogEntity
        super().__init__(modelo)

    def registrar(
        self,
        *,
        request_type: TipoResolucion,
        requested_by_module: str,
        resolved_user_ids: Sequence[int],
        outcome: ResultadoResolucion,
        subject_user_id: int | None = None,
        is_fallback_used: bool = False,
        resolved_by_user_id: int | None = None,
    ) -> ResolucionDestinatarioLogEntity:
        """
        Inserta un asiento de resolucion y devuelve la entidad con su `resolution_id` ya asignado.

        `resolved_user_ids` se persiste como documento JSON de NUMEROS (`[1, 7, 12]`), nunca como
        lista de correos: en esa columna no se escribe jamas una direccion en claro (ARC-120,
        REQ-081). La traza guarda identificadores precisamente para poder auditar a quien se aviso
        sin convertir el log en un fichero de datos personales. El DDL lo blinda con `IS JSON
        (STRICT)` y con un CHECK sobre la columna virtual `resolved_ids_solo_numeros`, que rechaza
        cualquier elemento que no sea numero; por eso la conversion a `int` es explicita aqui y no
        una confianza en lo que llegue.

        `recipient_count` se calcula sobre la MISMA lista ya normalizada: otro CHECK del DDL lo
        compara con la cardinalidad real del JSON, asi que ambos valores cuadran por construccion
        y no por disciplina de quien llama.

        Dos columnas no se informan a proposito: `resolved_at`, que la sella el `default=utc_now`
        del modelo con el reloj del servidor, y las columnas VIRTUALES del DDL
        (`resolved_ids_cardinalidad`, `resolved_ids_solo_numeros`), que Oracle calcula y rechaza con
        ORA-54013 si una sentencia las menciona.
        """

        identificadores = [int(user_id) for user_id in resolved_user_ids]
        documento_json = json.dumps(identificadores)
        return self.crear(
            request_type=request_type.value,
            requested_by_module=requested_by_module,
            subject_user_id=subject_user_id,
            resolved_user_ids=documento_json,
            recipient_count=len(identificadores),
            is_fallback_used=INDICADOR_SI if is_fallback_used else INDICADOR_NO,
            outcome=outcome.value,
            resolved_by_user_id=resolved_by_user_id,
        )

    def listar_recientes(self, *, limite: int = LIMITE_RECIENTES_POR_DEFECTO) -> list[ResolucionDestinatarioLogEntity]:
        """
        Ultimas resoluciones registradas, de la mas reciente a la mas antigua.

        Desempata por `-resolution_id` porque varias resoluciones pueden compartir `resolved_at` al
        milisegundo: sin ese segundo criterio el orden no seria estable entre consultas.

        El recorte `[:limite]` se aplica sobre el queryset para que lo traduzca Oracle a
        `OFFSET .. FETCH NEXT` y solo viajen las filas pedidas; traer la tabla entera para cortarla
        en Python crece con el historico y acaba sin memoria.
        """

        return list(self.modelo.objects.order_by("-resolved_at", "-resolution_id")[:limite])


__all__ = [
    "CAMPOS_DESTINATARIO",
    "LIMITE_RECIENTES_POR_DEFECTO",
    "RepositorioDirectorioDestinatarios",
    "RepositorioResolucionDestinatarioLog",
]
