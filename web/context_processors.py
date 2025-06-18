from django.conf import settings
from django.http import HttpRequest


def app_version(_: HttpRequest) -> dict[str, str]:
    return {
        "APP_VERSION": settings.APP_VERSION,
    }


def global_template_vars(_: HttpRequest) -> dict[str, bool]:
    return {}
