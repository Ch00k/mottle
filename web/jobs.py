import logging
from typing import Any, Optional

from asgiref.sync import sync_to_async
from django.db.models import Q

from web.email import send_email
from web.views_utils import compile_email

from .models import Playlist, PlaylistUpdate, SpotifyUser
from .utils import MottleException, MottleSpotifyClient

logger = logging.getLogger(__name__)


async def check_playlist_for_updates(playlist: Playlist, spotify_client: MottleSpotifyClient) -> list[PlaylistUpdate]:
    updates: list[PlaylistUpdate] = []

    logger.info(f"Processing playlist {playlist}")

    try:
        playlist_track_ids = await playlist.get_track_ids(spotify_client)
    except MottleException as e:
        logger.error(f"Failed to get tracks for playlist {playlist}: {e}")
        return updates

    watch_configs = playlist.configs_as_watching.all()  # pyright: ignore
    logger.info(f"Watched playlists: {await watch_configs.acount()}")

    async for config in watch_configs:
        watched_playlist = await sync_to_async(lambda: config.watched_playlist)()
        logger.info(f"Processing watched playlist {watched_playlist}")

        try:
            watched_playlist_track_ids = await watched_playlist.get_track_ids(spotify_client)
        except MottleException as e:
            logger.error(f"Failed to get tracks for watched playlist {watched_playlist}: {e}")
            continue

        new_track_ids = set(watched_playlist_track_ids) - set(playlist_track_ids)

        if not new_track_ids:
            logger.info(f"No new tracks in playlist {watched_playlist}")
            continue

        logger.info(f"New track IDs: {new_track_ids}")
        update, created = await PlaylistUpdate.find_or_create(playlist, watched_playlist, list(new_track_ids))
        if created:
            logger.info(f"Created PlaylistUpdate {update}")
            logger.info("Adding to updates list")
            updates.append(update)
        else:
            logger.info(f"Found existing PlaylistUpdate {update}")
            if update.is_notified_of:
                logger.info(f"PlaylistUpdate {update} already has been notified of")
            else:
                logger.info(f"PlaylistUpdate {update} has not been notified of yet")
                logger.info("Adding to updates list")
                updates.append(update)

    return updates


async def check_user_playlists_for_updates(
    user: SpotifyUser, spotify_client: MottleSpotifyClient, send_notifications: bool = False
) -> None:
    logger.info(f"Processing user {user}")
    updates: dict[str, list[dict[str, Any]]] = {}

    async for playlist in user.playlists.filter(~Q(configs_as_watching=None)):  # pyright: ignore
        playlist_updates = await check_playlist_for_updates(playlist, spotify_client)

        if playlist_updates:
            augmented_playlist_updates = [
                {
                    "update": p,
                    "auto_acceptable": await sync_to_async(lambda: p.is_auto_acceptable)(),
                    "auto_accept_successful": False,
                }
                for p in playlist_updates
            ]
            updates[playlist.spotify_id] = augmented_playlist_updates

    if not updates:
        logger.info(f"No updates for user {user}")
        return

    user_spotify_client: Optional[MottleSpotifyClient] = None

    for update_list in updates.values():
        for update in update_list:
            if update["auto_acceptable"]:
                if user_spotify_client is None:
                    spotify_auth = await sync_to_async(lambda: user.spotify_auth)()  # pyright: ignore
                    await spotify_auth.maybe_refresh()  # TODO: Put this inside MottleSpotifyClient?
                    user_spotify_client = MottleSpotifyClient(spotify_auth.access_token)

                try:
                    await update["update"].accept(user_spotify_client)
                except Exception as e:
                    logger.error(f"Failed to accept update {update}: {e}")
                else:
                    update["auto_accept_successful"] = True

    message = await compile_email(updates, spotify_client)
    logger.debug(f"Email message:\n{message}")

    if not send_notifications:
        logger.warning("Email notifications disabled")
        return

    if user.email is None:
        logger.warning(f"No email for user {user}")
        return

    logger.info(f"Sending email to {user.email}")
    await send_email(user.email, "Your watched playlists have been updated", message)

    await PlaylistUpdate.objects.filter(
        target_playlist__in=user.playlists.filter(~Q(configs_as_watching=None))  # pyright: ignore
    ).aupdate(is_notified_of=True)


async def check_playlists_for_updates(spotify_client: MottleSpotifyClient, send_notifications: bool = False) -> None:
    logger.info("Checking playlists for updates")

    async for user in SpotifyUser.objects.filter(~Q(playlists=None)):
        await check_user_playlists_for_updates(user, spotify_client, send_notifications)
