import inspect
import logging
import re
from collections.abc import Callable
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
    def __init__(self, request: MottleHttpRequest, entity_id: str) -> None:
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
            except AttributeError as e:
                raise MottleException(f"Method get_{self._entity_name} not found in Spotify client") from e

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
    def __init__(self, request: MottleHttpRequest, playlist_id: str) -> None:
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
    def __init__(self, request: MottleHttpRequest, album_id: str) -> None:
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


# def get_duplicates_message(items: Collection) -> str:
#     num_duplicates = len(items)

#     if not num_duplicates:
#         message = "No duplicates found"
#     elif num_duplicates == 1:
#         message = "1 track has duplicates"
#     elif num_duplicates % 10 == 1:
#         message = f"{len(items)} track has duplicates"
#     else:
#         message = f"{len(items)} tracks have duplicates"

#     return message


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
            "playlist_image_url": await playlist_metadata.image_url_small,
        },
    )


async def compile_event_updates_email(
    updates: dict[EventArtist, list[EventUpdate]], spotify_client: MottleSpotifyClient
) -> tuple[str, str]:
    plaintext = ""
    html = '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="UTF-8">\n<title>Event Updates</title>\n</head>\n<body>\n'

    for event_artist, event_updates in updates.items():
        artist_spotify_id = await sync_to_async(lambda: event_artist.artist.spotify_id)()
        spotify_artist = await spotify_client.get_artist(artist_spotify_id)

        artist_name_line = f"{spotify_artist.name}"
        plaintext += artist_name_line + "\n"
        plaintext += "=" * len(artist_name_line) + "\n"
        html += f"<h1>{spotify_artist.name}</h1>\n\n"

        for update in event_updates:
            event = update.event
            event_type = " ".join(event.type.split("_"))
            if update.type == EventUpdate.FULL:
                if event.type == EventType.live_stream:
                    plaintext += f"New {event_type}: {event.date}\n"
                    html += f"<h2>New {event_type}: {event.date}</h2>\n"

                    if event.stream_urls:
                        plaintext += "Stream available at:\n"
                        plaintext += "\n".join(event.stream_urls) + "\n"
                        stream_links = ", ".join([f'<a href="{url}">here</a>' for url in event.stream_urls])
                        html += f"<p>Watch stream {stream_links}</p>\n"
                    else:
                        plaintext += "Stream is not available yet\n"
                        html += "<p>Stream is not available yet</p>\n"
                else:
                    plaintext += f"New {event_type} on {event.date} at {event.venue} ({event.city}, {event.country})\n"
                    html += (
                        f"<h2>New {event_type} on {event.date} at {event.venue} ({event.city}, {event.country})</h2>\n"
                    )

                    if event.tickets_urls:
                        plaintext += "Tickets available at:\n"
                        plaintext += "\n".join(event.tickets_urls) + "\n"
                        tickets_links = ", ".join([f'<a href="{url}">here</a>' for url in event.tickets_urls])
                        html += f"<p>Buy tickets {tickets_links}</p>\n"
                    else:
                        plaintext += "Tickets are not available yet\n"
                        html += "<p>Tickets are not available yet</p>\n"
            elif event.type == EventType.live_stream:
                if "stream_urls" in update.changes:  # type: ignore [operator]  # TODO: WTF!?
                    plaintext += f"Stream URLs for {event_type} on {event.date} have changed\n"
                    plaintext += "Stream available at:\n"
                    plaintext += "\n".join(update.changes["stream_urls"]["new"]) + "\n"  # type: ignore [index]
                    html += f"<h2>Stream URLs for {event_type} on {event.date} have changed</h2>\n"
                    stream_links = ", ".join(
                        [f'<a href="{url}">here</a>' for url in update.changes["stream_urls"]["new"]]  # type: ignore [index]
                    )
                    html += f"<p>Stream available at: {stream_links}</p>\n"
            elif "tickets_urls" in update.changes:  # type: ignore [operator]  # TODO: WTF!?
                plaintext += (
                    f"Tickets URLs for {event_type} on {event.date} at {event.venue} "
                    f"({event.city}, {event.country}) have changed\n"
                )
                plaintext += "Tickets available at:\n"
                plaintext += "\n".join(update.changes["tickets_urls"]["new"]) + "\n"  # type: ignore [index]
                html += (
                    f"<h2>Tickets URLs for {event_type} on {event.date} at {event.venue} "
                    f"({event.city}, {event.country}) have changed</h2>\n"
                )
                tickets_links = ", ".join(
                    [f'<a href="{url}">here</a>' for url in update.changes["tickets_urls"]["new"]]  # type: ignore [index]
                )
                html += f"<p>Tickets available at: {tickets_links}</p>\n"

            plaintext += "\n"
            html += "\n"

        html += "<hr>\n\n"

    html += "</body>\n</html>"
    return plaintext, html


async def compile_playlist_updates_email(
    updates: dict[str, list[dict[str, Any]]],
    spotify_client: MottleSpotifyClient,
    num_to_show: int = 10,
) -> tuple[str, str]:
    plaintext = ""
    html = '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="UTF-8">\n<title>Playlist Updates</title>\n</head>\n<body>\n'

    for playlist_spotify_id, playlist_updates in updates.items():
        playlist_data = await spotify_client.get_playlist(playlist_spotify_id)

        playlist_name_line = f"Playlist: {playlist_data.name}"
        plaintext += playlist_name_line + "\n"
        plaintext += "=" * len(playlist_name_line) + "\n"
        html += f"<h1>Playlist: {playlist_data.name}</h1>\n\n"

        for update in playlist_updates:
            watched_playlist: Playlist | None = await sync_to_async(lambda: update["update"].source_playlist)()
            watched_artist: Artist | None = await sync_to_async(lambda: update["update"].source_artist)()

            if watched_playlist is not None:
                watched_playlist_data = await spotify_client.get_playlist(watched_playlist.spotify_id)

                watched_playlist_line = (
                    f"Watched playlist: {watched_playlist_data.name} by {watched_playlist_data.owner.display_name}"
                )
                plaintext += watched_playlist_line + "\n"
                plaintext += "-" * len(watched_playlist_line) + "\n"
                plaintext += "New tracks:\n"
                html += (
                    f"<h2>Watched playlist: {watched_playlist_data.name} "
                    f"by {watched_playlist_data.owner.display_name}</h2>\n"
                )
                html += "<h3>New tracks:</h3>\n<ul>\n"

                tracks_data = await spotify_client.get_tracks(update["update"].tracks_added)
                for track_data in tracks_data[: num_to_show - 1]:
                    artists_str = ", ".join([a.name for a in track_data.artists])
                    plaintext += f"{track_data.name} by {artists_str}\n"
                    html += f"<li>{track_data.name} by {artists_str}</li>\n"

                if len(tracks_data) > num_to_show:
                    plaintext += f"...and {len(tracks_data) - num_to_show} more\n"
                    html += f"<li>...and {len(tracks_data) - num_to_show} more</li>\n"
                html += "</ul>\n"
            elif watched_artist is not None:
                watched_artist_data = await spotify_client.get_artist(watched_artist.spotify_id)

                watched_artist_line = f"Watched artist: {watched_artist_data.name}"
                plaintext += watched_artist_line + "\n"
                plaintext += "-" * len(watched_artist_line) + "\n"
                plaintext += "New albums:\n"
                html += f"<h2>Watched artist: {watched_artist_data.name}</h2>\n"
                html += "<h3>New albums:</h3>\n<ul>\n"

                albums_data = await spotify_client.get_albums(update["update"].albums_added)
                for album_data in albums_data[: num_to_show - 1]:
                    plaintext += f"{album_data.name} ({album_data.release_date})\n"
                    html += f"<li>{album_data.name} ({album_data.release_date})</li>\n"

                if len(albums_data) > num_to_show:
                    plaintext += f"...and {len(albums_data) - num_to_show} more\n"
                    html += f"<li>...and {len(albums_data) - num_to_show} more</li>\n"
                html += "</ul>\n"

                # TODO: Show tracks from albums?
            else:
                # XXX: This should never happen
                logger.error(f"PlaylistUpdate {update} has no source playlist or artist")
                continue

            plaintext += "\n"

            path = reverse("playlist_updates", args=[playlist_spotify_id])
            if update["auto_acceptable"]:
                if update["auto_accept_successful"]:
                    plaintext += "The update has been auto-accepted\n"
                    html += "<p>The update has been auto-accepted</p>\n"
                else:
                    plaintext += (
                        f"The update could not be auto-accepted. Please accept manually at https://mottle.it{path}\n"
                    )
                    html += (
                        f"<p>The update could not be auto-accepted. Please accept manually "
                        f'<a href="https://mottle.it{path}">here</a></p>\n'
                    )
            else:
                plaintext += f"Accept the update at https://mottle.it{path}\n"
                html += f'<p>Accept the update <a href="https://mottle.it{path}">here</a></p>\n'

            plaintext += "\n"
            html += "\n<hr>\n\n"

    html += "</body>\n</html>"
    return plaintext, html


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
            return catch_e(e)
        return resp

    def inner(*args: Any, **kwargs: Any) -> Any:
        try:
            resp = view_func(*args, **kwargs)
        except MottleException as e:
            return catch_e(e)
        return resp

    if inspect.iscoroutinefunction(view_func):
        return ainner
    return inner
