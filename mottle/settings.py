from pathlib import Path
from typing import Any

import sentry_sdk
from cryptography.fernet import Fernet, MultiFernet
from environs import Env
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from tekore import Credentials

env = Env()

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = env.str("SECRET_KEY", "very-secret-key")
DEBUG = env.bool("DJANGO_DEBUG", False)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", ["127.0.0.1"])  # ["127.0.0.1", "localhost", "mottle.it", "www.mottle.it"]

CSRF_TRUSTED_ORIGINS: list[str] = env.list(
    "CSRF_TRUSTED_ORIGINS", ["https://mottle.it"]
)  # ["https://mottle.it", "https://www.mottle.it"]
SESSION_COOKIE_DOMAIN = env.str("SESSION_COOKIE_DOMAIN", None)
# SESSION_COOKIE_AGE = 3_153_600_000  # 100 years
SESSION_SAVE_EVERY_REQUEST = True

APP_VERSION = env.str("APP_VERSION", "dev")

INSTALLED_APPS = [
    "daphne",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "web",
    "urlshortener",
    "django_hosts",
    "django_htmx",
    "django.contrib.gis",
]

MIDDLEWARE = [
    "django_hosts.middleware.HostsRequestMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "web.middleware.SpotifyAuthMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "django_hosts.middleware.HostsResponseMiddleware",
]

URLSHORTENER_BASE_URL = env.str("URLSHORTENER_BASE_URL", "https://mottle.it")

ROOT_HOSTCONF = "mottle.hosts"
ROOT_URLCONF = "mottle.urls"

DEFAULT_HOST = "default"

LOGIN_URL = "/login/"
AUTH_EXEMPT_PATHS = [LOGIN_URL, "/", "/logout/", "/callback/", "/changelog/"]

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
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "timeout": 5,  # seconds
            "init_command": """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                PRAGMA mmap_size = 134217728;
                PRAGMA journal_size_limit = 27103364;
                PRAGMA cache_size=2000;
            """,
        },
    },
}

if gdal_library_path := env.str("GDAL_LIBRARY_PATH", None):
    GDAL_LIBRARY_PATH = gdal_library_path

if geos_library_path := env.str("GEOS_LIBRARY_PATH", None):
    GEOS_LIBRARY_PATH = geos_library_path

sentry_sdk.init(
    dsn=env.str("SENTRY_DSN", ""),
    traces_sample_rate=1.0,
    release=f"mottle@{APP_VERSION}",
    environment=env.str("SENTRY_ENVIRONMENT", "dev"),
    integrations=[
        DjangoIntegration(transaction_style="url", middleware_spans=True, signals_spans=False, cache_spans=False),
        LoggingIntegration(level=None, event_level=None),
    ],
)

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
        "web": {
            "level": "DEBUG",
            "handlers": [LOG_FORMAT],
            "propagate": False,
        },
        "django.channels.server": {
            "level": "WARNING",
            "handlers": [LOG_FORMAT],
            "propagate": False,
        },
    },
}

if DEBUG_SQL:
    LOGGING["loggers"]["django.db.backends"] = {"level": "DEBUG", "handlers": ["sql"], "propagate": False}


TEKORE_HTTP_TIMEOUT = env.int("TEKORE_HTTP_TIMEOUT", 15)

SPOTIFY_CLIENT_ID = env.str("SPOTIFY_CLIENT_ID", "client-id")
SPOTIFY_CLIENT_SECRET = env.str("SPOTIFY_CLIENT_SECRET", "client-secret")
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

SPOTIFY_TOKEN_ENCRYPTION_KEYS: list[str] = env.list(
    "SPOTIFY_TOKEN_ENCRYPTION_KEYS", ["dGhpc19pc19hX3Rlc3Rfa2V5XzMyX2J5dGVzX2xvbmc="]
)
SPOTIFY_TOKEN_CRYPTER = MultiFernet([Fernet(k) for k in SPOTIFY_TOKEN_ENCRYPTION_KEYS])

PLAYLIST_ADD_TRACKS_PARALLELIZED = env.bool("PLAYLIST_ADD_TRACKS_PARALLELIZED", False)

MAIL_FROM_EMAIL = env.str("MAIL_FROM_EMAIL", "me@e.mail")
MAIL_FROM_NAME = env.str("MAIL_FROM_NAME", "Me")

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": env.str("EMAIL_HOST", "localhost"),
            "port": env.int("EMAIL_PORT", 587),
            "use_tls": env.bool("EMAIL_USE_TLS", True),
            "use_ssl": env.bool("EMAIL_USE_SSL", False),
            "username": env.str("EMAIL_HOST_USER", ""),
            "password": env.str("EMAIL_HOST_PASSWORD", ""),
            "timeout": env.int("EMAIL_TIMEOUT", 15),
        },
    },
}

OPENAI_API_KEY = env.str("OPENAI_API_KEY", "openai-api-key")
OPENAI_IMAGES_DUMP_DIR = env.path("OPENAI_IMAGES_DUMP_DIR", BASE_DIR / "images_dump")

HTTP_USER_AGENT = f"mottle/{APP_VERSION}"

# Compose passes an unset variable through as an empty string
PROXY_URL = env.str("PROXY_URL", None) or None

EVENTS_ENABLED = env.bool("EVENTS_ENABLED", True)
EVENT_SOURCES_FETCH_CONCURRENCY_LIMIT = env.int("EVENT_SOURCES_FETCH_CONCURRENCY_LIMIT", 100)
EVENTS_FETCH_CONCURRENCY_LIMIT = env.int("EVENTS_FETCH_CONCURRENCY_LIMIT", 100)
EVENT_ARTIST_NAME_MATCH_THRESHOLD = env.int("EVENT_ARTIST_NAME_MATCH_THRESHOLD", 85)
RESOLVE_SONGKICK_URLS = env.bool("RESOLVE_SONGKICK_URLS", False)

GEODJANGO_SRID = 4326
