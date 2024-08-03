import asyncio
import itertools
import logging
from collections import Counter
from contextlib import contextmanager
from functools import partial
from types import MethodType
from typing import Any, Callable, Generator, Iterable, Optional

from django.conf import settings
from tekore import Spotify
from tekore.model import (
    AlbumType,
    AudioFeatures,
    CurrentlyPlayingContext,
    FullAlbum,
    FullArtist,
    FullArtistOffsetPaging,
    FullPlaylist,
    FullTrack,
    Image,
    Model,
    PlaylistTrack,
    PrivateUser,
    SimpleAlbum,
    SimplePlaylist,
    SimpleTrack,
)

from .spotify import get_client

logger = logging.getLogger(__name__)


class MottleException(Exception):
    pass


class MottleSpotifyClient:
    def __init__(self, access_token: str, http_timeout: int = settings.TEKORE_HTTP_TIMEOUT, is_async: bool = True):
        self.spotify_client = get_client(access_token, http_timeout=http_timeout, async_on=is_async)

    async def get_current_user(self) -> PrivateUser:
        try:
            return await self.spotify_client.current_user()  # pyright: ignore
        except Exception as e:
            raise MottleException(f"Failed to get current user: {e}")

    async def get_current_user_playback(self) -> CurrentlyPlayingContext:
        try:
            return await self.spotify_client.playback()  # pyright: ignore
        except Exception as e:
            raise MottleException(f"Failed to get current user's playback: {e}")

    async def get_artists(self, query: str) -> list[FullArtist]:
        try:
            artists: tuple[FullArtistOffsetPaging] = await self.spotify_client.search(
                query, types=("artist",)
            )  # pyright: ignore
        except Exception as e:
            raise MottleException(f"Failed to search for artists with query {query}: {e}")
        else:
            ret: list[FullArtist] = artists[0].items
            return ret

    async def get_playlists(self, query: str) -> list[SimplePlaylist]:
        func = partial(self.spotify_client.search, query, types=("playlist",))
        return await get_all_offset_paging_items(func)  # pyright: ignore

    async def get_artist(self, artist_id: str) -> FullArtist:
        try:
            artist = await self.spotify_client.artist(artist_id)  # pyright: ignore
            return artist
        except Exception as e:
            raise MottleException(f"Failed to get artist {artist_id}: {e}")

    async def get_artist_albums(self, artist_id: str, album_types: Optional[list[str]] = None) -> list[SimpleAlbum]:
        func = partial(
            self.spotify_client.artist_albums,
            artist_id=artist_id,
            include_groups=album_types or ["album", "single", "compilation"],
        )
        return await get_all_offset_paging_items(func)  # pyright: ignore

    async def get_albums(self, album_ids: list[str]) -> list[FullAlbum]:
        return await self.spotify_client.albums(album_ids)  # pyright: ignore

    async def get_tracks(self, track_ids: list[str]) -> list[FullTrack]:
        try:
            with chunked_off(self.spotify_client):
                return await get_all_chunked(self.spotify_client.tracks, track_ids, chunk_size=50)
        except Exception as e:
            raise MottleException(f"Failed to get tracks: {e}")

    async def get_album_tracks(self, album_id: str) -> list[SimpleTrack]:
        func = partial(self.spotify_client.album_tracks, album_id)
        return await get_all_offset_paging_items(func)  # pyright: ignore

    async def get_tracks_in_albums(self, album_ids: list[str]) -> list[SimpleTrack]:
        tracks = []
        calls = [self.get_album_tracks(album_id) for album_id in album_ids]

        try:
            album_tracks = await asyncio.gather(*calls)
        except Exception as e:
            raise MottleException(f"Failed to get items: {e}")

        for album in album_tracks:
            tracks.extend(album)

        return tracks

    async def add_tracks_to_playlist(
        self, playlist_id: str, track_uris: Iterable[str], position: Optional[int] = None
    ) -> None:
        try:
            await self.spotify_client.playlist_add(playlist_id, track_uris, position)  # pyright: ignore
        except Exception as e:
            raise MottleException(f"Failed to add tracks to playlist {playlist_id}: {e}")

    async def remove_tracks_from_playlist(self, playlist_id: str, track_uris: list[str]) -> None:
        try:
            await self.spotify_client.playlist_remove(playlist_id, track_uris)  # pyright: ignore
        except Exception as e:
            raise MottleException(f"Failed to remove tracks from playlist {playlist_id}: {e}")

    async def remove_tracks_at_positions_from_playlist(
        self, playlist_id: str, tracks: list[dict], playlist_snapshot_id: str
    ) -> None:
        try:
            await self.spotify_client.playlist_remove_occurrences(
                playlist_id, tracks, playlist_snapshot_id
            )  # pyright: ignore
        except Exception as e:
            raise MottleException(f"Failed to remove tracks from playlist {playlist_id}: {e}")

    async def create_playlist(self, current_user_id: str, name: str, is_public: bool = True) -> FullPlaylist:
        try:
            playlist = await self.spotify_client.playlist_create(  # pyright: ignore
                user_id=current_user_id,
                name=name,
                public=is_public,
            )
        except Exception as e:
            raise MottleException(f"Failed to create playlist: {e}")

        return playlist

    async def create_playlist_with_tracks(
        self,
        current_user_id: str,
        name: str,
        track_uris: list[str],
        is_public: bool = True,
        cover_image: Optional[str] = None,
        add_tracks_parallelized: bool = False,
        fail_on_cover_image_upload_error: bool = True,
    ) -> FullPlaylist:
        if not track_uris:
            raise MottleException("No tracks to add to playlist")

        try:
            playlist = await self.spotify_client.playlist_create(  # pyright: ignore
                user_id=current_user_id,
                name=name,
                public=is_public,
            )
        except Exception as e:
            raise MottleException(f"Failed to create playlist: {e}")

        if add_tracks_parallelized:
            try:
                with chunked_off(self.spotify_client):
                    await perform_parallel_chunked_requests(
                        partial(self.add_tracks_to_playlist, playlist.id), track_uris
                    )
            except Exception as e:
                raise MottleException(f"Failed to add tracks to playlist: {e}")
        else:
            try:
                await self.add_tracks_to_playlist(playlist.id, track_uris)
            except Exception as e:
                raise MottleException(f"Failed to add tracks to playlist: {e}")

        if cover_image is not None:
            try:
                await self.spotify_client.playlist_cover_image_upload(playlist.id, cover_image)  # pyright: ignore
            except Exception:
                if fail_on_cover_image_upload_error:
                    raise MottleException(f"Failed to upload cover image to playlist {playlist.id}")
                else:
                    logger.warning(f"Failed to upload cover image to playlist {playlist.id}")

        return playlist

    async def get_current_user_playlists(self) -> list[SimplePlaylist]:
        return await get_all_offset_paging_items(self.spotify_client.followed_playlists)  # pyright: ignore

    async def get_current_user_followed_artists(self) -> list[FullArtist]:
        return await get_all_cursor_paging_items(self.spotify_client.followed_artists)  # pyright: ignore

    async def get_playlist(self, playlist_id: str) -> FullPlaylist:
        try:
            return await self.spotify_client.playlist(playlist_id)  # pyright: ignore
        except Exception as e:
            raise MottleException(f"Failed to get playlist {playlist_id}: {e}")

    async def follow_playlist(self, playlist_id: str) -> None:
        try:
            await self.spotify_client.playlist_follow(playlist_id)  # pyright: ignore
        except Exception as e:
            raise MottleException(f"Failed to follow playlist {playlist_id}: {e}")

    async def unfollow_playlist(self, playlist_id: str) -> None:
        try:
            await self.spotify_client.playlist_unfollow(playlist_id)  # pyright: ignore
        except Exception as e:
            raise MottleException(f"Failed to unfollow playlist {playlist_id}: {e}")

    async def get_playlist_cover_images(self, playlist_id: str) -> list[Image]:
        try:
            images: list[Image] = await self.spotify_client.playlist_cover_image(playlist_id)  # pyright: ignore
        except Exception as e:
            raise MottleException(f"Failed to get playlist cover images {playlist_id}: {e}")

        return images

    async def get_playlist_items(self, playlist_id: str) -> list[PlaylistTrack]:
        func = partial(self.spotify_client.playlist_items, playlist_id)
        return await get_all_offset_paging_items(func)  # pyright: ignore

    async def get_playlist_tracks_audio_features(self, track_ids: list[str]) -> list[AudioFeatures]:
        try:
            with chunked_off(self.spotify_client):
                return await get_all_chunked(self.spotify_client.tracks_audio_features, track_ids)
        except Exception as e:
            raise MottleException(f"Failed to get audio features for tracks: {e}")

    async def find_duplicate_tracks_in_playlist(self, playlist_id: str) -> dict[str, dict]:
        playlist_items = await self.get_playlist_items(playlist_id)
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


async def perform_parallel_requests(func: Callable, items: list[str]) -> Any:
    calls = [func(item) for item in items]
    qualname = func.func.__qualname__ if isinstance(func, partial) else func.__qualname__
    logger.debug(f"Paralellizing {qualname} into {len(calls)} calls")

    try:
        results = await asyncio.gather(*calls)
    except Exception as e:
        raise MottleException(f"Failed to perform parallel requests: {e}")

    return results


async def perform_parallel_chunked_requests(func: Callable, items: list[str], chunk_size: int = 100) -> Any:
    chunks = itertools.batched(items, chunk_size)
    calls = [func(chunk) for chunk in chunks]
    qualname = func.func.__qualname__ if isinstance(func, partial) else func.__qualname__
    logger.debug(f"Paralellizing {qualname} into {len(calls)} chunked calls")

    try:
        results = await asyncio.gather(*calls)
    except Exception as e:
        raise MottleException(f"Failed to perform parallel chunked requests: {e}")

    return results


async def get_all_chunked(func: Callable, items: list[str], chunk_size: int = 100) -> list:
    results: list[list[Model]] = await perform_parallel_chunked_requests(func, items, chunk_size)
    return list(itertools.chain(*results))


async def get_all_offset_paging_items(func: Callable) -> list[Model]:
    items: list[Model] = []

    try:
        paging = await func()
    except Exception as e:
        raise MottleException(f"Failed to get items: {e}")

    paging_total = paging[0].total if isinstance(paging, tuple) else paging.total
    page_size = len(paging[0].items) if isinstance(paging, tuple) else len(paging.items)

    # TODO: Handle this case in the template
    if not paging_total or not page_size:
        return items

    calls = [func(offset=offset) for offset in range(page_size, paging_total, page_size)]
    qualname = func.func.__qualname__ if isinstance(func, partial) else func.__qualname__
    logger.debug(f"Paralellizing {qualname} into {len(calls) + 1} calls")

    try:
        pages = [paging] + await asyncio.gather(*calls)
    except Exception as e:
        raise MottleException(f"Failed to get items: {e}")

    for page in pages:
        items.extend(page[0].items if isinstance(page, tuple) else page.items)

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


@contextmanager
def chunked_off(spotify_client: Spotify) -> Generator:
    if not spotify_client.chunked_on:
        yield

    spotify_client.chunked_on = False
    try:
        yield
    finally:
        spotify_client.chunked_on = True
