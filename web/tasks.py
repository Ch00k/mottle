import asyncio
import datetime
import itertools
import logging
import timeit
from collections import defaultdict
from functools import partial
from typing import Any

from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.gis.measure import Distance
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from sentry_sdk import capture_exception

from featureflags.data import FeatureFlag
from web.metrics import TASK_RUNTIME_SECONDS

from .email import send_email
from .events.data import EventSourceArtist, MusicBrainzArtist
from .events.enums import ArtistNameMatchAccuracy, EventType
from .events.exceptions import MusicBrainzException
from .images import create_cover_image
from .models import Artist, EventArtist, EventUpdate, Playlist, PlaylistUpdate, SpotifyAuth, SpotifyUser
from .spotify import get_client_token
from .utils import MottleException, MottleSpotifyClient, gather_with_concurrency
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


async def acheck_playlists_for_updates(send_notifications: bool = False) -> None:
    logger.info("Checking playlists for updates")

    async for user in SpotifyUser.objects.filter(~Q(playlists=None)):
        await check_user_playlists_for_updates(user, send_notifications)


def check_playlists_for_updates(send_notifications: bool = False) -> None:
    with TASK_RUNTIME_SECONDS.labels("get_playlist_updates").time():
        asyncio.run(acheck_playlists_for_updates(send_notifications=send_notifications))


async def acheck_artists_for_event_updates(
    artist_spotify_ids: list[str] | None = None,
    compile_notifications: bool = True,
    send_notifications: bool = True,
    force_refetch: bool = False,
    concurrent_execution: bool = True,
    concurrency_limit: int | None = None,
) -> None:
    start_time = timeit.default_timer()

    logger.info("Checking artists for event updates")

    if artist_spotify_ids:
        artists = EventArtist.objects.filter(artist__spotify_id__in=artist_spotify_ids).all()
    else:
        artists = EventArtist.objects.all()
    num_artists = await artists.acount()

    success_count = 0
    fail_count = 0
    num_events = 0
    num_urls = 0

    logger.info(f"Artists to process: {num_artists}")

    if concurrent_execution:
        calls = [artist.update_events(force_refetch=force_refetch) async for artist in artists]

        concurrency_limit = concurrency_limit or FeatureFlag.event_fetching_concurrency_limit()

        if concurrency_limit:
            results = await gather_with_concurrency(concurrency_limit, *calls, return_exceptions=True)
        else:
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
        async for artist in artists:
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


def check_artists_for_event_updates(
    artist_spotify_ids: list[str] | None = None,
    compile_notifications: bool = True,
    send_notifications: bool = True,
    force_refetch: bool = False,
    concurrent_execution: bool = True,
    concurrency_limit: int | None = None,
) -> None:
    with TASK_RUNTIME_SECONDS.labels("get_event_updates").time():
        asyncio.run(
            acheck_artists_for_event_updates(
                artist_spotify_ids=artist_spotify_ids,
                compile_notifications=compile_notifications,
                send_notifications=send_notifications,
                force_refetch=force_refetch,
                concurrent_execution=concurrent_execution,
                concurrency_limit=concurrency_limit,
            )
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


def upload_cover_image(
    playlist_title: str, playlist_spotify_id: str, spotify_user_id: str, dump_to_disk: bool = False
) -> None:
    logger.debug(f"Uploading cover image to Spotify playlist {playlist_spotify_id}")

    try:
        spotify_auth = SpotifyAuth.objects.get(spotify_user__id=spotify_user_id)
    except SpotifyAuth.DoesNotExist:
        logger.error(f"SpotifyAuth for user ID {spotify_user_id} does not exist")
        return

    logger.debug(spotify_auth)

    if spotify_auth.access_token is None:
        logger.error(f"{spotify_auth} access_token is None")
        return

    try:
        async_to_sync(spotify_auth.maybe_refresh)
    except MottleException as e:
        logger.error(e)
        return

    spotify_client = MottleSpotifyClient(spotify_auth.access_token)

    image_data = create_cover_image(playlist_title, dump_to_disk=dump_to_disk)
    async_to_sync(partial(spotify_client.upload_playlist_cover_image, playlist_spotify_id, image_data))()


async def find_event_data_sources_at_musicbrainz(
    artist_spotify_id: str,
    artist_name: str,
) -> tuple[MusicBrainzArtist | None, EventSourceArtist]:
    fetched_artist = EventSourceArtist(artist_name)

    try:
        musicbrainz_artist = await MusicBrainzArtist.find(artist_spotify_id, artist_name)
    except MusicBrainzException as e:
        logger.exception(e)
        capture_exception(e)
        return None, fetched_artist

    if musicbrainz_artist is None:
        return None, fetched_artist

    if musicbrainz_artist.songkick_url is not None:
        fetched_artist.songkick_url = musicbrainz_artist.songkick_url
        fetched_artist.songkick_match_accuracy = ArtistNameMatchAccuracy.musicbrainz

    if musicbrainz_artist.bandsintown_url is not None:
        fetched_artist.bandsintown_url = musicbrainz_artist.bandsintown_url
        fetched_artist.bandsintown_match_accuracy = ArtistNameMatchAccuracy.musicbrainz

    return musicbrainz_artist, fetched_artist


async def find_event_data_sources(
    artist_spotify_id: str,
    artist_name: str,
) -> tuple[MusicBrainzArtist | None, EventSourceArtist]:
    musicbrainz_artist, fetched_artist = await find_event_data_sources_at_musicbrainz(artist_spotify_id, artist_name)

    if all(
        (
            musicbrainz_artist,
            fetched_artist,
            fetched_artist.songkick_url,
            fetched_artist.bandsintown_url,
        )
    ):
        return musicbrainz_artist, fetched_artist

    if musicbrainz_artist is None:
        alternative_names = []
        use_advanced_heuristics = True
    else:
        alternative_names = musicbrainz_artist.names
        use_advanced_heuristics = False

    fetched_artist = await EventSourceArtist.find(
        artist_name, alternative_names, use_advanced_heuristics=use_advanced_heuristics
    )
    return musicbrainz_artist, fetched_artist


async def track_artist_events(
    artist_spotify_id: str, artist_name: str, spotify_user_id: str, force_reevaluate: bool = False
) -> EventArtist:
    artist, created = await Artist.objects.aget_or_create(spotify_id=artist_spotify_id)

    if created:
        logger.debug(f"Created artist: {artist}")
    else:
        logger.debug(f"Artist already existed: {artist}")

    try:
        event_artist = await sync_to_async(lambda: artist.event_artist)()  # pyright: ignore
    except ObjectDoesNotExist:
        logger.debug(f"Artist {artist} does not have an EventArtist. Trying to fetch")

        musicbrainz_artist, fetched_artist = await find_event_data_sources(artist_spotify_id, artist_name)
        musicbrainz_id = None if musicbrainz_artist is None else musicbrainz_artist.id

        event_artist = await EventArtist.create_from_fetched_artist(
            artist, musicbrainz_id, fetched_artist, [spotify_user_id]
        )
        logger.debug(f"Created EventArtist: {event_artist}")
    else:
        logger.debug(f"Artist {artist} already has an EventArtist: {artist.event_artist}")  # pyright: ignore
        # https://github.com/typeddjango/django-stubs/issues/997
        await event_artist.watching_users.aadd(spotify_user_id)  # type: ignore  # pyright: ignore

        if force_reevaluate:
            logger.debug(f"Force reevaluating EventArtist {event_artist}")
            musicbrainz_artist, fetched_artist = await find_event_data_sources(artist_spotify_id, artist_name)
            musicbrainz_id = None if musicbrainz_artist is None else musicbrainz_artist.id
            await event_artist.update_from_fetched_artist(musicbrainz_id, fetched_artist)

    return event_artist  # pyright: ignore


async def atrack_artists_events(
    artists_data: dict[str, str],
    spotify_user_id: str,
    force_reevaluate: bool = False,
    concurrent_execution: bool = True,
    concurrency_limit: int | None = None,
) -> None:
    start_time = timeit.default_timer()

    number_of_artists = len(artists_data)
    logger.debug(f"Artists to process: {number_of_artists}")

    success_count = 0
    fail_count = 0
    no_mb = 0
    no_sk = 0
    no_bt = 0

    if concurrent_execution:
        calls = [
            track_artist_events(artist_spotify_id, artist_name, spotify_user_id, force_reevaluate=force_reevaluate)
            for artist_spotify_id, artist_name in artists_data.items()
            if artist_name
        ]

        concurrency_limit = concurrency_limit or FeatureFlag.event_sources_fetching_concurrency_limit()

        if concurrency_limit:
            results = await gather_with_concurrency(concurrency_limit, *calls, return_exceptions=True)
        else:
            results = await asyncio.gather(*calls, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.exception(f"Failed to process artist: {result}")
                fail_count += 1
            elif isinstance(result, EventArtist):
                logger.debug(f"Processed artist: {result}")
                success_count += 1

                if result.musicbrainz_id is None:
                    no_mb += 1
                if result.songkick_url is None:
                    no_sk += 1
                if result.bandsintown_url is None:
                    no_bt += 1
    else:
        for artist_spotify_id, artist_name in artists_data.items():
            if not artist_name:
                logger.error(f"Artist name is empty for Spotify ID {artist_spotify_id}")
                continue

            try:
                event_artist = await track_artist_events(
                    artist_spotify_id, artist_name, spotify_user_id, force_reevaluate=force_reevaluate
                )
            except Exception as e:
                logger.exception(f"Failed to process artist {artist_name} (Spotify ID {artist_spotify_id}): {e}")
                fail_count += 1
            else:
                success_count += 1

                if event_artist.musicbrainz_id is None:
                    no_mb += 1
                if event_artist.songkick_url is None:
                    no_sk += 1
                if event_artist.bandsintown_url is None:
                    no_bt += 1

    elapsed_time = timeit.default_timer() - start_time
    avg_per_artist = elapsed_time / number_of_artists
    logger.debug(
        f"Processed {number_of_artists} artists in {elapsed_time} seconds, "
        f"success: {success_count}, fail: {fail_count}, no MB: {no_mb}, no SK: {no_sk}, no BT: {no_bt}, "
        f"average {avg_per_artist} seconds per artist"
    )


def track_artists_events(
    artists_data: dict[str, str],
    spotify_user_id: str,
    force_reevaluate: bool = False,
    concurrent_execution: bool = True,
    concurrency_limit: int | None = None,
) -> None:
    with TASK_RUNTIME_SECONDS.labels("track_artists_events").time():
        asyncio.run(
            atrack_artists_events(
                artists_data,
                spotify_user_id,
                force_reevaluate=force_reevaluate,
                concurrent_execution=concurrent_execution,
                concurrency_limit=concurrency_limit,
            )
        )
