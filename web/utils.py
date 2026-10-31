import asyncio
import itertools
import logging
from collections import Counter
from contextlib import contextmanager
from functools import partial
from typing import TYPE_CHECKING, Any

from django.conf import settings
from tekore.model import (
    FullAlbum,
    FullArtist,
    FullArtistOffsetPaging,
    FullPlaylist,
    FullPlaylistTrack,
    FullTrack,
    Model,
    PlaylistTrack,
    PrivateUser,
    SavedTrack,
    SimpleAlbum,
    SimplePlaylist,
    SimplePlaylistPaging,
    SimpleTrack,
)

from .spotify import get_client

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator, Iterable

logger = logging.getLogger(__name__)


class MottleException(Exception):
    pass


@contextmanager
def raising_mottle_exception(message: str) -> Generator:
    """Raise any exception the block raises as a MottleException with the message, from the original."""
    try:
        yield
    except Exception as e:
        raise MottleException(message) from e


class MottleSpotifyClient:
    def __init__(self, access_token: str, http_timeout: int = settings.TEKORE_HTTP_TIMEOUT) -> None:
        self.spotify_client = get_client(access_token, http_timeout=http_timeout)

    async def get_current_user(self) -> PrivateUser:
        with raising_mottle_exception("Failed to get current user"):
            return await self.spotify_client.current_user()

    async def get_current_user_saved_tracks(self) -> list[SavedTrack]:
        func = partial(self.spotify_client.saved_tracks)
        return await get_all_offset_paging_items(func)

    async def remove_user_saved_tracks(self, track_ids: list[str]) -> None:
        with raising_mottle_exception("Failed to remove saved tracks"):
            await self.spotify_client.saved_tracks_delete(track_ids)

    async def find_artists_and_playlists(self, query: str) -> tuple[list[FullArtist], list[SimplePlaylist]]:
        with raising_mottle_exception(f"Failed to search for artists and playlists with query {query}"):
            results: tuple[FullArtistOffsetPaging, SimplePlaylistPaging] = await self.spotify_client.search(
                query, types=("artist", "playlist")
            )

        return results[0].items, [p for p in results[1].items if p is not None]

    async def get_artists(self, artist_ids: list[str]) -> list[FullArtist]:
        with raising_mottle_exception("Failed to get artists"), self.spotify_client.chunked(on=False):
            return await get_all_chunked(self.spotify_client.artists, artist_ids, chunk_size=50)

    async def get_artist(self, artist_id: str) -> FullArtist:
        with raising_mottle_exception(f"Failed to get artist {artist_id}"):
            return await self.spotify_client.artist(artist_id)

    async def get_artist_albums(self, artist_id: str, album_types: list[str] | None = None) -> list[SimpleAlbum]:
        func = partial(
            self.spotify_client.artist_albums,
            artist_id=artist_id,
            include_groups=album_types or ["album", "single", "compilation"],
        )
        return await get_all_offset_paging_items(func)

    # https://community.spotify.com/t5/Spotify-for-Developers/Get-Artist-s-Albums-API-not-returning-all-results-for-certain/td-p/5961890
    async def get_artist_albums_separately_by_type(
        self, artist_id: str, album_types: list[str] | None = None
    ) -> list[SimpleAlbum]:
        calls = [
            self.get_artist_albums(artist_id, [album_type])
            for album_type in album_types or ["album", "single", "compilation"]
        ]

        with raising_mottle_exception("Failed to get artist albums"):
            albums = await asyncio.gather(*calls)

        return list(itertools.chain(*albums))

    async def get_album(self, album_id: str) -> FullAlbum:
        with raising_mottle_exception("Failed to get album"):
            return await self.spotify_client.album(album_id)

    async def get_albums(self, album_ids: list[str]) -> list[FullAlbum]:
        albums: list[FullAlbum] = await get_all_chunked(self.spotify_client.albums, album_ids)
        return albums

    async def get_tracks(self, track_ids: list[str]) -> list[FullTrack]:
        with raising_mottle_exception("Failed to get tracks"), self.spotify_client.chunked(on=False):
            return await get_all_chunked(self.spotify_client.tracks, track_ids, chunk_size=50)

    async def get_album_tracks(self, album_id: str) -> list[SimpleTrack]:
        func = partial(self.spotify_client.album_tracks, album_id)
        return await get_all_offset_paging_items(func)

    async def get_tracks_in_albums(self, album_ids: list[str]) -> list[SimpleTrack]:
        calls = [self.get_album_tracks(album_id) for album_id in album_ids]

        with raising_mottle_exception("Failed to get tracks"):
            album_tracks = await asyncio.gather(*calls)

        return list(itertools.chain(*album_tracks))

    async def add_tracks_to_playlist(
        self, playlist_id: str, track_uris: Iterable[str], position: int | None = None
    ) -> None:
        with raising_mottle_exception(f"Failed to add tracks to playlist {playlist_id}"):
            await self.spotify_client.playlist_add(playlist_id, track_uris, position)

    async def remove_tracks_from_playlist(self, playlist_id: str, track_uris: list[str]) -> None:
        with raising_mottle_exception(f"Failed to remove tracks from playlist {playlist_id}"):
            await self.spotify_client.playlist_remove(playlist_id, track_uris)

    async def create_playlist(self, current_user_id: str, name: str, is_public: bool = True) -> FullPlaylist:
        with raising_mottle_exception("Failed to create playlist"):
            return await self.spotify_client.playlist_create(user_id=current_user_id, name=name, public=is_public)

    async def upload_playlist_cover_image(self, playlist_id: str, cover_image: bytes | str) -> None:
        with raising_mottle_exception("Failed to upload cover image to playlist"):
            await self.spotify_client.playlist_cover_image_upload(playlist_id, cover_image)

    async def create_playlist_with_tracks(
        self, current_user_id: str, name: str, track_uris: list[str], is_public: bool = True
    ) -> FullPlaylist:
        if not track_uris:
            raise MottleException("No tracks to add to playlist")

        playlist = await self.create_playlist(current_user_id, name, is_public)
        await self.add_tracks_to_playlist(playlist.id, track_uris)
        return playlist

    async def get_current_user_playlists(self) -> list[SimplePlaylist]:
        return await get_all_offset_paging_items(self.spotify_client.followed_playlists)

    async def get_current_user_followed_artists(self) -> list[FullArtist]:
        with raising_mottle_exception("Failed to get followed artists"):
            first_page = await self.spotify_client.followed_artists()
            # tekore types all_items as a sync generator; an async client returns an async one
            return [a async for a in self.spotify_client.all_items(first_page)]  # ty: ignore[not-iterable]

    async def get_playlist(self, playlist_id: str) -> FullPlaylist:
        with raising_mottle_exception(f"Failed to get playlist {playlist_id}"):
            return await self.spotify_client.playlist(playlist_id)

    async def change_playlist_details(
        self,
        playlist_id: str,
        name: str | None = None,
        public: bool | None = None,
        collaborative: bool | None = None,
        description: str | None = None,
    ) -> None:
        with raising_mottle_exception(f"Failed to change playlist details {playlist_id}"):
            await self.spotify_client.playlist_change_details(
                playlist_id,
                name=name,
                public=public,
                collaborative=collaborative,
                description=description,
            )

    async def follow_playlist(self, playlist_id: str) -> None:
        with raising_mottle_exception(f"Failed to follow playlist {playlist_id}"):
            await self.spotify_client.playlist_follow(playlist_id)

    async def unfollow_playlist(self, playlist_id: str) -> None:
        with raising_mottle_exception(f"Failed to unfollow playlist {playlist_id}"):
            await self.spotify_client.playlist_unfollow(playlist_id)

    async def get_playlist_tracks(self, playlist_id: str) -> list[PlaylistTrack]:
        func = partial(self.spotify_client.playlist_items, playlist_id)
        playlist_tracks = await get_all_offset_paging_items(func)
        return [item for item in playlist_tracks if isinstance(item.track, FullPlaylistTrack)]

    async def find_duplicate_tracks_in_playlist(self, playlist_id: str) -> list[tuple[FullPlaylistTrack, int]]:
        # TODO: This implementation is quite awful
        playlist_tracks = await self.get_playlist_tracks(playlist_id)
        tracks = [item.track for item in playlist_tracks if isinstance(item.track, FullPlaylistTrack)]

        counter = Counter([track.id for track in tracks])
        duplicates = {track_id: count for track_id, count in counter.items() if count > 1}

        d = []
        seen_ids = set()
        for track in tracks:
            if track.id in seen_ids:
                continue
            if track.id in duplicates:
                d.append((track, duplicates[track.id]))

            seen_ids.add(track.id)

        return d


def get_qualname(func: Callable) -> str:
    if isinstance(func, partial):
        func = func.func

    return getattr(func, "__qualname__", repr(func))


async def perform_parallel_chunked_requests(func: Callable, items: list[str], chunk_size: int = 100) -> Any:
    chunks = itertools.batched(items, chunk_size, strict=False)
    calls = [func(chunk) for chunk in chunks]
    qualname = get_qualname(func)
    logger.debug(f"Paralellizing {qualname} into {len(calls)} chunked calls")

    with raising_mottle_exception("Failed to perform parallel chunked requests"):
        return await asyncio.gather(*calls)


async def get_all_chunked(func: Callable, items: list[str], chunk_size: int = 100) -> list:
    results: list[list[Model]] = await perform_parallel_chunked_requests(func, items, chunk_size)
    return list(itertools.chain(*results))


async def get_all_offset_paging_items(func: Callable) -> list[Any]:
    items: list[Any] = []

    with raising_mottle_exception("Failed to get items"):
        paging = await func()

    paging_total = paging[0].total if isinstance(paging, tuple) else paging.total
    page_size = paging[0].limit if isinstance(paging, tuple) else paging.limit
    first_page_items: list[Model] = paging[0].items if isinstance(paging, tuple) else paging.items

    logger.debug(f"Total items: {paging_total}, page size: {page_size}, items on first page: {len(first_page_items)}")

    # TODO: Handle this case in the template
    if not paging_total or not page_size:
        return items

    # This is the case with getting artist albums. Filtering on album type still seems to return the unfiltered total of
    # all album types which gives the following:
    # Total items: 93, page size: 50, items on first page: 11
    if len(first_page_items) < page_size:
        return first_page_items

    calls = [func(offset=offset) for offset in range(page_size, paging_total, page_size)]
    qualname = get_qualname(func)
    logger.debug(f"Paralellizing {qualname} into {len(calls) + 1} calls")

    with raising_mottle_exception("Failed to get items"):
        pages = [paging] + await asyncio.gather(*calls)

    for page in pages:
        items.extend(page[0].items if isinstance(page, tuple) else page.items)

    return items


# https://stackoverflow.com/a/61478547
async def gather_with_concurrency(
    concurrency: int, *coroutines: Awaitable[Any], return_exceptions: bool = False
) -> list[Any]:
    semaphore = asyncio.Semaphore(concurrency)

    async def sem_coro(coro: Awaitable[Any]) -> Any:
        async with semaphore:
            return await coro

    return await asyncio.gather(*(sem_coro(c) for c in coroutines), return_exceptions=return_exceptions)
