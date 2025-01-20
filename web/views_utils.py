import inspect
import logging
import re
from collections.abc import Callable, Collection
from typing import Any
from urllib.parse import unquote

import sentry_sdk
from asgiref.sync import sync_to_async
from django.http import HttpResponse, HttpResponseServerError
from django.shortcuts import render
from django.urls import reverse
from django_htmx.http import trigger_client_event

from .events.enums import EventType
from .middleware import MottleHttpRequest
from .models import Artist, EventArtist, EventUpdate, Playlist
from .templatetags.tekore_model_extras import get_largest_image, get_smallest_image, get_spotify_url
from .utils import MottleException, MottleSpotifyClient

logger = logging.getLogger(__name__)


UNDEFINED = "UNDEFINED"


class SpotifyEntityMetadata:
    def __init__(self, request: MottleHttpRequest, entity_id: str):
        self._id = entity_id
        self._spotify_client = request.spotify_client
        self._entity_name = camel_to_snake(self.__class__.__name__).replace("_metadata", "")
        self._headers = request.headers
        self._is_data_fetched = False
        self._header_prefix = f"M-{self._entity_name}"

        self._get_common_attrs()

    @property
    async def id(self) -> str:
        return self._id

    @property
    async def name(self) -> str:
        if self._name == UNDEFINED:
            logger.warning(f"Name of {self._entity_name} {self._id} is UNDEFINED")
            await self._fetch_data()
        return unquote(self._name)

    @property
    async def url(self) -> str:
        if self._url == UNDEFINED:
            logger.warning(f"Spotify URL of {self._entity_name} {self._id} is UNDEFINED")
            await self._fetch_data()
        return unquote(self._url)

    @property
    async def image_url_small(self) -> str | None:
        if self._image_url_small == UNDEFINED:
            logger.warning(f"Image URL (small) of {self._entity_name} {self._id} is UNDEFINED")
            await self._fetch_data()
        return None if self._image_url_small is None else unquote(self._image_url_small)

    @property
    async def image_url_large(self) -> str | None:
        if self._image_url_large == UNDEFINED:
            logger.warning(f"Image URL (large) of {self._entity_name} {self._id} is UNDEFINED")
            await self._fetch_data()
        return None if self._image_url_large is None else unquote(self._image_url_large)

    async def _fetch_data(self) -> None:
        logger.warning(f"Fetching data for {self._entity_name} {self._id}")

        if not self._is_data_fetched:
            try:
                spotify_client_method = getattr(self._spotify_client, f"get_{self._entity_name}")
            except AttributeError:
                raise MottleException(f"Method get_{self._entity_name} not found in Spotify client")

            entity = await spotify_client_method(self._id)

            self._name = entity.name
            self._url = get_spotify_url(entity)
            self._image_url_small = get_smallest_image(entity.images)
            self._image_url_large = get_largest_image(entity.images)

            self._fetch_additional_data(entity)

            self._is_data_fetched = True

    def _fetch_additional_data(self, _: Any) -> None:
        pass

    def _get_attr_from_header(self, attr_name: str) -> str:
        return self._headers.get(f"{self._header_prefix}-{attr_name}", UNDEFINED)

    def _get_common_attrs(self) -> None:
        self._name = self._get_attr_from_header("Name")
        self._url = self._get_attr_from_header("Url")
        self._image_url_small = self._get_attr_from_header("Imageurlsmall")
        self._image_url_large = self._get_attr_from_header("Imageurllarge")


class PlaylistMetadata(SpotifyEntityMetadata):
    def __init__(self, request: MottleHttpRequest, playlist_id: str):
        super().__init__(request, playlist_id)

        self._owner_id = self._get_attr_from_header("Ownerid")
        self._owner_name = self._get_attr_from_header("Ownername")
        self._owner_url = self._get_attr_from_header("Ownerurl")
        self._snapshot_id = self._get_attr_from_header("Snapshotid")
        self._num_tracks = self._get_attr_from_header("Numtracks")

    @property
    async def owner_id(self) -> str:
        if self._owner_id == UNDEFINED:
            logger.warning(f"Owner ID of {self._entity_name} {self._id} is UNDEFINED")
            await self._fetch_data()
        return unquote(self._owner_id)

    @property
    async def owner_name(self) -> str:
        if self._owner_name == UNDEFINED:
            logger.warning(f"Owner name of {self._entity_name} {self._id} is UNDEFINED")
            await self._fetch_data()
        return unquote(self._owner_name)

    @property
    async def owner_url(self) -> str:
        if self._owner_url == UNDEFINED:
            logger.warning(f"Owner URL of {self._entity_name} {self._id} is UNDEFINED")
            await self._fetch_data()
        return unquote(self._owner_url)

    @property
    async def snapshot_id(self) -> str:
        if self._snapshot_id == UNDEFINED:
            logger.warning(f"Snapshot ID of {self._entity_name} {self._id} is UNDEFINED")
            await self._fetch_data()
        return unquote(self._snapshot_id)

    @property
    async def num_tracks(self) -> int:
        if self._num_tracks == UNDEFINED:
            logger.warning(f"Number of tracks of {self._entity_name} {self._id} is UNDEFINED")
            await self._fetch_data()
        return int(self._num_tracks)

    def _fetch_additional_data(self, playlist: Any) -> None:
        self._owner_id = playlist.owner.id
        self._owner_name = playlist.owner.display_name
        self._owner_url = get_spotify_url(playlist.owner)
        self._snapshot_id = playlist.snapshot_id
        self._num_tracks = playlist.tracks.total


class ArtistMetadata(SpotifyEntityMetadata):
    pass


class AlbumMetadata(SpotifyEntityMetadata):
    def __init__(self, request: MottleHttpRequest, album_id: str):
        super().__init__(request, album_id)

        self._track_image_url: str | None = self._get_attr_from_header("Trackimageurl")

    @property
    async def track_image_url(self) -> str | None:
        if self._track_image_url == UNDEFINED:
            logger.warning(f"Track image URL of {self._entity_name} {self._id} is UNDEFINED")
            await self._fetch_data()
        return None if self._track_image_url is None else unquote(self._track_image_url)

    def _fetch_additional_data(self, album: Any) -> None:
        self._track_image_url = get_smallest_image(album.images)


def get_duplicates_message(items: Collection) -> str:
    num_duplicates = len(items)

    if not num_duplicates:
        message = "No duplicates found"
    elif num_duplicates == 1:
        message = "1 track has duplicates"
    else:
        if num_duplicates % 10 == 1:
            message = f"{len(items)} track has duplicates"
        else:
            message = f"{len(items)} tracks have duplicates"

    return message


async def get_playlist_modal_response(request: MottleHttpRequest, playlist_id: str, template_path: str) -> HttpResponse:
    playlist_metadata = PlaylistMetadata(request, playlist_id)
    playlist_name = await playlist_metadata.name

    try:
        playlists = await request.spotify_client.get_current_user_playlists()
    except MottleException as e:
        logger.exception(e)
        raise

    # TODO: This could probably be made more efficient by filtering playlist inside get_current_user_playlists
    playlists = [
        playlist
        for playlist in playlists
        if playlist.owner.id == request.session["spotify_user_spotify_id"] and playlist.id != playlist_id
    ]
    return render(
        request,
        template_path,
        context={
            "playlists": playlists,
            "playlist_id": playlist_id,
            "playlist_name": playlist_name,
        },
    )


async def compile_event_updates_email(
    updates: dict[EventArtist, list[EventUpdate]], spotify_client: MottleSpotifyClient
) -> str:
    message = ""

    for event_artist, event_updates in updates.items():
        artist_spotify_id = await sync_to_async(lambda: event_artist.artist.spotify_id)()
        spotify_artist = await spotify_client.get_artist(artist_spotify_id)
        artist_name_line = f"Artist: {spotify_artist.name}"
        message += artist_name_line + "\n"
        message += "=" * len(artist_name_line) + "\n"

        for update in event_updates:
            event = update.event
            event_type = " ".join(event.type.split("_"))
            if update.type == EventUpdate.FULL:
                if event.type == EventType.live_stream:
                    stream_urls = "\n".join(event.stream_urls)
                    message += f"New {event_type}: {event.date}\n"
                    if stream_urls:
                        message += "Stream available at:\n"
                        message += stream_urls
                    else:
                        message += "Stream is not available yet"
                    message += "\n"
                else:
                    tickets_urls = "\n".join(event.tickets_urls)
                    message += f"New {event_type}: {event.date} at {event.venue} ({event.city}, {event.country})\n"
                    if tickets_urls:
                        message += "Tickets available at:\n"
                        message += tickets_urls
                    else:
                        message += "Tickets are not available yet"
                    message += "\n"
            else:
                if event.type == EventType.live_stream:
                    if "stream_urls" in update.changes:
                        stream_urls = "\n".join(update.changes["stream_urls"]["new"])
                        message += f"Stream URLs for {event_type} on {event.date} have changed\n"
                        message += "Stream available at:\n"
                        message += stream_urls
                        message += "\n"
                else:
                    if "tickets_urls" in update.changes:
                        tickets_urls = "\n".join(update.changes["tickets_urls"]["new"])
                        message += (
                            f"Tickets URLs for {event_type} on {event.date} at {event.venue} "
                            f"({event.city}, {event.country}) have changed\n"
                        )
                        message += "Tickets available at:\n"
                        message += tickets_urls
                        message += "\n"
            message += "\n"
        # message += "\n"

    return message


async def compile_playlist_updates_email(
    updates: dict[str, list[dict[str, Any]]],
    spotify_client: MottleSpotifyClient,
    num_to_show: int = 10,
) -> str:
    message = ""

    for playlist_spotify_id, playlist_updates in updates.items():
        playlist_data = await spotify_client.get_playlist(playlist_spotify_id)
        playlist_name_line = f"Playlist: {playlist_data.name}"
        message += playlist_name_line + "\n"
        message += "=" * len(playlist_name_line) + "\n"

        for update in playlist_updates:
            watched_playlist: Playlist | None = await sync_to_async(lambda: update["update"].source_playlist)()
            watched_artist: Artist | None = await sync_to_async(lambda: update["update"].source_artist)()

            if watched_playlist is not None:
                watched_playlist_data = await spotify_client.get_playlist(watched_playlist.spotify_id)
                watched_playlist_line = (
                    f"Watched playlist: {watched_playlist_data.name} by {watched_playlist_data.owner.display_name}"
                )
                message += watched_playlist_line + "\n"
                message += "-" * len(watched_playlist_line) + "\n"
                message += "New tracks:\n"

                tracks_data = await spotify_client.get_tracks(update["update"].tracks_added)
                for track_data in tracks_data[: num_to_show - 1]:
                    message += f"{track_data.name} by {', '.join([a.name for a in track_data.artists])}\n"

                if len(tracks_data) > num_to_show:
                    message += f"...and {len(tracks_data) - num_to_show} more\n"
            elif watched_artist is not None:
                watched_artist_data = await spotify_client.get_artist(watched_artist.spotify_id)
                watched_artist_line = f"Watched artist: {watched_artist_data.name}"
                message += watched_artist_line + "\n"
                message += "-" * len(watched_artist_line) + "\n"
                message += "New albums:\n"

                albums_data = await spotify_client.get_albums(update["update"].albums_added)
                for album_data in albums_data[: num_to_show - 1]:
                    message += f"{album_data.name} ({album_data.release_date})\n"

                if len(albums_data) > num_to_show:
                    message += f"...and {len(albums_data) - num_to_show} more\n"

                # TODO: Show tracks from albums?
            else:
                # XXX: This should never happen
                logger.error(f"PlaylistUpdate {update} has no source playlist or artist")
                continue

            message += "\n"

            path = reverse("playlist_updates", args=[playlist_spotify_id])
            if update["auto_acceptable"]:
                if update["auto_accept_successful"]:
                    message += "The update has been auto-accepted\n"
                else:
                    message += (
                        f"The update could not be auto-accepted. Please accept manually at https://mottle.it{path}\n"
                    )
            else:
                message += f"Accept the update at https://mottle.it{path}\n"

            message += "\n"

    return message


def camel_to_snake(string: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", string).lower()


def catch_errors(view_func: Callable) -> Callable:
    def catch_e(e: Exception) -> HttpResponse:
        logger.exception(e)
        sentry_sdk.capture_exception(e)

        return trigger_client_event(
            HttpResponseServerError(),
            "HXToast",
            {"type": "error", "body": f"Error: {e}"},
        )

    async def ainner(*args: Any, **kwargs: Any) -> Any:
        try:
            resp = await view_func(*args, **kwargs)
        except MottleException as e:
            catch_e(e)
        return resp

    def inner(*args: Any, **kwargs: Any) -> Any:
        try:
            resp = view_func(*args, **kwargs)
        except MottleException as e:
            catch_e(e)
        return resp

    if inspect.iscoroutinefunction(view_func):
        return ainner
    else:
        return inner
