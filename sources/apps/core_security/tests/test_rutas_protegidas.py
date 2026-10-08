"""
Pruebas del guardia de sesion: PROTEGIDO POR DEFECTO y 401 UNIFORME.

Cubren la superficie HTTP sin tocar la base de datos: todas las denegaciones que se ejercitan
aqui se resuelven ANTES de consultar Oracle (ruta no exenta + credencial ausente o credencial
colada en la URL), de modo que la suite corre sin contenedor y sin `django_db`.

Lo que NO entra en este modulo, por necesitar sesiones reales en Oracle: caducidad absoluta,
caducidad por inactividad, revocacion y refresco de `last_activity_at`.
"""

import pytest
from django.test import Client
from django.urls import reverse

from apps.core_security import mensajes
from apps.core_security.middleware import PARAMETROS_CREDENCIAL_PROHIBIDOS
from apps.core_security.rutas_publicas import RUTAS_PUBLICAS, es_ruta_exenta


#: Ruta del contrato que SI existe en el URLconf y exige sesion.
RUTA_PROTEGIDA_EXISTENTE = "/api/incidents"

#: Ruta inventada que no existe en ningun URLconf: el guardia debe denegarla igual.
RUTA_INEXISTENTE = "/api/endpoint-nuevo-que-nadie-protegio"

#: Ruta publica del inicio de sesion (EP-001, REQ-058).
RUTA_LOGIN = "/api/auth/sessions"

CODIGO_SESION_INVALIDA = "AUTH_SESSION_INVALID"


@pytest.fixture
def cliente() -> Client:
    """Cliente HTTP sin credencial de sesion: toda peticion sale anonima."""

    return Client()


def test_AC_XFN_01_un_endpoint_nuevo_sin_declarar_como_publico_responde_401(cliente: Client) -> None:
    """
    [AC-XFN-01] Una ruta que ni siquiera existe en el URLconf responde 401, no 404.

    Es el oraculo de "protegido por defecto": un endpoint nuevo queda protegido aunque nadie
    se acuerde de configurarlo, y la denegacion no revela si el recurso existe.
    """

    respuesta = cliente.get(RUTA_INEXISTENTE)

    assert respuesta.status_code == 401
    assert respuesta.json()["code"] == CODIGO_SESION_INVALIDA


def test_AC_SES_04_una_peticion_sin_credencial_recibe_el_401_uniforme(cliente: Client) -> None:
    """[AC-SES-04] Sin cabecera `Authorization` la respuesta es el 401 canonico del servicio."""

    respuesta = cliente.get(RUTA_PROTEGIDA_EXISTENTE)

    assert respuesta.status_code == 401
    cuerpo = respuesta.json()
    assert cuerpo["code"] == CODIGO_SESION_INVALIDA
    assert cuerpo["message"] == mensajes.SESION_REQUERIDA
    assert cuerpo["details"] == []
    assert isinstance(cuerpo["traceId"], str)
    assert cuerpo["traceId"] != ""


def test_AC_SES_04_la_denegacion_es_identica_exista_o_no_el_recurso(cliente: Client) -> None:
    """
    [AC-SES-04] La denegacion es indistinguible entre un recurso que existe y uno que no.

    Mismo status, mismo `code` y mismo `message`; solo el `traceId` cambia, porque es el
    correlador de cada peticion y no informacion sobre el recurso.
    """

    respuesta_existente = cliente.get(RUTA_PROTEGIDA_EXISTENTE)
    respuesta_inventada = cliente.get(RUTA_INEXISTENTE)

    cuerpo_existente = respuesta_existente.json()
    cuerpo_inventada = respuesta_inventada.json()

    assert respuesta_existente.status_code == respuesta_inventada.status_code
    assert cuerpo_existente["code"] == cuerpo_inventada["code"]
    assert cuerpo_existente["message"] == cuerpo_inventada["message"]
    assert cuerpo_existente["traceId"] != cuerpo_inventada["traceId"]


@pytest.mark.parametrize("parametro", PARAMETROS_CREDENCIAL_PROHIBIDOS)
def test_REQ_056_la_credencial_de_sesion_no_se_acepta_en_la_url(cliente: Client, parametro: str) -> None:
    """
    [REQ-056] La credencial de sesion NO viaja en la URL: si llega como query param, se deniega.

    En la URL quedaria registrada en logs intermedios, en el historial del navegador y en la
    cabecera `Referer`; por eso se rechaza sin usarla, con el mismo 401 uniforme.
    """

    respuesta = cliente.get(RUTA_PROTEGIDA_EXISTENTE, {parametro: "lo-que-sea"})

    assert respuesta.status_code == 401
    cuerpo = respuesta.json()
    assert cuerpo["code"] == CODIGO_SESION_INVALIDA
    assert cuerpo["message"] == mensajes.SESION_REQUERIDA


def test_el_inicio_de_sesion_es_la_unica_ruta_publica_declarada() -> None:
    """
    [AC-XFN-01] `RUTAS_PUBLICAS` declara UNA sola ruta: `POST /api/auth/sessions` (EP-001, REQ-058).

    Esta prueba se pone roja el dia que alguien abra una segunda ruta publica, para que esa
    decision de seguridad sea explicita y revisada.
    """

    assert len(RUTAS_PUBLICAS) == 1
    assert RUTAS_PUBLICAS[0].path == RUTA_LOGIN
    assert RUTAS_PUBLICAS[0].metodos == ("POST",)

    assert es_ruta_exenta(RUTA_LOGIN, "POST") is True
    assert es_ruta_exenta(RUTA_LOGIN, "GET") is False
    assert es_ruta_exenta("/api/auth/sessions/current", "POST") is False
    assert es_ruta_exenta(RUTA_PROTEGIDA_EXISTENTE, "GET") is False


def test_la_ruta_de_login_esta_montada_en_el_composition_root(cliente: Client) -> None:
    """
    [AC-XFN-01] EP-001 se sirve EXACTAMENTE en `/api/auth/sessions` y es alcanzable sin sesion.

    El path literal sale del contrato (`servers[0].url` = `/api` mas el path del recurso). Del
    cuerpo invalido solo se afirma que NO es 401 ni 404: la envolvente de los errores de
    validacion la fija otro encargo.
    """

    assert reverse("core_security:auth-sessions") == RUTA_LOGIN

    respuesta = cliente.post(RUTA_LOGIN, data={}, content_type="application/json")

    assert respuesta.status_code not in (401, 404)
