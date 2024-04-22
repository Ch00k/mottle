import itertools
import logging
from collections import defaultdict
from uuid import UUID

from django.db.models import Q

from web.email import send_email
from web.spotify import get_client_token
from web.views_utils import compile_email

from .models import PlaylistUpdate, SpotifyUser, generate_playlist_update_hash
from .utils import MottleException, MottleSpotifyClient

logger = logging.getLogger(__name__)


async def check_user_playlists_for_updates(user: SpotifyUser, spotify_client: MottleSpotifyClient) -> None:
    updates = defaultdict(list)

    async for playlist in user.playlists.filter(~Q(watched_playlists=None)):  # pyright: ignore
        logger.info(f"Processing playlist {playlist}")

        try:
            playlist_tracks = await spotify_client.get_playlist_items(playlist.spotify_id)
        except MottleException as e:
            logger.error(f"Failed to get tracks for playlist {playlist}: {e}")
            continue

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
                logger.info(
                    "Checking if PlaylistUpdate with the same target_playlist and source_playlist " "already exists"
                )
                try:
                    outdated_update = await PlaylistUpdate.objects.aget(
                        target_playlist=playlist, source_playlist=watched_playlist, is_overridden_by=None
                    )
                except PlaylistUpdate.DoesNotExist:
                    logger.info("PlaylistUpdate with the same target_playlist and source_playlist does not exist")
                    outdated_update = None
                else:
                    logger.info(
                        "PlaylistUpdate with the same target_playlist and source_playlist exists: {outdated_update}"
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
                    outdated_update.save()

                updates[playlist.spotify_id].append(update)
            else:
                logger.info(f"PlaylistUpdate with hash {update_hash} already exists: {update}")
                if update.is_notified_of:
                    logger.info(f"PlaylistUpdate {update} already has been notified of")
                    continue
                else:
                    logger.info(f"PlaylistUpdate {update} has not been notified of yet")
                    updates[playlist.spotify_id].append(update)

    if updates:
        if user.email is not None:
            message = await compile_email(updates, spotify_client)
            logger.info(f"Sending email to {user.email}:\n{message}")
            await send_email(user.email, "Playlists you watch got updates", message)

            playlist_updates = list(itertools.chain.from_iterable(updates.values()))
            for pl in playlist_updates:
                pl.is_notified_of = True

            await PlaylistUpdate.objects.abulk_update(playlist_updates, ["is_notified_of"])
        else:
            logger.warning(f"No email for user {user}")
    else:
        logger.info(f"No updates for user {user}")


async def check_playlists_for_updates() -> None:
    logger.info("Checking playlists for updates")
    token = get_client_token()
    spotify_client = MottleSpotifyClient(token.access_token)

    async for user in SpotifyUser.objects.filter(~Q(playlists=None)):
        logger.info(f"Processing user {user}")
        await check_user_playlists_for_updates(user, spotify_client)


def scheck_playlists_for_updates() -> None:
    logger.info("Checking playlists for updates")
    token = get_client_token()
    spotify_client = MottleSpotifyClient(token.access_token, is_async=False)

    updates: dict[UUID, list] = {}

    for user in SpotifyUser.objects.filter(~Q(playlists=None)):
        logger.info(f"Processing user {user}")
        updates[user.id] = []

        for playlist in user.playlists.filter(~Q(watched_playlists=None)):  # pyright: ignore
            logger.info(f"Processing playlist {playlist}")

            try:
                playlist_tracks = spotify_client.sget_playlist_items(playlist.spotify_id)
            except MottleException as e:
                logger.error(f"Failed to get tracks for playlist {playlist}: {e}")
                continue

            playlist_track_ids = set([track.track.id for track in playlist_tracks if track.track is not None])

            watched_playlists = playlist.watched_playlists.all()
            logger.info(f"Watched playlists: {watched_playlists.count()}")

            for watched_playlist in watched_playlists:
                logger.info(f"Processing watched playlist {watched_playlist}")

                try:
                    watched_playlist_tracks = spotify_client.sget_playlist_items(watched_playlist.spotify_id)
                except MottleException as e:
                    logger.error(f"Failed to get tracks for watched playlist {watched_playlist}: {e}")
                    continue

                watched_playlist_track_ids = set(
                    [track.track.id for track in watched_playlist_tracks if track.track is not None]
                )
                new_track_ids = watched_playlist_track_ids - playlist_track_ids

                if new_track_ids:
                    logger.info(f"New track IDs: {new_track_ids}")
                    update = PlaylistUpdate.objects.create(
                        target_playlist=playlist, source_playlist=watched_playlist, tracks_added=list(new_track_ids)
                    )
                    updates[user.id].append(update)
                else:
                    logger.info(f"No new tracks in playlist {watched_playlist}")


# def upate() -> None:
#     spotify_client = MottleSpotifyClient()

#     for playlist in Playlist.objects.all():
#         logger.info(f"Processing playlist {playlist}")

#         watched_artists = playlist.watched_artists.all()  # pyright: ignore
#         for watched_artist in watched_artists:
#             logger.info(f"Processing watched artist {watched_artist}")

#             album_types = []

#             if watched_artist.watch_albums:
#                 album_types.append("album")
#             if watched_artist.watch_singles:
#                 album_types.append("single")
#             if watched_artist.watch_cmpilations:
#                 album_types.append("compilation")

#             logger.info(f"Watched album types: {album_types}")

#             try:
#                 all_albums = async_to_sync(
#                     partial(spotify_client.get_artist_albums, watched_artist.artist.spotify_id, album_types)
#                 )()
#             except MottleException as e:
#                 logger.error(f"Failed to get albums for {watched_artist}: {e}")
#                 continue

#             all_album_ids = [album.id for album in all_albums]
#             logger.info(f"Fetched album IDs: {all_album_ids}")

#             seen_album_ids = watched_artist.seen_albums
#             logger.info(f"Seen album IDs: {seen_album_ids}")

#             new_album_ids = set(all_album_ids) - set(seen_album_ids)
#             logger.info(f"New album IDs: {new_album_ids}")

#             try:
#                 new_tracks = async_to_sync(partial(spotify_client.get_tracks_in_albums, list(new_album_ids)))()
#             except MottleException as e:
#                 logger.error(f"Failed to get tracks for new albums: {e}")
#                 continue

#             new_track_uris = [track.uri for track in new_tracks]

#             try:
#                 async_to_sync(partial(spotify_client.add_tracks_to_playlist, playlist.spotify_id, new_track_uris))()
#             except MottleException as e:
#                 logger.error(f"Failed to add tracks to playlist: {e}")
#                 continue

#             watched_artist.seen_albums = list(all_album_ids)
#             watched_artist.save()
