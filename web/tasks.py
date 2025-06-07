import asyncio
import logging
import timeit
from functools import partial

from asgiref.sync import async_to_sync, sync_to_async
from django.core.exceptions import ObjectDoesNotExist
from sentry_sdk import capture_exception

from .events.data import EventSourceArtist, MusicBrainzArtist
from .events.enums import ArtistNameMatchAccuracy
from .events.exceptions import MusicBrainzException
from .images import create_cover_image
from .models import Artist, EventArtist, SpotifyAuth
from .utils import MottleException, MottleSpotifyClient

logger = logging.getLogger(__name__)


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

    if (
        musicbrainz_artist is not None
        and fetched_artist is not None
        and fetched_artist.songkick_url is not None
        and fetched_artist.bandsintown_url is not None
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
    concurrent_execution: bool = False,
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
    concurrent_execution: bool = False,
) -> None:
    asyncio.run(
        atrack_artists_events(
            artists_data, spotify_user_id, force_reevaluate=force_reevaluate, concurrent_execution=concurrent_execution
        )
    )
