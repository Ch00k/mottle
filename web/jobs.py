import itertools
import logging

from django.db.models import Q

from web.email import send_email
from web.views_utils import compile_email

from .models import Playlist, PlaylistUpdate, SpotifyUser, generate_playlist_update_hash
from .utils import MottleException, MottleSpotifyClient

logger = logging.getLogger(__name__)


async def check_playlist_for_updates(playlist: Playlist, spotify_client: MottleSpotifyClient) -> list[PlaylistUpdate]:
    updates: list[PlaylistUpdate] = []

    logger.info(f"Processing playlist {playlist}")

    try:
        playlist_tracks = await spotify_client.get_playlist_items(playlist.spotify_id)
    except MottleException as e:
        logger.error(f"Failed to get tracks for playlist {playlist}: {e}")
        return updates

    playlist_track_ids = set([track.track.id for track in playlist_tracks if track.track is not None])

    watched_playlists = playlist.watched_playlists.all()
    logger.info(f"Watched playlists: {await watched_playlists.acount()}")

    async for watched_playlist in watched_playlists:
        logger.info(f"Processing watched playlist {watched_playlist}")

        try:
            watched_playlist_tracks = await spotify_client.get_playlist_items(watched_playlist.spotify_id)
        except MottleException as e:
            logger.error(f"Failed to get tracks for watched playlist {watched_playlist}: {e}")
            continue

        watched_playlist_track_ids = set(
            [track.track.id for track in watched_playlist_tracks if track.track is not None]
        )
        new_track_ids = watched_playlist_track_ids - playlist_track_ids

        if not new_track_ids:
            logger.info(f"No new tracks in playlist {watched_playlist}")
            continue

        logger.info(f"New track IDs: {new_track_ids}")
        update_hash = generate_playlist_update_hash(tracks_added=list(new_track_ids))  # pyright: ignore
        logger.debug(f"PlaylistUpdate hash: {update_hash}")

        logger.info(f"Checking if PlaylistUpdate with hash {update_hash} already exists")
        try:
            update = await PlaylistUpdate.objects.aget(update_hash=update_hash)
        except PlaylistUpdate.DoesNotExist:
            logger.info(f"PlaylistUpdate with hash {update_hash} does not exist")
            logger.info("Checking if PlaylistUpdate with the same target_playlist and source_playlist already exists")
            try:
                outdated_update = await PlaylistUpdate.objects.aget(
                    target_playlist=playlist, source_playlist=watched_playlist, is_overridden_by=None
                )
            except PlaylistUpdate.DoesNotExist:
                logger.info("PlaylistUpdate with the same target_playlist and source_playlist does not exist")
                outdated_update = None
            else:
                logger.info(
                    f"PlaylistUpdate with the same target_playlist and source_playlist exists: {outdated_update}"
                )

            update = await PlaylistUpdate.objects.acreate(
                target_playlist=playlist,
                source_playlist=watched_playlist,
                is_notified_of=False,
                is_accepted=None,
                tracks_added=list(new_track_ids),
            )
            logger.info(f"Created new PlaylistUpdate: {update}")

            if outdated_update is not None:
                logger.info(f"Setting is_overridden_by to {update} for outdated {outdated_update}")
                outdated_update.is_overridden_by = update
                await outdated_update.asave()

            updates.append(update)
        else:
            logger.info(f"PlaylistUpdate with hash {update_hash} already exists: {update}")
            if update.is_notified_of:
                logger.info(f"PlaylistUpdate {update} already has been notified of")
                continue
            else:
                logger.info(f"PlaylistUpdate {update} has not been notified of yet")
                updates.append(update)

    return updates


async def check_user_playlists_for_updates(
    user: SpotifyUser, spotify_client: MottleSpotifyClient, send_notifications: bool = False
) -> None:
    updates = {}

    async for playlist in user.playlists.filter(~Q(watched_playlists=None)):  # pyright: ignore
        playlist_updates = await check_playlist_for_updates(playlist, spotify_client)

        if playlist_updates:
            updates[playlist.spotify_id] = playlist_updates

    if updates:
        if send_notifications and user.email is not None:
            message = await compile_email(updates, spotify_client)
            logger.info(f"Sending email to {user.email}:\n{message}")
            await send_email(user.email, "Your watched playlists have been updated", message)

            playlist_updates = list(itertools.chain.from_iterable(updates.values()))
            for pl in playlist_updates:
                pl.is_notified_of = True

            await PlaylistUpdate.objects.abulk_update(playlist_updates, ["is_notified_of"])
        else:
            logger.warning(f"No email for user {user}")
    else:
        logger.info(f"No updates for user {user}")


async def check_playlists_for_updates(spotify_client: MottleSpotifyClient, send_notifications: bool = False) -> None:
    logger.info("Checking playlists for updates")

    async for user in SpotifyUser.objects.filter(~Q(playlists=None)):
        logger.info(f"Processing user {user}")
        await check_user_playlists_for_updates(user, spotify_client, send_notifications)
