from pathlib import Path

import sentry_sdk
from environs import Env
from sentry_sdk.integrations.django import DjangoIntegration
from tekore import Credentials

env = Env()

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = env.str("SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "mottle.it", "www.mottle.it"]
CSRF_TRUSTED_ORIGINS = ["https://mottle.it", "https://www.mottle.it"]
SESSION_COOKIE_DOMAIN = env.str("SESSION_COOKIE_DOMAIN", None)

APP_VERSION = env.str("APP_VERSION", "dev")

INSTALLED_APPS = [
    "daphne",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "web",
    "django_htmx",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "web.middleware.SpotifyAuthMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "mottle.urls"

LOGIN_URL = "/login/"
AUTH_EXEMPT_PATHS = [LOGIN_URL, "/", "/callback/"]

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

WSGI_APPLICATION = "mottle.wsgi.application"
ASGI_APPLICATION = "mottle.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": env.path("DATABASE_FILE", BASE_DIR / "db.sqlite3"),
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = env.path("STATIC_ROOT", BASE_DIR / "static/")
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {
            "format": "{asctime} {levelname:<8s} {name:<15s} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "plain",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "web": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "tekore": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}

TEKORE_HTTP_TIMEOUT = env.int("TEKORE_HTTP_TIMEOUT", 15)

SPOTIFY_CLIENT_ID = env.str("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = env.str("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = env.str("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:65534/callback/")
SPOTIFY_TOKEN_SCOPE = env.list(
    "SPOTIFY_TOKEN_SCOPE",
    [
        "playlist-read-private",
        "playlist-modify-private",
        "playlist-modify-public",
        "user-follow-read",
        "user-follow-modify",
        "ugc-image-upload",
    ],
)

SPOTIFY_CREDEINTIALS = Credentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
)

PLAYLIST_ADD_TRACKS_PARALLELIZED = env.bool("PLAYLIST_ADD_TRACKS_PARALLELIZED", False)

sentry_sdk.init(
    dsn=env.str("SENTRY_DSN", ""),
    enable_tracing=True,
    release=f"mottle@{APP_VERSION}",
    integrations=[
        DjangoIntegration(
            transaction_style="url",
            middleware_spans=True,
            signals_spans=False,
            cache_spans=False,
        ),
    ],
)
