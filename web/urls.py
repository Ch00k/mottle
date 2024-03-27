from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("search/", views.search, name="search"),
    path("artist/<str:artist_id>/", views.albums, name="albums"),
    path("playlists/", views.playlists, name="playlists"),
    path("playlist/<str:playlist_id>/", views.playlist, name="playlist"),
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),
    path("callback/", views.callback, name="callback"),
]
