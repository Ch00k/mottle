# https://github.com/typeddjango/django-stubs/pull/1887
# pyright: reportCallIssue=false, reportArgumentType=false
# mypy: disable-error-code="arg-type"

from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("search/artists/", views.search_artists, name="search_artists"),
    path("search/playlists/", views.search_playlists, name="search_playlists"),
    path("artist/<str:artist_id>/", views.albums, name="albums"),
    path("playlists/", views.playlists, name="playlists"),
    path("artists/", views.followed_artists, name="artists"),
    path("playlist/<str:playlist_id>/", views.playlist, name="playlist"),
    path("playlist/<str:playlist_id>/audio-features/", views.playlist_audio_features, name="playlist_audio_features"),
    path("playlist/<str:playlist_id>/copy/", views.copy_playlist, name="copy_playlist"),
    path("playlist/<str:playlist_id>/merge/", views.merge_playlist, name="merge_playlist"),
    path("playlist/<str:playlist_id>/configuration/", views.configure_playlist, name="configure_playlist"),
    path("playlist/<str:playlist_id>/updates/", views.playlist_updates, name="playlist_updates"),
    path("playlist-updates/<str:update_id>/accept/", views.accept_playlist_update, name="accept_playlist_update"),
    path("playlist/<str:watched_playlist_id>/watch/", views.watch_playlist, name="watch_playlist"),
    path("playlist/<str:playlist_id>/auto-accept-updates/", views.auto_accept_updates, name="auto_accept_updates"),
    path("deduplicate/<str:playlist_id>/", views.deduplicate, name="deduplicate"),
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),
    path("callback/", views.callback, name="callback"),
]
