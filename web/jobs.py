import asyncio
import datetime
import itertools
import logging
import timeit
from collections import defaultdict
from typing import Any

from asgiref.sync import sync_to_async
from django.contrib.gis.measure import Distance
from django.db.models import Q

from .email import send_email
from .events.enums import EventType
from .models import EventArtist, EventUpdate, Playlist, PlaylistUpdate, SpotifyUser
from .spotify import get_client_token
from .utils import MottleException, MottleSpotifyClient
from .views_utils import compile_event_updates_email, compile_playlist_updates_email

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
    logger.info(f"Watched playlists or artists: {await watch_configs.acount()}")

    async for config in watch_configs:
        watched_playlist = await sync_to_async(lambda: config.watched_playlist)()
        watched_artist = await sync_to_async(lambda: config.watched_artist)()

        if watched_playlist is not None:
            logger.info(f"Processing watched playlist {watched_playlist}")

            try:
                watched_playlist_track_ids = await watched_playlist.get_track_ids(spotify_client)
            except MottleException as e:
                logger.error(f"Failed to get tracks for watched playlist {watched_playlist}: {e}")
                continue

            ignored_track_ids = config.tracks_ignored or []
            new_track_ids = set(watched_playlist_track_ids) - set(playlist_track_ids) - set(ignored_track_ids)

            if not new_track_ids:
                logger.info(f"No new tracks in playlist {watched_playlist}")
                continue

            logger.info(f"New track IDs: {new_track_ids}")
            update, created = await PlaylistUpdate.find_or_create_for_playlist(
                playlist, watched_playlist, list(new_track_ids)
            )

        if watched_artist is not None:
            logger.info(f"Processing watched artist {watched_artist}")

            watched_artist_all_album_ids = await watched_artist.get_album_ids(spotify_client)
            ignored_album_ids = config.albums_ignored or []

            album_ids = set(watched_artist_all_album_ids) - set(ignored_album_ids)
            new_album_ids = []

            for album_id in album_ids:
                album_tracks = await spotify_client.get_album_tracks(album_id)
                album_track_ids = [t.id for t in album_tracks]

                # TODO: This will return True if at least one track already exists in the playlist
                # TODO: If that's the case, treat the rest of the tracks as `tracks_added`?
                exists_in_playlist = not set(playlist_track_ids).isdisjoint(album_track_ids)
                if not exists_in_playlist:
                    new_album_ids.append(album_id)

            if not new_album_ids:
                logger.info(f"No new albums of artist {watched_artist}")
                continue

            logger.info(f"New album IDs: {new_album_ids}")
            update, created = await PlaylistUpdate.find_or_create_for_artist(
                playlist, watched_artist, list(new_album_ids)
            )

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

    logger.debug(f"Updates for playlist {playlist}: {updates}")
    return updates


async def check_user_playlists_for_updates(user: SpotifyUser, send_notifications: bool = False) -> None:
    logger.info(f"Processing user {user}")
    updates: dict[str, list[dict[str, Any]]] = {}

    spotify_auth = await sync_to_async(lambda: user.spotify_auth)()  # pyright: ignore

    # TODO: This needs to happen for every update, not just once
    try:
        await spotify_auth.maybe_refresh()  # TODO: Put this inside MottleSpotifyClient?
    except Exception as e:
        logger.error(f"Failed to check for playlist updates for user {user}: failed to refresh token: {e}")
        raise

    spotify_client = MottleSpotifyClient(spotify_auth.access_token)

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

    for update_list in updates.values():
        for update in update_list:
            if update["auto_acceptable"]:
                try:
                    await update["update"].accept(spotify_client)
                except Exception as e:
                    logger.error(f"Failed to accept update {update}: {e}")
                else:
                    update["auto_accept_successful"] = True

    message = await compile_playlist_updates_email(updates, spotify_client)
    logger.debug(f"Email message:\n{message}")

    if not send_notifications:
        logger.warning("Email notifications disabled")
        return

    user_config = await sync_to_async(lambda: user.user)()  # pyright: ignore
    if not user_config.playlist_notifications:
        logger.warning("Playlist notifications disabled for user {user}")
        return

    if user.email is None:
        logger.warning(f"No email for user {user}")
        return

    logger.info(f"Sending email to {user.email}")
    try:
        await send_email(user.email, "We've got updates for you", message)
    except Exception as e:
        logger.error(f"Failed to send email to {user.email}: {e}")
    else:
        # TODO: This will update all PlaylistUpdates, not just the ones that were sent
        await PlaylistUpdate.objects.filter(
            target_playlist__in=user.playlists.filter(~Q(configs_as_watching=None))  # pyright: ignore
        ).aupdate(is_notified_of=True)


async def check_playlists_for_updates(send_notifications: bool = False) -> None:
    logger.info("Checking playlists for updates")

    async for user in SpotifyUser.objects.filter(~Q(playlists=None)):
        await check_user_playlists_for_updates(user, send_notifications)


async def check_artists_for_event_updates(
    compile_notifications: bool = True,
    send_notifications: bool = False,
    force_refetch: bool = False,
    concurrent_execution: bool = False,
) -> None:
    start_time = timeit.default_timer()

    logger.info("Checking artists for event updates")

    all_artists = EventArtist.objects.all()
    num_artists = await all_artists.acount()

    success_count = 0
    fail_count = 0
    num_events = 0
    num_urls = 0

    logger.info(f"Artists to process: {num_artists}")

    if concurrent_execution:
        calls = [artist.update_events(force_refetch=force_refetch) async for artist in all_artists]
        results = await asyncio.gather(*calls, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.exception(f"Failed to process artist: {result}")
                fail_count += 1
            elif isinstance(result, EventArtist):
                logger.debug(f"Processed artist: {result}")
                success_count += 1
                num_events += await result.events.acount()  # pyright: ignore
                async for event in result.events.all():  # pyright: ignore
                    num_urls += len(event.stream_urls or []) + len(event.tickets_urls or [])
    else:
        async for artist in all_artists:
            logger.info(f"Processing artist {artist}")

            try:
                await artist.update_events(force_refetch=force_refetch)
            except Exception as e:
                logger.error(f"Failed to update events for artist {artist}: {e}")
                fail_count += 1
                continue
            else:
                success_count += 1

    if compile_notifications:
        # TODO: Only do it if there are updates
        token = get_client_token()
        spotify_client = MottleSpotifyClient(token.access_token)

        async for spotify_user in SpotifyUser.objects.filter(
            ~Q(watched_event_artists=None),
            email__isnull=False,
            user__location__isnull=False,
            user__event_distance_threshold__isnull=False,
        ):
            artists_with_events = defaultdict(list)
            user = await sync_to_async(lambda: spotify_user.user)()  # pyright: ignore

            async for event_artist in spotify_user.watched_event_artists.all():  # pyright: ignore
                # Two cases:
                # 1. Streaming event
                # 2. Non-streaming event with geolocation defined, and its geolocation is within 100 km of user's location
                # Non-streaming events sometimes do not have geolocation (https://www.songkick.com/concerts/41973416)
                events_query = event_artist.events.filter(
                    Q(date__gte=datetime.date.today())
                    & (
                        Q(type=EventType.live_stream)
                        | (
                            Q(~Q(type=EventType.live_stream), geolocation__isnull=False)
                            & Q(geolocation__distance_lte=(user.location, Distance(km=user.event_distance_threshold)))
                        )
                    )
                )

                async for event in events_query.all():
                    async for update in event.updates.filter(is_notified_of=False).all():
                        artists_with_events[event_artist].append(update)

            if not artists_with_events:
                logger.info(f"No event updates for user {spotify_user}")
                continue

            all_updates = list(itertools.chain.from_iterable(artists_with_events.values()))
            logger.info(f"Event updates for user {spotify_user}: {all_updates}")

            message = await compile_event_updates_email(artists_with_events, spotify_client)

            logger.debug(f"Email message for {spotify_user}:\n{message}")

            if not send_notifications:
                logger.warning("Email notifications disabled")
                continue

            if not user.event_notifications:
                logger.warning("Playlist notifications disabled for user {spotify_user}")
                return

            if spotify_user.email is None:
                logger.warning(f"No email for user {spotify_user}")
                continue

            logger.info(f"Sending email to {spotify_user.email}")
            try:
                await send_email(spotify_user.email, "We've got updates for you", message)
            except Exception as e:
                logger.error(f"Failed to send email to {spotify_user.email}: {e}")
            else:
                # TODO: This will cause other users to not get notifications if they follow the same artists
                logger.info("Marking updates as notified of")
                await EventUpdate.objects.filter(id__in=[u.id for u in all_updates]).aupdate(is_notified_of=True)

    elapsed_time = timeit.default_timer() - start_time
    avg_per_artist = elapsed_time / num_artists
    logger.debug(
        f"Processed {num_artists} artists in {elapsed_time} seconds, "
        f"success: {success_count}, fail: {fail_count}, events: {num_events}, URLs: {num_urls}, "
        f"average {avg_per_artist} seconds per artist"
    )


# def _get_users_with_event_updates() -> QuerySet[SpotifyUser]:
#     return SpotifyUser.objects.filter(~Q(watched_event_artists=None)).prefetch_related(
#         Prefetch(
#             "watched_event_artists",
#             queryset=EventArtist.objects.filter(~Q(events=None)).prefetch_related(
#                 Prefetch(
#                     "events",
#                     queryset=Event.objects.prefetch_related(
#                         Prefetch("updates", queryset=EventUpdate.objects.filter(is_notified_of=False))
#                     ),
#                 )
#             ),
#         )
#     )
