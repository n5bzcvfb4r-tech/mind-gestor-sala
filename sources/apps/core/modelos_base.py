"""
Mixins transversales de persistencia: sin borrado fisico, inmutabilidad y atribucion.

Estos mixins son el sitio donde se hacen cumplir, a nivel de ORM, tres invariantes del
modelo de datos que ninguna capa superior puede saltarse por descuido:

* las bajas son logicas, nunca `DELETE` (REQ-047);
* los historicos y la auditoria son append-only (REQ-015, REQ-065);
* la atribucion (quien y cuando) sale del contexto de sesion (REQ-048, REQ-064).
"""

from typing import Any

from django.db import models

from apps.core.contexto import contexto_requerido, utc_now
from apps.core.errores import BorradoFisicoNoPermitidoError, RegistroInmutableError


class SinBorradoFisicoQuerySet(models.QuerySet):
    """QuerySet que rechaza el borrado fisico masivo."""

    def delete(self) -> None:
        raise BorradoFisicoNoPermitidoError()


class SinBorradoFisicoManager(models.Manager.from_queryset(SinBorradoFisicoQuerySet)):
    """Manager por defecto de las entidades sin borrado fisico."""

    pass


class SinBorradoFisicoMixin(models.Model):
    """
    Prohibe el `DELETE` sobre la entidad, tanto por instancia como por queryset.

    Las bajas del sistema son LOGICAS (REQ-047): se marca el estado y se registra el motivo,
    la fecha y el actor, pero la fila permanece para conservar la trazabilidad historica.
    """

    objects = SinBorradoFisicoManager()

    class Meta:
        abstract = True

    def delete(self, *args: Any, **kwargs: Any) -> None:
        raise BorradoFisicoNoPermitidoError()


class RegistroInmutableMixin(SinBorradoFisicoMixin):
    """
    Entidad append-only: admite el INSERT y rechaza cualquier UPDATE o DELETE posterior.

    Aplica a historicos de incidencia y a la auditoria (REQ-015, REQ-065): una vez escrita,
    la entrada es evidencia y no se reescribe.
    """

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None and not self._state.adding:
            raise RegistroInmutableError()
        super().save(*args, **kwargs)


class AtribucionMixin(models.Model):
    """
    Rellena la atribucion (actor y marca temporal) de alta y modificacion.

    El `user_id` que llegue en el payload se IGNORA; la atribucion sale SIEMPRE del contexto
    de sesion (REQ-048, REQ-064, AC-TRZ-01). Lo mismo con las fechas: se toman de `utc_now()`
    y nunca de datos de entrada, para que un cliente no pueda antedatar ni suplantar.

    El modelo concreto puede renombrar las columnas mediante los atributos de clase
    `CAMPO_ACTOR_ALTA`, `CAMPO_FECHA_ALTA`, `CAMPO_ACTOR_MODIFICACION` y `CAMPO_FECHA_MODIFICACION`.
    Solo se escriben los campos que el modelo declare realmente.
    """

    CAMPO_ACTOR_ALTA: str = "created_by"
    CAMPO_FECHA_ALTA: str = "created_at"
    CAMPO_ACTOR_MODIFICACION: str = "updated_by"
    CAMPO_FECHA_MODIFICACION: str = "updated_at"

    class Meta:
        abstract = True

    def _nombres_de_campo(self) -> set[str]:
        """Nombres de atributo persistidos por el modelo (incluye `attname`, p.ej. `created_by_id`)."""

        nombres: set[str] = set()
        for campo in self._meta.fields:
            nombres.add(campo.name)
            nombres.add(campo.attname)
        return nombres

    def _fijar_campo(self, nombre: str, valor: Any, nombres: set[str]) -> None:
        """Asigna el valor al campo indicado, aceptando tanto la clave foranea como su `_id`."""

        if f"{nombre}_id" in nombres:
            setattr(self, f"{nombre}_id", valor)
        elif nombre in nombres:
            setattr(self, nombre, valor)

    def _valor_actual(self, nombre: str, nombres: set[str]) -> Any:
        if f"{nombre}_id" in nombres:
            return getattr(self, f"{nombre}_id", None)
        if nombre in nombres:
            return getattr(self, nombre, None)
        return None

    def aplicar_atribucion(self, *, es_alta: bool | None = None) -> None:
        """
        Sobrescribe los campos de atribucion con el actor del contexto y la hora del servidor.

        La atribucion sale SIEMPRE del contexto de sesion (REQ-048, REQ-064) y NUNCA del
        payload: el `user_id` que llegue en los datos de entrada se ignora, y las marcas
        temporales se toman de `utc_now()`.

        En ALTA:

        * si el modelo declara el campo de actor de alta, se fija con el actor de sesion;
        * si NO lo declara pero si declara el de actor de modificacion, se fija ESTE ultimo
          con el actor de sesion: esa columna es la unica atribucion que la tabla tiene y en
          un alta debe quedar sellada (hay tablas, como `configuracion_smtp`, cuyo
          `updated_by` es NOT NULL y que no tienen `created_by`, asi que dejarlo vacio
          reventaria el INSERT);
        * si el campo de actor de modificacion esta declarado y ya venia informado, se
          refresca igualmente.

        La misma regla, termino a termino, se aplica a la pareja de fechas (alta y
        modificacion). Los campos que el modelo no declara se ignoran sin error.

        En MODIFICACION se informan actor y fecha de modificacion, y NO se tocan los de alta.
        """

        contexto = contexto_requerido()
        actor = contexto.user_id
        ahora = utc_now()
        nombres = self._nombres_de_campo()
        alta = self._state.adding if es_alta is None else es_alta

        if alta:
            self._fijar_campo(self.CAMPO_ACTOR_ALTA, actor, nombres)
            self._fijar_campo(self.CAMPO_FECHA_ALTA, ahora, nombres)
            sin_actor_de_alta = not {self.CAMPO_ACTOR_ALTA, f"{self.CAMPO_ACTOR_ALTA}_id"} & nombres
            sin_fecha_de_alta = not {self.CAMPO_FECHA_ALTA, f"{self.CAMPO_FECHA_ALTA}_id"} & nombres
            if sin_actor_de_alta or self._valor_actual(self.CAMPO_ACTOR_MODIFICACION, nombres) is not None:
                self._fijar_campo(self.CAMPO_ACTOR_MODIFICACION, actor, nombres)
            if sin_fecha_de_alta or self._valor_actual(self.CAMPO_FECHA_MODIFICACION, nombres) is not None:
                self._fijar_campo(self.CAMPO_FECHA_MODIFICACION, ahora, nombres)
            return

        self._fijar_campo(self.CAMPO_ACTOR_MODIFICACION, actor, nombres)
        self._fijar_campo(self.CAMPO_FECHA_MODIFICACION, ahora, nombres)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.aplicar_atribucion()
        super().save(*args, **kwargs)
