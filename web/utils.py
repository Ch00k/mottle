import asyncio
import logging
from collections import Counter
from functools import partial
from types import MethodType
from typing import Callable, Optional

from django.http import HttpRequest
from tekore import Spotify
from tekore.model import (
    AlbumType,
    FullArtist,
    FullArtistOffsetPaging,
    FullPlaylist,
    Model,
    PlaylistTrack,
    SimpleAlbum,
    SimplePlaylist,
    SimpleTrack,
)

logger = logging.getLogger(__name__)


class MottleException(Exception):
    pass


class HttpRequestWithSpotifyClient(HttpRequest):
    spotify_client: Spotify


async def get_artists(spotify_client: Spotify, query: str) -> list[FullArtist]:
    spotify_client.max_limits_on = False

    try:
        artists: tuple[FullArtistOffsetPaging] = await spotify_client.search(
            query, types=("artist",), limit=20
        )  # pyright: ignore
    except Exception as e:
        raise MottleException(f"Failed to search for artists with query {query}: {e}")
    else:
        ret: list[FullArtist] = artists[0].items
        return ret


async def get_artist(spotify_client: Spotify, artist_id: str) -> FullArtist:
    try:
        artist = await spotify_client.artist(artist_id)  # pyright: ignore
        return artist
    except Exception as e:
        raise MottleException(f"Failed to get artist {artist_id}: {e}")


async def get_artist_albums(
    spotify_client: Spotify, artist_id: str, album_types: Optional[list[str]] = None
) -> list[SimpleAlbum]:
    func = partial(
        spotify_client.artist_albums,
        artist_id=artist_id,
        include_groups=album_types or ["album", "single", "compilation"],
    )
    return await get_all_offset_paging_items(func)  # pyright: ignore


async def get_album_tracks(spotify_client: Spotify, album_id: str) -> list[SimpleTrack]:
    func = partial(spotify_client.album_tracks, album_id)
    return await get_all_offset_paging_items(func)  # pyright: ignore


async def get_tracks_in_albums(spotify_client: Spotify, album_ids: list[str]) -> list[SimpleTrack]:
    tracks = []
    calls = [get_album_tracks(spotify_client, album_id) for album_id in album_ids]

    try:
        album_tracks = await asyncio.gather(*calls)
    except Exception as e:
        raise MottleException(f"Failed to get items: {e}")

    for album in album_tracks:
        tracks.extend(album)

    return tracks


async def add_tracks_to_playlist(
    spotify_client: Spotify, playlist_id: str, track_uris: list[str], position: Optional[int] = None
) -> None:
    try:
        await spotify_client.playlist_add(playlist_id, track_uris, position)  # pyright: ignore
    except Exception as e:
        raise MottleException(f"Failed to add tracks to playlist {playlist_id}: {e}")


async def remove_tracks_from_playlist(spotify_client: Spotify, playlist_id: str, track_uris: list[str]) -> None:
    try:
        await spotify_client.playlist_remove(playlist_id, track_uris)  # pyright: ignore
    except Exception as e:
        raise MottleException(f"Failed to remove tracks from playlist {playlist_id}: {e}")


async def remove_tracks_at_positions_from_playlist(
    spotify_client: Spotify, playlist_id: str, tracks: dict[str, list[int]]
) -> None:
    try:
        await spotify_client.playlist_remove_occurrences(playlist_id, tracks)  # pyright: ignore
    except Exception as e:
        raise MottleException(f"Failed to remove tracks from playlist {playlist_id}: {e}")


async def create_playlist_with_tracks(
    spotify_client: Spotify, name: str, track_uris: list[str], is_public: bool = True
) -> FullPlaylist:
    if not track_uris:
        raise MottleException("No tracks to add to playlist")

    try:
        user = await spotify_client.current_user()  # pyright: ignore
    except Exception as e:
        raise MottleException(f"Failed to get current user: {e}")

    try:
        playlist = await spotify_client.playlist_create(  # pyright: ignore
            user_id=user.id,
            name=name,
            public=is_public,
        )
    except Exception as e:
        raise MottleException(f"Failed to create playlist: {e}")

    await add_tracks_to_playlist(spotify_client, playlist.id, track_uris)

    return playlist


async def get_current_user_playlists(spotify_client: Spotify) -> list[SimplePlaylist]:
    return await get_all_offset_paging_items(spotify_client.followed_playlists)  # pyright: ignore


async def get_current_user_followed_artists(spotify_client: Spotify) -> list[FullArtist]:
    return await get_all_cursor_paging_items(spotify_client.followed_artists)  # pyright: ignore


async def get_playlist(spotify_client: Spotify, playlist_id: str) -> FullPlaylist:
    try:
        return await spotify_client.playlist(playlist_id)  # pyright: ignore
    except Exception as e:
        raise MottleException(f"Failed to get playlist {playlist_id}: {e}")


async def follow_playlist(spotify_client: Spotify, playlist_id: str) -> None:
    try:
        await spotify_client.playlist_follow(playlist_id)  # pyright: ignore
    except Exception as e:
        raise MottleException(f"Failed to follow playlist {playlist_id}: {e}")


async def unfollow_playlist(spotify_client: Spotify, playlist_id: str) -> None:
    try:
        await spotify_client.playlist_unfollow(playlist_id)  # pyright: ignore
    except Exception as e:
        raise MottleException(f"Failed to unfollow playlist {playlist_id}: {e}")


async def get_playlist_items(spotify_client: Spotify, playlist_id: str) -> list[PlaylistTrack]:
    func = partial(spotify_client.playlist_items, playlist_id)
    return await get_all_offset_paging_items(func)  # pyright: ignore


async def find_duplicate_tracks_in_playlist(spotify_client: Spotify, playlist_id: str) -> dict[str, dict]:
    playlist_items = await get_playlist_items(spotify_client, playlist_id)
    counter = Counter(
        [item.track.id for item in playlist_items if item.track is not None and item.track.id is not None]
    )
    duplicates = [track_id for track_id, count in counter.items() if count > 1]

    duplicate_dict: dict[str, dict] = {}

    for index, item in enumerate(playlist_items):
        if item.track is not None and item.track.track and item.track.id in duplicates:
            if item.track.id in duplicate_dict:
                duplicate_dict[item.track.id]["positions"].append(index)
            else:
                duplicate_dict[item.track.id] = {"track": item, "positions": [index]}

    return duplicate_dict


async def get_all_offset_paging_items(func: Callable) -> list[Model]:
    items: list[Model] = []

    try:
        paging = await func()
    except Exception as e:
        raise MottleException(f"Failed to get items: {e}")

    # TODO: Handle this case in the template
    if paging.total == 0:
        return items

    page_size = len(paging.items)

    calls = [func(offset=offset) for offset in range(page_size, paging.total, page_size)]
    logger.debug(f"Paralellized into {len(calls) + 1} calls")

    try:
        pages = [paging] + await asyncio.gather(*calls)
    except Exception as e:
        raise MottleException(f"Failed to get items: {e}")

    for page in pages:
        items.extend(page.items)

    return items


async def get_all_cursor_paging_items(func: Callable) -> list[Model]:
    # TODO: This does not paralellize the calls

    if isinstance(func, MethodType) and isinstance(func.__self__, Spotify):
        spotify_client = func.__self__
    else:
        raise MottleException("func must be a method of Spotify")

    try:
        items = spotify_client.all_items(await func())
    except Exception as e:
        raise MottleException(f"Failed to get items: {e}")

    return [i async for i in items]  # pyright: ignore


def list_has(albums: list[SimpleAlbum], album_type: AlbumType) -> bool:
    return any([album for album in albums if album.album_type == album_type])  # pyright: ignore


def follow_all_artists_in_playlist() -> None:
    pass


def update_discography_playlist() -> None:
    pass
