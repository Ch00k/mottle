from web.jobs import check_artists_for_event_updates, check_playlists_for_updates


async def get_playlist_updates() -> None:
    await check_playlists_for_updates(send_notifications=True)


async def get_event_updates() -> None:
    await check_artists_for_event_updates()
