import asyncio
import itertools
import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Generator, Iterable
from contextlib import contextmanager
from functools import partial
from types import MethodType
from typing import Any

from django.conf import settings
from tekore import Spotify
from tekore.model import (
    AlbumType,
    AudioFeatures,
    FullAlbum,
    FullArtist,
    FullArtistOffsetPaging,
    FullPlaylist,
    FullPlaylistTrack,
    FullTrack,
    Image,
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

logger = logging.getLogger(__name__)


class MottleException(Exception):
    pass


class MottleSpotifyClient:
    def __init__(
        self, access_token: str, http_timeout: int = settings.TEKORE_HTTP_TIMEOUT, is_async: bool = True
    ) -> None:
        self.spotify_client = get_client(access_token, http_timeout=http_timeout, async_on=is_async)

    async def get_current_user(self) -> PrivateUser:
        try:
            return await self.spotify_client.current_user()  # pyright: ignore[reportGeneralTypeIssues]
        except Exception as e:
            raise MottleException("Failed to get current user") from e

    async def get_current_user_saved_tracks(self) -> list[SavedTrack]:
        func = partial(self.spotify_client.saved_tracks)
        return await get_all_offset_paging_items(func)  # pyright: ignore[reportReturnType]

    async def remove_user_saved_tracks(self, track_ids: list[str]) -> None:
        try:
            await self.spotify_client.saved_tracks_delete(track_ids)  # pyright: ignore[reportGeneralTypeIssues]
        except Exception as e:
            raise MottleException("Failed to remove saved tracks") from e

    async def delete_current_user_saved_tracks(self, track_ids: list[str]) -> None:
        try:
            with chunked_off(self.spotify_client):
                await perform_parallel_chunked_requests(
                    self.spotify_client.saved_tracks_delete, track_ids, chunk_size=50
                )
        except Exception as e:
            raise MottleException("Failed to delete saved tracks") from e

    async def find_artists(self, query: str) -> list[FullArtist]:
        try:
            artists: tuple[FullArtistOffsetPaging] = await self.spotify_client.search(query, types=("artist",))  # pyright: ignore[reportGeneralTypeIssues]
        except Exception as e:
            raise MottleException("Failed to search for artists with query {query}") from e
        else:
            ret: list[FullArtist] = artists[0].items
            return ret

    async def find_playlists(self, query: str) -> list[SimplePlaylist]:
        func = partial(self.spotify_client.search, query, types=("playlist",))
        return await get_all_offset_paging_items(func)  # pyright: ignore[reportReturnType]

    async def find_artists_and_playlists(self, query: str) -> tuple[list[FullArtist], list[SimplePlaylist]]:
        try:
            results: tuple[FullArtistOffsetPaging, SimplePlaylistPaging] = await self.spotify_client.search(
                query, types=("artist", "playlist")
            )  # pyright: ignore[reportGeneralTypeIssues]
        except Exception as e:
            raise MottleException("Failed to search for artists and playlists with query {query}") from e
        else:
            ret: tuple[list[FullArtist], list[SimplePlaylist]] = (results[0].items, results[1].items)  # pyright: ignore[reportAssignmentType]
            return ret

    async def get_artists(self, artist_ids: list[str]) -> list[FullArtist]:
        try:
            with chunked_off(self.spotify_client):
                return await get_all_chunked(self.spotify_client.artists, artist_ids, chunk_size=50)
        except Exception as e:
            raise MottleException("Failed to get artists") from e

    async def get_artist(self, artist_id: str) -> FullArtist:
        try:
            return await self.spotify_client.artist(artist_id)  # pyright: ignore[reportGeneralTypeIssues]
        except Exception as e:
            raise MottleException(f"Failed to get artist {artist_id}") from e

    async def get_artist_albums(self, artist_id: str, album_types: list[str] | None = None) -> list[SimpleAlbum]:
        func = partial(
            self.spotify_client.artist_albums,
            artist_id=artist_id,
            include_groups=album_types or ["album", "single", "compilation"],
        )
        return await get_all_offset_paging_items(func)  # pyright: ignore[reportReturnType]

    # https://community.spotify.com/t5/Spotify-for-Developers/Get-Artist-s-Albums-API-not-returning-all-results-for-certain/td-p/5961890
    async def get_artist_albums_separately_by_type(
        self, artist_id: str, album_types: list[str] | None = None
    ) -> list[SimpleAlbum]:
        calls = [
            self.get_artist_albums(artist_id, [album_type])
            for album_type in album_types or ["album", "single", "compilation"]
        ]

        try:
            albums = await asyncio.gather(*calls)
        except Exception as e:
            raise MottleException("Failed to get artist albums") from e

        return list(itertools.chain(*albums))

    async def get_album(self, album_id: str) -> FullAlbum:
        try:
            return await self.spotify_client.album(album_id)  # pyright: ignore[reportGeneralTypeIssues]
        except Exception as e:
            raise MottleException("Failed to get album") from e

    async def get_albums(self, album_ids: list[str]) -> list[FullAlbum]:
        albums: list[FullAlbum] = await get_all_chunked(self.spotify_client.albums, album_ids)
        return albums

    async def get_tracks(self, track_ids: list[str]) -> list[FullTrack]:
        try:
            with chunked_off(self.spotify_client):
                return await get_all_chunked(self.spotify_client.tracks, track_ids, chunk_size=50)
        except Exception as e:
            raise MottleException("Failed to get tracks") from e

    async def get_album_tracks(self, album_id: str) -> list[SimpleTrack]:
        func = partial(self.spotify_client.album_tracks, album_id)
        return await get_all_offset_paging_items(func)  # pyright: ignore[reportReturnType]

    async def get_tracks_in_albums(self, album_ids: list[str]) -> list[SimpleTrack]:
        tracks = []
        calls = [self.get_album_tracks(album_id) for album_id in album_ids]

        try:
            album_tracks = await asyncio.gather(*calls)
        except Exception as e:
            raise MottleException("Failed to get tracks") from e

        for album in album_tracks:
            tracks.extend(album)

        return tracks

    async def add_tracks_to_playlist(
        self, playlist_id: str, track_uris: Iterable[str], position: int | None = None
    ) -> None:
        try:
            await self.spotify_client.playlist_add(playlist_id, track_uris, position)  # pyright: ignore[reportGeneralTypeIssues,reportArgumentType]
        except Exception as e:
            raise MottleException(f"Failed to add tracks to playlist {playlist_id}") from e

    async def remove_tracks_from_playlist(self, playlist_id: str, track_uris: list[str]) -> None:
        try:
            await self.spotify_client.playlist_remove(playlist_id, track_uris)  # pyright: ignore[reportGeneralTypeIssues]
        except Exception as e:
            raise MottleException(f"Failed to remove tracks from playlist {playlist_id}") from e

    # async def remove_tracks_at_positions_from_playlist(
    #     self, playlist_id: str, tracks: list[dict], playlist_snapshot_id: str
    # ) -> None:
    #     try:
    #         await self.spotify_client.playlist_remove_occurrences(
    #             playlist_id, tracks, playlist_snapshot_id
    #         )
    #     except Exception as e:
    #         raise MottleException(f"Failed to remove tracks from playlist {playlist_id}") from e

    async def create_playlist(self, current_user_id: str, name: str, is_public: bool = True) -> FullPlaylist:
        try:
            playlist = await self.spotify_client.playlist_create(  # pyright: ignore[reportGeneralTypeIssues]
                user_id=current_user_id,
                name=name,
                public=is_public,
            )
        except Exception as e:
            raise MottleException("Failed to create playlist") from e

        return playlist

    async def upload_playlist_cover_image(self, playlist_id: str, cover_image: bytes | str) -> None:
        try:
            await self.spotify_client.playlist_cover_image_upload(playlist_id, cover_image)  # pyright: ignore[reportGeneralTypeIssues,reportArgumentType]
        except Exception as e:
            raise MottleException("Failed to upload cover image to playlist") from e

    async def create_playlist_with_tracks(
        self,
        current_user_id: str,
        name: str,
        track_uris: list[str],
        is_public: bool = True,
        cover_image: str | None = None,
        add_tracks_parallelized: bool = False,
        fail_on_cover_image_upload_error: bool = True,
    ) -> FullPlaylist:
        if not track_uris:
            raise MottleException("No tracks to add to playlist")

        try:
            playlist = await self.spotify_client.playlist_create(  # pyright: ignore[reportGeneralTypeIssues]
                user_id=current_user_id,
                name=name,
                public=is_public,
            )
        except Exception as e:
            raise MottleException("Failed to create playlist") from e

        if add_tracks_parallelized:
            try:
                with chunked_off(self.spotify_client):
                    await perform_parallel_chunked_requests(
                        partial(self.add_tracks_to_playlist, playlist.id), track_uris
                    )
            except Exception as e:
                raise MottleException("Failed to add tracks to playlist") from e
        else:
            try:
                await self.add_tracks_to_playlist(playlist.id, track_uris)
            except Exception as e:
                raise MottleException("Failed to add tracks to playlist") from e

        if cover_image is not None:
            try:
                await self.spotify_client.playlist_cover_image_upload(playlist.id, cover_image)  # pyright: ignore[reportGeneralTypeIssues]
            except Exception as e:
                if fail_on_cover_image_upload_error:
                    raise MottleException(f"Failed to upload cover image to playlist {playlist.id}") from e

                logger.error(f"Failed to upload cover image to playlist {playlist.id}: {e}")

        return playlist

    async def get_current_user_playlists(self) -> list[SimplePlaylist]:
        return await get_all_offset_paging_items(self.spotify_client.followed_playlists)  # pyright: ignore[reportReturnType]

    async def get_current_user_followed_artists(self) -> list[FullArtist]:
        return await get_all_cursor_paging_items(self.spotify_client.followed_artists)  # pyright: ignore[reportReturnType]

    async def get_playlist(self, playlist_id: str) -> FullPlaylist:
        try:
            return await self.spotify_client.playlist(playlist_id)  # pyright: ignore[reportGeneralTypeIssues]
        except Exception as e:
            raise MottleException(f"Failed to get playlist {playlist_id}") from e

    async def change_playlist_details(
        self,
        playlist_id: str,
        name: str | None = None,
        public: bool | None = None,
        collaborative: bool | None = None,
        description: str | None = None,
    ) -> None:
        try:
            await self.spotify_client.playlist_change_details(  # pyright: ignore[reportGeneralTypeIssues]
                playlist_id,
                name=name,  # pyright: ignore[reportGeneralTypeIssues]
                public=public,  # pyright: ignore[reportGeneralTypeIssues]
                collaborative=collaborative,  # pyright: ignore[reportGeneralTypeIssues]
                description=description,  # pyright: ignore[reportGeneralTypeIssues]
            )
        except Exception as e:
            raise MottleException(f"Failed to change playlist details {playlist_id}") from e

    async def follow_playlist(self, playlist_id: str) -> None:
        try:
            await self.spotify_client.playlist_follow(playlist_id)  # pyright: ignore[reportGeneralTypeIssues]
        except Exception as e:
            raise MottleException(f"Failed to follow playlist {playlist_id}") from e

    async def unfollow_playlist(self, playlist_id: str) -> None:
        try:
            await self.spotify_client.playlist_unfollow(playlist_id)  # pyright: ignore[reportGeneralTypeIssues]
        except Exception as e:
            raise MottleException(f"Failed to unfollow playlist {playlist_id}") from e

    async def get_playlist_cover_images(self, playlist_id: str) -> list[Image]:
        try:
            images: list[Image] = await self.spotify_client.playlist_cover_image(playlist_id)  # pyright: ignore[reportGeneralTypeIssues]
        except Exception as e:
            raise MottleException(f"Failed to get playlist cover images {playlist_id}") from e

        return images

    async def get_playlist_tracks(self, playlist_id: str) -> list[PlaylistTrack]:
        func = partial(self.spotify_client.playlist_items, playlist_id)
        playlist_tracks = await get_all_offset_paging_items(func)  # pyright: ignore[reportReturnType]
        return [item for item in playlist_tracks if isinstance(item.track, FullPlaylistTrack)]  # pyright: ignore[reportReturnType,reportAttributeAccessIssue]

    async def get_playlist_tracks_audio_features(self, track_ids: list[str]) -> list[AudioFeatures]:
        try:
            with chunked_off(self.spotify_client):
                return await get_all_chunked(self.spotify_client.tracks_audio_features, track_ids)
        except Exception as e:
            raise MottleException("Failed to get audio features for tracks") from e

    async def find_duplicate_tracks_in_playlist(self, playlist_id: str) -> list[tuple[FullPlaylistTrack, int]]:
        # TODO: This implementation is quite awful
        playlist_tracks = await self.get_playlist_tracks(playlist_id)
        playlist_tracks = [item for item in playlist_tracks if isinstance(item.track, FullPlaylistTrack)]

        counter = Counter([item.track.id for item in playlist_tracks])  # pyright: ignore[reportOptionalMemberAccess]  # TODO: Why?
        duplicates = {track_id: count for track_id, count in counter.items() if count > 1}

        d = []
        seen_ids = set()
        for track in playlist_tracks:
            if track.track.id in seen_ids:  # pyright: ignore[reportOptionalMemberAccess]  # TODO: Why?
                continue
            if track.track.id in duplicates:  # pyright: ignore[reportOptionalMemberAccess]  # TODO: Why?
                d.append((track.track, duplicates[track.track.id]))  # pyright: ignore[reportOptionalMemberAccess]  # TODO: Why?)

            seen_ids.add(track.track.id)  # pyright: ignore[reportOptionalMemberAccess]  # TODO: Why?

        return d


async def perform_parallel_requests(func: Callable, items: list[str]) -> Any:
    calls = [func(item) for item in items]
    qualname = func.func.__qualname__ if isinstance(func, partial) else func.__qualname__
    logger.debug(f"Paralellizing {qualname} into {len(calls)} calls")

    try:
        results = await asyncio.gather(*calls)
    except Exception as e:
        raise MottleException("Failed to perform parallel requests") from e

    return results


async def perform_parallel_chunked_requests(func: Callable, items: list[str], chunk_size: int = 100) -> Any:
    chunks = itertools.batched(items, chunk_size)
    calls = [func(chunk) for chunk in chunks]
    qualname = func.func.__qualname__ if isinstance(func, partial) else func.__qualname__
    logger.debug(f"Paralellizing {qualname} into {len(calls)} chunked calls")

    try:
        results = await asyncio.gather(*calls)
    except Exception as e:
        raise MottleException("Failed to perform parallel chunked requests") from e

    return results


async def get_all_chunked(func: Callable, items: list[str], chunk_size: int = 100) -> list:
    results: list[list[Model]] = await perform_parallel_chunked_requests(func, items, chunk_size)
    return list(itertools.chain(*results))


async def get_all_offset_paging_items(func: Callable) -> list[Model]:
    items: list[Model] = []

    try:
        paging = await func()
    except Exception as e:
        raise MottleException("Failed to get items") from e

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
    qualname = func.func.__qualname__ if isinstance(func, partial) else func.__qualname__
    logger.debug(f"Paralellizing {qualname} into {len(calls) + 1} calls")

    try:
        pages = [paging] + await asyncio.gather(*calls)
    except Exception as e:
        raise MottleException("Failed to get items") from e

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
        raise MottleException("Failed to get items") from e

    return [i async for i in items]  # pyright: ignore[reportGeneralTypeIssues]


def list_has(albums: list[SimpleAlbum], album_type: AlbumType) -> bool:
    return any([album for album in albums if album.album_type == album_type])  # noqa: C419 # pyright: ignore[reportGeneralTypeIssues]


@contextmanager
def chunked_off(spotify_client: Spotify) -> Generator:
    if not spotify_client.chunked_on:
        yield

    spotify_client.chunked_on = False
    try:
        yield
    finally:
        spotify_client.chunked_on = True


# https://stackoverflow.com/a/61478547
async def gather_with_concurrency(
    concurrency: int, *coroutines: Awaitable[Any], return_exceptions: bool = False
) -> list[Any]:
    semaphore = asyncio.Semaphore(concurrency)

    async def sem_coro(coro: Awaitable[Any]) -> Any:
        async with semaphore:
            return await coro

    return await asyncio.gather(*(sem_coro(c) for c in coroutines), return_exceptions=return_exceptions)
