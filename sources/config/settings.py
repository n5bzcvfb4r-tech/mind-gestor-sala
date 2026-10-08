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
    "apps.identidad",
    "apps.usuarios",
    "apps.catalogos",
    "apps.incidencias",
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
    # la clase de autenticacion la aporta la tarea duena de identidad (EP-001..EP-004).
    "DEFAULT_AUTHENTICATION_CLASSES": (),
    "EXCEPTION_HANDLER": "rest_framework.views.exception_handler",
}

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

# LOGGING
# -------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
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
    },
}
