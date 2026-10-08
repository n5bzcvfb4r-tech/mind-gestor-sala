import json
import os
from pathlib import Path


def evaluate_bool_default(varname: str, default: str) -> bool:
    """
    Evaluates whether the value of an environment variable is considered truthy, with a configurable default.

    The function interprets common false-like values such as:
    "", "0", "false", "no", "off", "none", "[]", "()" (case-insensitive, stripped).

    Additionally, if the value is numeric, it is considered falsy if equal to zero.

    Args:
        varname (str): The name of the environment variable.
        default (str): The raw value used when the environment variable is not defined.

    Returns:
        bool: True if the variable is considered truthy, False otherwise.
    """

    value = os.environ.get(varname, default).strip().lower()
    if value in {"", "0", "false", "f", "no", "n", "off", "none", "[]", "()"}:
        return False
    try:
        return float(value) != 0
    except ValueError:
        return True


def evaluate_bool(varname: str) -> bool:
    """
    Evaluates whether the value of an environment variable is considered truthy.

    The function interprets common false-like values such as:
    "", "0", "false", "no", "off", "none", "[]", "()" (case-insensitive, stripped).

    Additionally, if the value is numeric, it is considered falsy if equal to zero.

    Args:
        varname (str): The name of the environment variable.

    Returns:
        bool: True if the variable is considered truthy, False otherwise.
    """

    return evaluate_bool_default(varname, "False")


def evaluate_dict(varname: str) -> dict:
    """
    Evaluates a dictionary from a string environment variable.

    The function attempts to parse the value of the environment variable as JSON.
    If parsing fails, it returns an empty dictionary.

    Args:
        varname (str): The name of the environment variable.
    Returns:
        dict: The parsed dictionary, or an empty dictionary if parsing fails.
    """

    value = os.environ.get(varname, "{}").strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


# GENERAL CONFIGURATION
# -------------------------------------------------------------
# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# NAMING CONFIGURATION
# -------------------------------------------------------------
ENVIRONMENT = os.environ.get("ENVIRONMENT")
SERVICE_NAME = os.environ.get("SERVICE_NAME")
APPLICATION_NAME = os.environ.get("APPLICATION_NAME")

LOCAL_ENVIRONMENT = (ENVIRONMENT or "local").lower() == "local"

# SECURITY CONFIGURATION
# -------------------------------------------------------------
# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get("SECRET_KEY", "INSECURE")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = evaluate_bool("DEBUG")

ALLOWED_HOSTS = ["*"]

# APPLICATION CONFIGURATION
# -------------------------------------------------------------
DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    # API
    "rest_framework",
    # Code First
    "drf_spectacular",
    "drf_spectacular_sidecar",
]

LOCAL_APPS = [
    "apps.core",
    "apps.core_security",
    "apps.identidad",
    "apps.usuarios",
    "apps.catalogos",
    "apps.incidencias",
    "apps.avisos",
    "apps.ciclo_vida",
    "apps.trazabilidad",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Guardia de sesion (ARC-012): protegido por defecto. Va ANTES del publicador de
    # contexto porque es quien resuelve la sesion y deja `request.contexto_sesion`.
    "apps.core_security.middleware.SesionRequeridaMiddleware",
    "apps.core.middleware.ContextoSesionMiddleware",
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Password validation
# https://docs.djangoproject.com/en/5.1/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# SECURITY AUTHENTICATION CONFIG
# -------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",  # "rest_framework.schemas.coreapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    # El proyecto usa sesion opaca en servidor (tabla sesion_usuario), NUNCA JWT autocontenido:
    # el rol vigente se relee de la base en cada peticion y la revocacion es inmediata (REQ-057).
    "DEFAULT_AUTHENTICATION_CLASSES": ("apps.core_security.autenticacion.AutenticacionSesionOpaca",),
    # Envolvente UNICA de error: todo fallo atendido por DRF sale con el mismo cuerpo
    # (`code`, `message`, `details`, `traceId`) que devuelve el guardia de sesion.
    "EXCEPTION_HANDLER": "apps.core_security.manejadores.manejador_excepciones",
}

# SESION DE USUARIO (ARC-012)
# -------------------------------------------------------------
# Sesion OPACA server-side: el identificador uuid de `sesion_usuario` viaja en la cabecera
# `Authorization: Bearer <session_id>` (nunca en la URL, REQ-056) y todo su estado vive en
# Oracle. Las dos ventanas de vigencia son independientes y parametrizables por entorno.
SESION_INACTIVIDAD_MINUTOS = int(os.environ.get("SESION_INACTIVIDAD_MINUTOS", "30"))
SESION_VIGENCIA_ABSOLUTA_HORAS = int(os.environ.get("SESION_VIGENCIA_ABSOLUTA_HORAS", "12"))

# Argon2id es el algoritmo adaptativo del proyecto y el unico que admite el CHECK
# `ck_usuario_pwd_algorithm` junto con bcrypt. La contrasenia se guarda SOLO como hash.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

# DATABASE CONFIGURATION
# -------------------------------------------------------------
# Motor del proyecto: Oracle 23ai (driver python-oracledb). Todo parametrizado por entorno,
# con las variables del entorno de prueba de la plataforma como fallback.
DB_HOST = os.environ.get("DB_HOST", os.environ.get("MIND_ENV_ORACLE_HOST", "localhost"))
DB_PORT = os.environ.get("DB_PORT", os.environ.get("MIND_ENV_ORACLE_PORT", "1521"))
DB_SERVICE_NAME = os.environ.get("DB_SERVICE_NAME", os.environ.get("MIND_ENV_ORACLE_SERVICE_NAME", "FREEPDB1"))
DB_USER = os.environ.get("DB_USER", os.environ.get("MIND_ENV_ORACLE_USER", "facilities"))
DB_PASSWORD = os.environ.get("DB_PASSWORD", os.environ.get("MIND_ENV_ORACLE_PASSWORD", "entornodev"))

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.oracle",
        "NAME": f"{DB_HOST}:{DB_PORT}/{DB_SERVICE_NAME}",
        "USER": DB_USER,
        "PASSWORD": DB_PASSWORD,
        # python-oracledb en modo thin: sin Instant Client y sin opciones de cx_Oracle.
        "OPTIONS": {},
        "TEST": {"CREATE_DB": False, "USER": DB_USER, "PASSWORD": DB_PASSWORD},
    }
}

# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# CACHE
# -------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": "/var/tmp/django_cache",
        "TIMEOUT": os.environ.get("CACHE_DEFAULT_TIMEOUT", 300),
    }
}
CACHE_OAUTH_TTL = os.environ.get("CACHE_OAUTH_TTL", 60 * 60)  # 1h

# INTERNATIONALIZATION CONFIGURATION
# -------------------------------------------------------------
# Servicio en espanol (NFR/REQ-050). USE_TZ desactivado: el proyecto maneja SIEMPRE
# datetime naive en UTC; el datetime aware esta prohibido por la matriz de tipos.
LANGUAGE_CODE = "es-es"
TIME_ZONE = "UTC"
USE_I18N = True
USE_L10N = True
USE_TZ = False

# MEDIA & STATIC CONFIGURATION
# -------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = os.path.join(BASE_DIR, STATIC_URL)

MEDIA_URL = "media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

# CODE FIRST
# -------------------------------------------------------------
SPECTACULAR_SETTINGS = {
    'TITLE': 'incidencias_project — API',
    'DESCRIPTION': 'Servicio de gestion de incidencias de salas',
    'VERSION': '0.1.0',
    'SERVERS': [{'url': '/api', 'description': 'Base publica de la API'}],
    'SWAGGER_UI_DIST': 'SIDECAR',  # shorthand to use the sidecar instead
    'SWAGGER_UI_FAVICON_HREF': 'SIDECAR',
    'REDOC_DIST': 'SIDECAR',
}

# VERIFICACION DE CATALOGOS AL ARRANQUE
# -------------------------------------------------------------
# El proceso servidor no debe levantarse con los catalogos maestros vacios.
VERIFICAR_CATALOGOS_AL_ARRANQUE = evaluate_bool_default("VERIFICAR_CATALOGOS_AL_ARRANQUE", "True")

# MOTOR DE COLA DE AVISOS (ARC-014)
# -------------------------------------------------------------
# Despachador en proceso (APScheduler) del outbox de avisos por correo: toma las
# solicitudes PENDIENTE en FIFO con bloqueo, las entrega por SMTP y recupera las que
# quedan atascadas en ENVIANDO tras una caida del worker.
#
# La ventana de recuperacion y la franja de horario laboral son GAPS declarados del RFP
# (REQ-132: "gap: ventana de recuperacion de solicitudes atascadas y franja concreta del
# horario laboral"). Van parametrizadas por entorno con un valor por defecto documentado,
# nunca fijadas en el codigo del despachador.
AVISOS_MOTOR = {
    # Arranque del planificador en proceso. Se desactiva en los comandos de gestion y en
    # las pruebas para que ningun hilo de fondo toque la base de datos sin pedirlo.
    "habilitado": evaluate_bool_default("AVISOS_MOTOR_HABILITADO", "True"),
    "intervalo_despacho_segundos": int(os.environ.get("AVISOS_INTERVALO_DESPACHO_SEGUNDOS", "30")),
    "intervalo_recuperacion_segundos": int(os.environ.get("AVISOS_INTERVALO_RECUPERACION_SEGUNDOS", "60")),
    # Ventana tras la cual una solicitud atascada en ENVIANDO vuelve a PENDIENTE (REQ-132).
    "ventana_recuperacion_minutos": int(os.environ.get("AVISOS_VENTANA_RECUPERACION_MINUTOS", "15")),
    "tamano_lote": int(os.environ.get("AVISOS_TAMANO_LOTE", "25")),
    # Tope de intentos por solicitud; la columna configuracion_smtp.max_attempts manda
    # cuando hay configuracion activa, este es el valor de respaldo (T.5 ARC-109).
    "max_attempts_por_defecto": int(os.environ.get("AVISOS_MAX_ATTEMPTS", "3")),
    # Espera creciente entre reintentos, en minutos (REQ-134 / AC-SMTP-04: 1, 5 y 15 min).
    "backoff_minutos": tuple(int(v) for v in os.environ.get("AVISOS_BACKOFF_MINUTOS", "1,5,15").split(",") if v.strip()),
    "smtp_timeout_segundos": int(os.environ.get("AVISOS_SMTP_TIMEOUT_SEGUNDOS", "30")),
    # Ventana de servicio del DESPACHADOR. No restringe el ENCOLADO, que es siempre posible:
    # las solicitudes generadas fuera de hora se conservan y se procesan al reanudar (REQ-132).
    "respetar_horario_laboral": evaluate_bool_default("AVISOS_RESPETAR_HORARIO_LABORAL", "True"),
    "hora_inicio_laboral": int(os.environ.get("AVISOS_HORA_INICIO_LABORAL", "8")),
    "hora_fin_laboral": int(os.environ.get("AVISOS_HORA_FIN_LABORAL", "20")),
    # Convenio de datetime.weekday(): 0=lunes .. 6=domingo.
    "dias_laborables": tuple(int(v) for v in os.environ.get("AVISOS_DIAS_LABORABLES", "0,1,2,3,4").split(",") if v.strip()),
    # Testigo que el worker escribe en aviso_correo.locked_by (VARCHAR2(60 CHAR)). Si queda
    # vacio, apps/avisos/motor/configuracion.py deriva uno del hostname y el PID del proceso.
    "identificador_worker": os.environ.get("AVISOS_WORKER_ID", "")[:60],
}

# COMPOSICION DEL CONTENIDO DE LOS AVISOS (ARC-014, REQ-131)
# -------------------------------------------------------------
# Enlace al detalle de la incidencia que viaja en el correo de aviso de alta. El correo NO
# adjunta la foto de la incidencia: solo enlaza al detalle en la SPA, donde la fotografia ya
# esta custodiada.
#
# La URL base publica de la SPA es un GAP declarado del RFP (REQ-131): el RFP no fija la
# direccion con la que se publica la aplicacion y el correo necesita una URL absoluta, porque
# se lee fuera del navegador que sirvio la SPA. Por eso va parametrizada por entorno con un
# valor por defecto documentado y NUNCA fijada en el codigo del compositor.
AVISOS_COMPOSICION = {
    "url_base_spa": os.environ.get("AVISOS_URL_BASE_SPA", "http://localhost:4200"),
    "ruta_detalle_incidencia": os.environ.get("AVISOS_RUTA_DETALLE_INCIDENCIA", "/incidencias/{incident_id}"),
}

# LOGGING
# -------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    # El patron `verbose` incluye `%(data)s`: sin este filtro, cualquier registro ajeno al
    # servicio (`django.request` al responder un 401, las librerias de terceros...) rompe el
    # formateo y se pierde la linea. El filtro rellena `data` cuando falta.
    "filters": {
        "datos_estructurados": {
            "()": "apps.core_security.trazas.DatosEstructuradosFilter",
        },
    },
    "formatters": {
        "verbose": {"format": "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s %(data)s"},
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose" if LOCAL_ENVIRONMENT else "json",
            "filters": ["datos_estructurados"],
        },
        'null': {'class': 'logging.NullHandler'},
    },
    "loggers": {
        "": {
            "handlers": ["console"],
            "level": os.getenv("LOGGER_LEVEL", "DEBUG" if DEBUG else "INFO"),
            "propagate": False,
        },
        "django": {
            "handlers": ["console" if DEBUG else "null"],
            "level": os.getenv("LOGGER_LEVEL", "DEBUG" if DEBUG else "INFO"),
        },
        "django.server": {
            "handlers": ["console" if DEBUG else "null"],
            "level": os.getenv("LOGGER_LEVEL", "DEBUG" if DEBUG else "INFO"),
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": ["console" if DEBUG else "null"],
            "level": os.getenv("LOGGER_LEVEL", "DEBUG" if DEBUG else "INFO"),
            "propagate": False,
        },
        "django.utils.autoreload": {
            "handlers": ["console" if DEBUG else "null"],
            "level": os.getenv("LOGGER_LEVEL", "DEBUG" if DEBUG else "INFO"),
            "propagate": False,
        },
        # Planificador en proceso del motor de avisos (apps/avisos/motor/planificador.py). Se fija
        # a INFO para que el detalle por tick ("Running job...") no inunde el log en DEBUG; el
        # resultado de cada ciclo lo traza el propio motor con su formato estructurado.
        "apscheduler": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
