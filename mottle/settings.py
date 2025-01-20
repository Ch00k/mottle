from pathlib import Path
from socket import gethostbyname, gethostname
from typing import Any

import sentry_sdk
from cryptography.fernet import Fernet, MultiFernet
from environs import Env
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from tekore import Credentials

env = Env()

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = env.str("SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", False)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")  # ["127.0.0.1", "localhost", "mottle.it", "www.mottle.it"]
ALLOWED_HOSTS.append(gethostbyname(gethostname()))  # Needed for Prometheus scraping to work

CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS")  # ["https://mottle.it", "https://www.mottle.it"]
SESSION_COOKIE_DOMAIN = env.str("SESSION_COOKIE_DOMAIN", None)
# SESSION_COOKIE_AGE = 3_153_600_000  # 100 years
SESSION_SAVE_EVERY_REQUEST = True

APP_VERSION = env.str("APP_VERSION", "dev")

INSTALLED_APPS = [
    "daphne",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "web",
    "urlshortener",
    "django_hosts",
    "django_htmx",
    "django_prometheus",
    "django_q",
    "django.contrib.gis",
]

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django_hosts.middleware.HostsRequestMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "web.middleware.SpotifyAuthMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "django_hosts.middleware.HostsResponseMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

URLSHORTENER_BASE_URL = env.str("URLSHORTENER_BASE_URL", "https://mottle.it")

ROOT_HOSTCONF = "mottle.hosts"
ROOT_URLCONF = "mottle.urls"

DEFAULT_HOST = "default"

LOGIN_URL = "/login/"
AUTH_EXEMPT_PATHS = [LOGIN_URL, "/", "/logout/", "/callback/", "/metrics", "/changelog/"]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "web.context_processors.app_version",
                "web.context_processors.global_template_vars",
            ],
        },
    },
]

ASGI_APPLICATION = "mottle.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.spatialite",
        "NAME": env.path("DATABASE_FILE", BASE_DIR / "db.sqlite3"),
    },
    "tasks": {
        "ENGINE": "django_prometheus.db.backends.sqlite3",
        "NAME": env.path("DATABASE_FILE_TASKS", BASE_DIR / "tasks.sqlite3"),
    },
}

DATABASE_ROUTERS = [
    "mottle.db_routers.DefaultRouter",
    "mottle.db_routers.TasksRouter",
]

if gdal_library_path := env.str("GDAL_LIBRARY_PATH", None):
    GDAL_LIBRARY_PATH = gdal_library_path

if geos_library_path := env.str("GEOS_LIBRARY_PATH", None):
    GEOS_LIBRARY_PATH = geos_library_path

sentry_sdk.init(
    dsn=env.str("SENTRY_DSN", ""),
    enable_tracing=True,
    release=f"mottle@{APP_VERSION}",
    environment=env.str("SENTRY_ENVIRONMENT", "dev"),
    integrations=[
        DjangoIntegration(transaction_style="url", middleware_spans=True, signals_spans=False, cache_spans=False),
        LoggingIntegration(level=None, event_level=None),
    ],
)

Q_CLUSTER = {
    "name": "default",
    "orm": "tasks",
    "workers": 1,
    "timeout": 300,
    "retry": 450,
    "max_attempts": 1,
    "ack_failures": True,
    "save_limit": 0,
    "scheduler": False,
    "log_level": "DEBUG",
    "ALT_CLUSTERS": {
        "long_running": {
            "orm": "tasks",
            "timeout": 20 * 60 * 60,  # 20 hours
            "retry": 21 * 60 * 60,  # 21 hours
        },
    },
    "error_reporter": {
        "sentry": {},
    },
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = env.path("STATIC_ROOT", BASE_DIR / "web/static/")
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


LOG_FORMAT = env.str("LOG_FORMAT", "json")
DEBUG_SQL = env.bool("DEBUG_SQL", False)

LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {
            "format": "{asctime} {levelname:<8s} {name:<15s} {message}",
            "style": "{",
        },
        "json": {
            "()": "mottle.logging.MottleJSONFormatter",
        },
    },
    "filters": {
        "require_debug_true": {
            "()": "django.utils.log.RequireDebugTrue",
        }
    },
    "handlers": {
        "plain": {
            "class": "logging.StreamHandler",
            "formatter": "plain",
        },
        "json": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
        "sql": {
            "class": "logging.StreamHandler",
            "formatter": "plain",
            "filters": ["require_debug_true"],
        },
    },
    "root": {
        "level": "INFO",
        "handlers": [LOG_FORMAT],
    },
    "loggers": {
        "__main__": {
            "level": "DEBUG",
            "handlers": [LOG_FORMAT],
            "propagate": False,
        },
        "web": {
            "level": "DEBUG",
            "handlers": [LOG_FORMAT],
            "propagate": False,
        },
        "scheduler": {
            "level": "DEBUG",
            "handlers": [LOG_FORMAT],
            "propagate": False,
        },
        "taskrunner": {
            "level": "DEBUG",
            "handlers": [LOG_FORMAT],
            "propagate": False,
        },
        "django.channels.server": {
            "level": "WARNING",
            "handlers": [LOG_FORMAT],
            "propagate": False,
        },
        "django-q": {
            "level": "DEBUG",
            "handlers": [LOG_FORMAT],
            "propagate": False,
        },
    },
}

if DEBUG_SQL:
    LOGGING["loggers"]["django.db.backends"]["level"] = "DEBUG"
    LOGGING["loggers"]["django.db.backends"]["handlers"] = ["sql"]
    LOGGING["loggers"]["django.db.backends"]["proagate"] = False


TEKORE_HTTP_TIMEOUT = env.int("TEKORE_HTTP_TIMEOUT", 15)

SPOTIFY_CLIENT_ID = env.str("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = env.str("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = env.str("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:65534/callback/")
SPOTIFY_TOKEN_SCOPE = env.list(
    "SPOTIFY_TOKEN_SCOPE",
    [
        "playlist-read-private",
        "playlist-read-collaborative",
        "playlist-modify-private",
        "playlist-modify-public",
        "user-follow-read",
        "user-follow-modify",
        "user-read-email",
        "ugc-image-upload",
        "user-library-read",
        "user-library-modify",
    ],
)

SPOTIFY_CREDEINTIALS = Credentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
)

SPOTIFY_TOKEN_ENCRYPTION_KEYS = env.list("SPOTIFY_TOKEN_ENCRYPTION_KEYS")
SPOTIFY_TOKEN_CRYPTER = MultiFernet([Fernet(k) for k in SPOTIFY_TOKEN_ENCRYPTION_KEYS])

PLAYLIST_ADD_TRACKS_PARALLELIZED = env.bool("PLAYLIST_ADD_TRACKS_PARALLELIZED", False)

MAILERSEND_API_TOKEN = env.str("MAILERSEND_API_TOKEN")
MAILERSEND_HTTP_TIMEOUT = env.int("MAILERSEND_HTTP_TIMEOUT", 15)
MAIL_FROM_EMAIL = env.str("MAIL_FROM_EMAIL")
MAIL_FROM_NAME = env.str("MAIL_FROM_NAME")

OPENAI_API_KEY = env.str("OPENAI_API_KEY", None)
OPENAI_IMAGES_DUMP_DIR = env.path("OPENAI_IMAGES_DUMP_DIR", BASE_DIR / "images_dump")

HTTP_USER_AGENT = f"mottle/{APP_VERSION}"
BRIGHTDATA_PROXY_URL = env.str("BRIGHTDATA_PROXY_URL", None)

EVENT_ARTIST_NAME_MATCH_THRESHOLD = env.int("EVENT_ARTIST_NAME_MATCH_THRESHOLD", 85)

GEODJANGO_SRID = 4326
EVENT_DISTANCE_THRESHOLD_KM = env.float("EVENT_DISTANCE_THRESHOLD_KM", 100)

EVENTS_ENABLED_FOR_SPOTIFY_USER_IDS = env.list("EVENTS_ENABLED_FOR_SPOTIFY_USER_IDS", [])
