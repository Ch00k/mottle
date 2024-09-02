from pathlib import Path

import sentry_sdk
from cryptography.fernet import Fernet, MultiFernet
from environs import Env
from sentry_sdk.integrations.django import DjangoIntegration
from tekore import Credentials

env = Env()

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = env.str("SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", False)

# web is the name of the Docker Compose service, and is needed for Prometheus scraping target
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "web", "mottle.it", "www.mottle.it"]
CSRF_TRUSTED_ORIGINS = ["https://mottle.it", "https://www.mottle.it"]
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
    "django_htmx",
    "django_prometheus",
    "django_q",
]

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "web.middleware.SpotifyAuthMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

ROOT_URLCONF = "mottle.urls"

LOGIN_URL = "/login/"
AUTH_EXEMPT_PATHS = [LOGIN_URL, "/", "/logout/", "/callback/", "/metrics"]

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
        "ENGINE": "django_prometheus.db.backends.sqlite3",
        "NAME": env.path("DATABASE_FILE", BASE_DIR / "db.sqlite3"),
    }
}

Q_CLUSTER = {
    "orm": "default",
    "workers": 1,
    "timeout": 300,
    "retry": 600,
    "max_attempts": 1,
    "save_limit": 0,
    "schedule": False,
    "log_level": "DEBUG",
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
        "scheduler": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "taskrunner": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "django-q": {
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
