from django.urls import path

from . import views

urlpatterns = [
    path("<str:hash>", views.get_url, name="get_url"),
]
