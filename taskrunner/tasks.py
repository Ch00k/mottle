from django_q.tasks import async_task

from web.tasks import track_artists_events, upload_cover_image


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


def task_track_artists_events(artists_data: dict[str, str], spotify_user_id: str) -> None:
    async_task(
        track_artists_events,
        artists_data=artists_data,
        spotify_user_id=spotify_user_id,
        cluster="long_running",
    )
