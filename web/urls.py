from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("search/", views.search, name="search"),  # type: ignore[arg-type]
    path("artist/<str:artist_id>/", views.albums, name="albums"),  # type: ignore[arg-type]
    path("playlists/", views.playlists, name="playlists"),  # type: ignore[arg-type]
    path("artists/", views.followed_artists, name="artists"),  # type: ignore[arg-type]
    path("playlist/<str:playlist_id>/", views.playlist, name="playlist"),  # type: ignore[arg-type]
    path("login/", views.login, name="login"),  # type: ignore[arg-type]
    path("logout/", views.logout, name="logout"),  # type: ignore[arg-type]
    path("callback/", views.callback, name="callback"),  # type: ignore[arg-type]
]
