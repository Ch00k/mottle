from web.jobs import check_playlists_for_updates


async def get_playlist_updates() -> None:
    await check_playlists_for_updates(send_notifications=True)
