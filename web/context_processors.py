from django.conf import settings
from django.http import HttpRequest


def app_version(_: HttpRequest) -> dict[str, str]:
    return {
        "APP_VERSION": settings.APP_VERSION,
    }
