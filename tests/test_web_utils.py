import asyncio
import json
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from tekore import AsyncSender, Spotify
from tekore.model import AlbumType, SimpleAlbum

from web.utils import (
    MottleException,
    MottleSpotifyClient,
    chunked_off,
    gather_with_concurrency,
    get_all_chunked,
    get_qualname,
    list_has,
    perform_parallel_chunked_requests,
    perform_parallel_requests,
)

DATA_DIR = Path(__file__).parent / "data"


class TestListHas:
    def test_returns_true_when_album_type_exists(self) -> None:
        albums = [
            Mock(spec=SimpleAlbum, album_type=AlbumType.album),
            Mock(spec=SimpleAlbum, album_type=AlbumType.single),
        ]
        assert list_has(albums, AlbumType.album) is True

    def test_returns_true_when_album_type_exists_multiple_times(self) -> None:
        albums = [
            Mock(spec=SimpleAlbum, album_type=AlbumType.album),
            Mock(spec=SimpleAlbum, album_type=AlbumType.album),
            Mock(spec=SimpleAlbum, album_type=AlbumType.single),
        ]
        assert list_has(albums, AlbumType.album) is True

    def test_returns_false_when_album_type_does_not_exist(self) -> None:
        albums = [
            Mock(spec=SimpleAlbum, album_type=AlbumType.album),
            Mock(spec=SimpleAlbum, album_type=AlbumType.single),
        ]
        assert list_has(albums, AlbumType.compilation) is False

    def test_returns_false_when_list_is_empty(self) -> None:
        albums: list[SimpleAlbum] = []
        assert list_has(albums, AlbumType.album) is False


class TestChunkedOff:
    def test_disables_and_reenables_chunking(self) -> None:
        client = Mock(spec=Spotify)
        client.chunked_on = True

        with chunked_off(client):
            assert client.chunked_on is False

        assert client.chunked_on is True

    def test_reenables_chunking_even_on_exception(self) -> None:
        client = Mock(spec=Spotify)
        client.chunked_on = True

        with chunked_off(client):
            assert client.chunked_on is False
            with pytest.raises(ValueError, match="test error"):
                raise ValueError("test error")

        assert client.chunked_on is True

    def test_does_nothing_when_chunking_already_disabled(self) -> None:
        client = Mock(spec=Spotify)
        client.chunked_on = False

        with chunked_off(client):
            assert client.chunked_on is False

        assert client.chunked_on is False


@pytest.mark.asyncio
class TestGatherWithConcurrency:
    async def test_executes_all_coroutines(self) -> None:
        async def coro(value: int) -> int:
            return value * 2

        results = await gather_with_concurrency(3, coro(1), coro(2), coro(3))
        assert results == [2, 4, 6]

    async def test_limits_concurrency(self) -> None:
        max_concurrent = 0
        current_concurrent = 0

        async def coro(value: int) -> int:
            nonlocal max_concurrent, current_concurrent
            current_concurrent += 1
            max_concurrent = max(max_concurrent, current_concurrent)
            await asyncio.sleep(0.01)
            current_concurrent -= 1
            return value

        await gather_with_concurrency(2, *[coro(i) for i in range(10)])
        assert max_concurrent <= 2

    async def test_handles_exceptions_when_return_exceptions_true(self) -> None:
        async def success() -> int:
            return 42

        async def failure() -> None:
            raise ValueError("error")

        results = await gather_with_concurrency(2, success(), failure(), return_exceptions=True)
        assert results[0] == 42
        assert isinstance(results[1], ValueError)

    async def test_raises_exception_when_return_exceptions_false(self) -> None:
        async def failure() -> None:
            raise ValueError("error")

        with pytest.raises(ValueError, match="error"):
            await gather_with_concurrency(2, failure(), return_exceptions=False)


@pytest.mark.asyncio
class TestPerformParallelRequests:
    async def test_calls_function_for_each_item(self) -> None:
        async def mock_func(x: str) -> str:
            return f"result_{x}"

        items = ["a", "b", "c"]

        results = await perform_parallel_requests(mock_func, items)

        assert results == ["result_a", "result_b", "result_c"]

    async def test_wraps_exceptions_in_mottle_exception(self) -> None:
        async def mock_func(x: str) -> None:
            raise Exception("api error")

        items = ["a"]

        with pytest.raises(MottleException, match="Failed to perform parallel requests"):
            await perform_parallel_requests(mock_func, items)


@pytest.mark.asyncio
class TestPerformParallelChunkedRequests:
    async def test_chunks_items_and_calls_function(self) -> None:
        async def mock_func(chunk: list[str]) -> str:
            return f"result_{len(chunk)}"

        items = ["a", "b", "c", "d", "e"]

        results = await perform_parallel_chunked_requests(mock_func, items, chunk_size=2)

        assert len(results) == 3  # 3 chunks: [a,b], [c,d], [e]
        assert results == ["result_2", "result_2", "result_1"]

    async def test_wraps_exceptions_in_mottle_exception(self) -> None:
        async def mock_func(chunk: list[str]) -> None:
            raise Exception("api error")

        items = ["a", "b"]

        with pytest.raises(MottleException, match="Failed to perform parallel chunked requests"):
            await perform_parallel_chunked_requests(mock_func, items, chunk_size=1)


@pytest.mark.asyncio
class TestGetAllChunked:
    async def test_flattens_chunked_results(self) -> None:
        async def mock_func(chunk: list[str]) -> list[str]:
            return [f"result_{item}" for item in chunk]

        items = ["a", "b", "c", "d", "e"]

        results = await get_all_chunked(mock_func, items, chunk_size=2)

        assert results == ["result_a", "result_b", "result_c", "result_d", "result_e"]


@pytest.mark.asyncio
class TestMottleException:
    async def test_exception_has_message(self) -> None:
        exc = MottleException("Test error message")
        assert str(exc) == "Test error message"

    async def test_exception_can_wrap_original_exception(self) -> None:
        original = ValueError("Original error")
        exc = MottleException("Wrapped error")
        exc.__cause__ = original

        assert isinstance(exc.__cause__, ValueError)
        assert str(exc.__cause__) == "Original error"


SPOTIFY_RESPONSES = {
    "/v1/search": "spotify_search.json",
    "/v1/playlists/test_playlist_id/tracks": "spotify_playlist_items.json",
}


def spotify_api(request: httpx.Request) -> httpx.Response:
    """Answer the Spotify Web API with responses saved from it, a search with the result types it asks for."""
    data = json.loads((DATA_DIR / SPOTIFY_RESPONSES[request.url.path]).read_text())

    if request.url.path == "/v1/search":
        types = request.url.params["type"].split(",")
        data = {f"{t}s": data[f"{t}s"] for t in types}

    return httpx.Response(200, json=data)


@pytest.fixture
def spotify_client() -> MottleSpotifyClient:
    client = MottleSpotifyClient("test_access_token")
    client.spotify_client = Spotify(
        "test_access_token", sender=AsyncSender(httpx.AsyncClient(transport=httpx.MockTransport(spotify_api)))
    )
    return client


@pytest.mark.asyncio
class TestMottleSpotifyClient:
    async def test_find_playlists_skips_null_results(self, spotify_client: MottleSpotifyClient) -> None:
        """Spotify's search results hold null in place of some playlists, which are left out."""
        playlists = await spotify_client.find_playlists("deep purple")

        assert [p.name for p in playlists] == ["Deep Purple - Greatest Hits"]

    async def test_find_artists_and_playlists_skips_null_playlists(self, spotify_client: MottleSpotifyClient) -> None:
        """Searching artists and playlists together also leaves out the null playlists."""
        artists, playlists = await spotify_client.find_artists_and_playlists("deep purple")

        assert [a.name for a in artists] == ["Deep Purple", "Purple Disco Machine"]
        assert [p.name for p in playlists] == ["Deep Purple - Greatest Hits"]

    async def test_find_duplicate_tracks_in_playlist(self, spotify_client: MottleSpotifyClient) -> None:
        """A track the playlist holds more than once is reported once, with the number of times it appears."""
        duplicates = await spotify_client.find_duplicate_tracks_in_playlist("test_playlist_id")

        assert [(track.name, count) for track, count in duplicates] == [("Like a Rolling Stone", 2)]


class TestGetQualname:
    def test_function(self) -> None:
        """A function is named by its qualified name."""
        assert get_qualname(list_has) == "list_has"

    def test_partial(self) -> None:
        """A partial is named by the function it wraps."""
        assert get_qualname(partial(list_has, [])) == "list_has"

    def test_callable_without_qualname(self) -> None:
        """A callable object without a qualified name is named by its repr."""
        mock = Mock()

        assert get_qualname(mock) == repr(mock)
