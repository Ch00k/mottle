# https://github.com/typeddjango/django-stubs/pull/1887
# pyright: reportCallIssue=false, reportArgumentType=false
# mypy: disable-error-code="arg-type"

from django.urls import path

from . import views

urlpatterns = [
    path("<str:hash>", views.get_url, name="get_url"),
]
