# https://github.com/typeddjango/django-stubs/pull/1887
# pyright: reportCallIssue=false, reportArgumentType=false
# mypy: disable-error-code="arg-type"

from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("search/", views.search, name="search"),
    path("artist/<str:artist_id>/", views.albums, name="albums"),
    path("playlists/", views.playlists, name="playlists"),
    path("artists/", views.followed_artists, name="artists"),
    path("playlist/<str:playlist_id>/", views.playlist, name="playlist"),
    path("deduplicate/<str:playlist_id>/", views.deduplicate, name="deduplicate"),
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),
    path("callback/", views.callback, name="callback"),
]
