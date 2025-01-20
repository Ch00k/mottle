from django.http import HttpRequest, HttpResponse, HttpResponseServerError
from django_htmx.http import trigger_client_event


def server_error(_: HttpRequest) -> HttpResponse:
    return trigger_client_event(
        HttpResponseServerError(),
        "HXToast",
        {"type": "error", "body": "Oops, something went wrong"},
    )
