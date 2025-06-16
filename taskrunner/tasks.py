from django_q.tasks import async_task

from web.tasks import (
    check_artists_for_event_updates,
    check_playlists_for_updates,
    track_artists_events,
    upload_cover_image,
)


def task_upload_cover_image(
    playlist_title: str,
    playlist_spotify_id: str,
    spotify_user_id: str,
    dump_to_disk: bool = False,
) -> None:
    async_task(
        upload_cover_image,
        playlist_title=playlist_title,
        playlist_spotify_id=playlist_spotify_id,
        spotify_user_id=spotify_user_id,
        dump_to_disk=dump_to_disk,
        cluster="default",
    )


def task_track_artists_events(
    artists_data: dict[str, str],
    spotify_user_id: str,
    force_reevaluate: bool = False,
    concurrent_execution: bool = True,
    concurrency_limit: int | None = None,
) -> None:
    async_task(
        track_artists_events,
        artists_data=artists_data,
        spotify_user_id=spotify_user_id,
        force_reevaluate=force_reevaluate,
        concurrent_execution=concurrent_execution,
        concurrency_limit=concurrency_limit,
        cluster="long_running",
    )


def get_playlist_updates(send_notifications: bool = False) -> None:
    async_task(
        check_playlists_for_updates,
        send_notifications=send_notifications,
        cluster="long_running",
    )


def get_event_updates(
    artist_spotify_ids: list[str] | None = None,
    compile_notifications: bool = True,
    send_notifications: bool = True,
    force_refetch: bool = False,
    concurrent_execution: bool = True,
    concurrency_limit: int | None = None,
) -> None:
    async_task(
        check_artists_for_event_updates,
        artist_spotify_ids=artist_spotify_ids,
        compile_notifications=compile_notifications,
        send_notifications=send_notifications,
        force_refetch=force_refetch,
        concurrent_execution=concurrent_execution,
        concurrency_limit=concurrency_limit,
        cluster="long_running",
    )
