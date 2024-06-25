import logging
from typing import Any, Optional
from urllib.parse import unquote

from asgiref.sync import sync_to_async
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse

from .models import Artist, Playlist
from .utils import MottleException, MottleSpotifyClient

logger = logging.getLogger(__name__)


async def get_artist_name(request: HttpRequest, artist_id: str) -> str:
    artist_name = request.headers.get("M-ArtistName")

    if artist_name is None:
        logger.warning("Artist name not found in headers")
        artist = await request.spotify_client.get_artist(artist_id)  # type: ignore[attr-defined]
        artist_name = artist.name
    else:
        artist_name = unquote(artist_name)

    return artist_name


async def get_playlist_name(request: HttpRequest, playlist_id: str) -> str:
    playlist_name = request.headers.get("M-PlaylistName")

    if playlist_name is None:
        logger.warning("Playlist name not found in headers")
        playlist = await request.spotify_client.get_playlist(playlist_id)  # type: ignore[attr-defined]
        playlist_name = playlist.name
    else:
        playlist_name = unquote(playlist_name)

    return playlist_name


async def get_playlist_data(request: HttpRequest, playlist_id: str) -> tuple:
    playlist_name = request.headers.get("M-PlaylistName")
    playlist_owner_id = request.headers.get("M-PlaylistOwnerID")
    playlist_snapshot_id = request.headers.get("M-PlaylistSnapshotID")

    if playlist_name is None or playlist_owner_id is None or playlist_snapshot_id is None:
        logger.warning("Playlist name, owner ID, or snapshot ID not found in headers")

        playlist = await request.spotify_client.get_playlist(playlist_id)  # type: ignore[attr-defined]
        playlist_name = playlist.name
        playlist_owner_id = playlist.owner.id
        playlist_snapshot_id = playlist.snapshot_id
    else:
        playlist_name = unquote(playlist_name)
        playlist_owner_id = unquote(playlist_owner_id)
        playlist_snapshot_id = unquote(playlist_snapshot_id)

    return playlist_name, playlist_owner_id, playlist_snapshot_id


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
    playlist_name = await get_playlist_name(request, playlist_id)

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
