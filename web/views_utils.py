import logging
import re
from typing import Any, Collection, Optional
from urllib.parse import unquote

from asgiref.sync import sync_to_async
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse

from web.middleware import MottleHttpRequest

from .models import Artist, Playlist
from .templatetags.tekore_model_extras import largest_image, smallest_image, spotify_url
from .utils import MottleException, MottleSpotifyClient

logger = logging.getLogger(__name__)


UNDEFINED = "UNDEFINED"


class SpotifyEntityMetadata:
    def __init__(self, request: MottleHttpRequest, entity_id: str):
        self._spotify_client = request.spotify_client
        self._entity_id = entity_id
        self._entity_name = camel_to_snake(self.__class__.__name__).replace("_metadata", "")
        self._headers = request.headers
        self._is_data_fetched = False
        self._header_prefix = f"M-{self._entity_name}"

        self._get_common_attrs()

    @property
    async def name(self) -> str:
        if self._name == UNDEFINED:
            await self._fetch_data()
        return unquote(self._name)

    @property
    async def spotify_url(self) -> str:
        if self._spotify_url == UNDEFINED:
            await self._fetch_data()
        return unquote(self._spotify_url)

    @property
    async def image_url(self) -> Optional[str]:
        if self._image_url == UNDEFINED:
            await self._fetch_data()
        return None if self._image_url is None else unquote(self._image_url)

    async def _fetch_data(self) -> None:
        logger.warning(f"Fetching data for {self._entity_name} {self._entity_id}")

        if not self._is_data_fetched:
            try:
                spotify_client_method = getattr(self._spotify_client, f"get_{self._entity_name}")
            except AttributeError:
                raise MottleException(f"Method get_{self._entity_name} not found in Spotify client")

            entity = await spotify_client_method(self._entity_id)

            self._name = entity.name
            self._spotify_url = spotify_url(entity)
            self._image_url = largest_image(entity.images)

            self._fetch_additional_data(entity)

            self._is_data_fetched = True

    def _fetch_additional_data(self, entity: Any) -> None:
        pass

    def _get_attr_from_header(self, attr_name: str) -> str:
        return self._headers.get(f"{self._header_prefix}-{attr_name}", UNDEFINED)

    def _get_common_attrs(self) -> None:
        self._name = self._get_attr_from_header("Name")
        self._spotify_url = self._get_attr_from_header("Spotifyurl")
        self._image_url = self._get_attr_from_header("Imageurl")


class PlaylistMetadata(SpotifyEntityMetadata):
    def __init__(self, request: MottleHttpRequest, playlist_id: str):
        super().__init__(request, playlist_id)

        self._owner_id = self._get_attr_from_header("Ownerid")
        self._snapshot_id = self._get_attr_from_header("Snapshotid")

    @property
    async def owner_id(self) -> str:
        if self._owner_id == UNDEFINED:
            await self._fetch_data()
        return unquote(self._owner_id)

    @property
    async def snapshot_id(self) -> str:
        if self._snapshot_id == UNDEFINED:
            await self._fetch_data()
        return unquote(self._snapshot_id)

    def _fetch_additional_data(self, playlist: Any) -> None:
        self._owner_id = playlist.owner.id
        self._snapshot_id = playlist.snapshot_id


class ArtistMetadata(SpotifyEntityMetadata):
    pass


class AlbumMetadata(SpotifyEntityMetadata):
    def __init__(self, request: MottleHttpRequest, album_id: str):
        super().__init__(request, album_id)

        self._track_image_url: Optional[str] = self._get_attr_from_header("Trackimageurl")

    @property
    async def track_image_url(self) -> Optional[str]:
        if self._track_image_url == UNDEFINED:
            await self._fetch_data()
        return None if self._track_image_url is None else unquote(self._track_image_url)

    def _fetch_additional_data(self, album: Any) -> None:
        self._track_image_url = smallest_image(album.images)


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


async def compile_email(
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
            watched_playlist: Optional[Playlist] = await sync_to_async(lambda: update["update"].source_playlist)()
            watched_artist: Optional[Artist] = await sync_to_async(lambda: update["update"].source_artist)()

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
