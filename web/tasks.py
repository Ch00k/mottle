from django_q.tasks import async_task

from .images import upload_cover_image


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
    )
