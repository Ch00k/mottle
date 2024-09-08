import logging
from typing import Any, Optional
from urllib.parse import unquote

from asgiref.sync import sync_to_async
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse

from .models import Artist, Playlist
from .templatetags.tekore_model_extras import largest_image, smallest_image, spotify_url
from .utils import MottleException, MottleSpotifyClient

logger = logging.getLogger(__name__)


UNDEFINED = "UNDEFINED"


# TODO: Refactor the following two classses to use a common base class
class PlaylistMetadata:
    def __init__(self, request: HttpRequest, playlist_id: str):
        self.spotify_client = request.spotify_client  # type: ignore[attr-defined]
        self.playlist_id = playlist_id

        headers = request.headers
        self._name = headers.get("M-PlaylistName", UNDEFINED)
        self._owner_id = headers.get("M-PlaylistOwnerID", UNDEFINED)
        self._snapshot_id = headers.get("M-PlaylistSnapshotID", UNDEFINED)
        self._spotify_url = headers.get("M-PlaylistSpotifyURL", UNDEFINED)
        self._image_url: Optional[str] = headers.get("M-PlaylistImageURL", UNDEFINED)

        self.playlist_data_fetched = False

    @property
    async def name(self) -> str:
        if self._name == UNDEFINED:
            await self.fetch_playlist_data()
        return unquote(self._name)

    @property
    async def owner_id(self) -> str:
        if self._owner_id == UNDEFINED:
            await self.fetch_playlist_data()
        return unquote(self._owner_id)

    @property
    async def snapshot_id(self) -> str:
        if self._snapshot_id == UNDEFINED:
            await self.fetch_playlist_data()
        return unquote(self._snapshot_id)

    @property
    async def image_url(self) -> Optional[str]:
        if self._image_url == UNDEFINED:
            await self.fetch_playlist_data()
        return None if self._image_url is None else unquote(self._image_url)

    @property
    async def spotify_url(self) -> str:
        if self._spotify_url == UNDEFINED:
            await self.fetch_playlist_data()
        return unquote(self._spotify_url)

    async def fetch_playlist_data(self) -> None:
        if not self.playlist_data_fetched:
            playlist = await self.spotify_client.get_playlist(self.playlist_id)

            self._name = playlist.name
            self._owner_id = playlist.owner.id
            self._snapshot_id = playlist.snapshot_id
            self._spotify_url = spotify_url(playlist)
            self._image_url = largest_image(playlist.images)
            self.playlist_data_fetched = True


class ArtistMetadata:
    def __init__(self, request: HttpRequest, artist_id: str):
        self.spotify_client = request.spotify_client  # type: ignore[attr-defined]
        self.artist_id = artist_id

        headers = request.headers
        self._name = headers.get("M-ArtistName", UNDEFINED)

        self.artist_data_fetched = False

    @property
    async def name(self) -> str:
        if self._name == UNDEFINED:
            await self.fetch_artist_data()
        return unquote(self._name)

    async def fetch_artist_data(self) -> None:
        if not self.artist_data_fetched:
            artist = await self.spotify_client.get_artist(self.artist_id)

            self._name = artist.name
            self.artist_data_fetched = True


class AlbumMetadata:
    def __init__(self, request: HttpRequest, album_id: str):
        self.spotify_client = request.spotify_client  # type: ignore[attr-defined]
        self.album_id = album_id

        headers = request.headers
        self._name = headers.get("M-AlbumName", UNDEFINED)
        self._spotify_url = headers.get("M-AlbumSpotifyURL", UNDEFINED)
        self._image_url: Optional[str] = headers.get("M-AlbumImageURL", UNDEFINED)
        self._track_image_url: Optional[str] = headers.get("M-TrackImageURL", UNDEFINED)

        self.album_data_fetched = False

    @property
    async def name(self) -> str:
        if self._name == UNDEFINED:
            await self.fetch_album_data()
        return unquote(self._name)

    @property
    async def spotify_url(self) -> str:
        if self._spotify_url == UNDEFINED:
            await self.fetch_album_data()
        return unquote(self._spotify_url)

    @property
    async def image_url(self) -> Optional[str]:
        if self._image_url == UNDEFINED:
            await self.fetch_album_data()
        return None if self._image_url is None else unquote(self._image_url)

    @property
    async def track_image_url(self) -> Optional[str]:
        if self._track_image_url == UNDEFINED:
            await self.fetch_album_data()
        return None if self._track_image_url is None else unquote(self._track_image_url)

    async def fetch_album_data(self) -> None:
        if not self.album_data_fetched:
            album = await self.spotify_client.get_album(self.album_id)

            self._name = album.name
            self._image_url = largest_image(album.images)
            self._track_image_url = smallest_image(album.images)
            self.album_data_fetched = True


def get_duplicates_message(items: list) -> str:
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


async def get_playlist_modal_response(request: HttpRequest, playlist_id: str, template_path: str) -> HttpResponse:
    playlist_metadata = PlaylistMetadata(request, playlist_id)
    playlist_name = await playlist_metadata.name

    try:
        playlists = await request.spotify_client.get_current_user_playlists()  # type: ignore[attr-defined]
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
