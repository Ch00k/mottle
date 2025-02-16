# https://github.com/typeddjango/django-stubs/pull/1887
# pyright: reportCallIssue=false, reportArgumentType=false
# mypy: disable-error-code="arg-type"

from django.urls import include, path

from . import views

urlpatterns = [
    path("", include("django_prometheus.urls")),
    path("", views.index, name="index"),
    path("changelog/", views.changelog, name="changelog"),
    path("search/artists/", views.search_artists, name="search_artists"),
    path("search/playlists/", views.search_playlists, name="search_playlists"),
    path("artist/<str:artist_id>/", views.albums, name="albums"),
    path("artist/<str:artist_id>/events/", views.artist_events, name="artist_events"),
    path("album/<str:album_id>/", views.album, name="album"),
    path("playlists/", views.playlists, name="playlists"),
    # path("artists/", views.followed_artists, name="artists"),
    path("playlist/<str:playlist_id>/", views.playlist_items, name="playlist"),
    path("playlist/<str:playlist_id>/rename/", views.rename_playlist, name="rename_playlist"),
    path("playlist/<str:playlist_id>/cover-image/", views.playlist_cover_image, name="playlist_cover_image"),
    path("playlist/<str:playlist_id>/follow/", views.follow_playlist, name="follow_playlist"),
    path("playlist/<str:playlist_id>/unfollow/", views.unfollow_playlist, name="unfollow_playlist"),
    path("playlist/<str:playlist_id>/audio-features/", views.playlist_audio_features, name="playlist_audio_features"),
    path("playlist/<str:playlist_id>/copy/", views.copy_playlist, name="copy_playlist"),
    path("playlist/<str:playlist_id>/merge/", views.merge_playlist, name="merge_playlist"),
    path("playlist/<str:playlist_id>/watch/", views.watch_playlist, name="watch_playlist"),
    path("playlist/<str:playlist_id>/unwatch/", views.unwatch_playlist, name="unwatch_playlist"),
    path("playlist/<str:playlist_id>/deduplicate/", views.deduplicate_playlist, name="deduplicate_playlist"),
    path("playlist/<str:playlist_id>/configuration/", views.configure_playlist, name="configure_playlist"),
    path("playlist/<str:playlist_id>/updates/", views.playlist_updates, name="playlist_updates"),
    path(
        "playlist/<str:playlist_id>/auto-accept-updates/",
        views.auto_accept_playlist_updates,
        name="auto_accept_playlist_updates",
    ),
    path(
        "playlist/<str:playlist_id>/remove-tracks/",
        views.remove_tracks_from_playlist,
        name="remove_tracks_from_playlist",
    ),
    path(
        "playlist/<str:playlist_id>/updates/<str:update_id>/accept/",
        views.accept_playlist_update,
        name="accept_playlist_update",
    ),
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),
    path("callback/", views.callback, name="callback"),
    path("settings/", views.user_settings, name="user_settings"),
    path("events/", views.user_events, name="user_events"),
    path("event/<str:event_id>/", views.event_details, name="event_details"),
]
