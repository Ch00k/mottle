import json
import re
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from tekore import AsyncSender, Spotify

from web.utils import MottleException, MottleSpotifyClient

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

API_URL = "https://api.spotify.com/v1"


def item(kind: str, spotify_id: str) -> dict[str, Any]:
    return {
        "id": spotify_id,
        "href": f"{API_URL}/{kind}s/{spotify_id}",
        "type": kind,
        "uri": f"spotify:{kind}:{spotify_id}",
        "external_urls": {"spotify": f"https://open.spotify.com/{kind}/{spotify_id}"},
    }


def paging(items: list[Any], next_url: str | None = None) -> dict[str, Any]:
    return {
        "href": f"{API_URL}/paging",
        "items": items,
        "limit": 50,
        "next": next_url,
        "offset": 0,
        "previous": None,
        "total": len(items),
    }


def public_user(spotify_id: str) -> dict[str, Any]:
    return item("user", spotify_id) | {"display_name": f"User {spotify_id}"}


def private_user(spotify_id: str) -> dict[str, Any]:
    return public_user(spotify_id) | {"account_id": spotify_id}


def simple_artist(spotify_id: str) -> dict[str, Any]:
    return item("artist", spotify_id) | {"name": f"Artist {spotify_id}"}


def full_artist(spotify_id: str) -> dict[str, Any]:
    return simple_artist(spotify_id) | {
        "followers": {"href": None, "total": 0},
        "genres": [],
        "images": [],
        "popularity": 0,
    }


def simple_album(spotify_id: str, album_type: str = "album") -> dict[str, Any]:
    return item("album", spotify_id) | {
        "album_type": album_type,
        "artists": [simple_artist("artist")],
        "images": [],
        "name": f"Album {spotify_id}",
        "total_tracks": 1,
        "release_date": "2020-01-01",
        "release_date_precision": "day",
    }


def simple_track(spotify_id: str) -> dict[str, Any]:
    return item("track", spotify_id) | {
        "artists": [simple_artist("artist")],
        "disc_number": 1,
        "duration_ms": 1000,
        "explicit": False,
        "is_local": False,
        "name": f"Track {spotify_id}",
        "track_number": 1,
    }


def full_track(spotify_id: str) -> dict[str, Any]:
    return simple_track(spotify_id) | {"album": simple_album("album"), "external_ids": {}, "popularity": 0}


def full_album(spotify_id: str) -> dict[str, Any]:
    return simple_album(spotify_id) | {
        "copyrights": [],
        "external_ids": {},
        "genres": [],
        "label": None,
        "popularity": 0,
        "tracks": paging([simple_track(f"{spotify_id}_track")]),
    }


def playlist_track(spotify_id: str) -> dict[str, Any]:
    return {
        "added_at": "2025-01-01T00:00:00Z",
        "added_by": public_user("owner"),
        "is_local": False,
        "primary_color": None,
        "video_thumbnail": None,
        "track": full_track(spotify_id) | {"episode": False, "track": True},
    }


def playlist(spotify_id: str) -> dict[str, Any]:
    return item("playlist", spotify_id) | {
        "collaborative": False,
        "description": None,
        "images": [],
        "name": f"Playlist {spotify_id}",
        "owner": public_user("owner"),
        "public": True,
        "snapshot_id": "snapshot",
        "primary_color": None,
    }


def simple_playlist(spotify_id: str) -> dict[str, Any]:
    tracks = {"href": f"{API_URL}/playlists/{spotify_id}/tracks", "total": 0}
    return playlist(spotify_id) | {"tracks": tracks, "items": tracks}


def full_playlist(spotify_id: str) -> dict[str, Any]:
    return playlist(spotify_id) | {
        "followers": {"href": None, "total": 0},
        "tracks": paging([]),
        "items": paging([]),
    }


def ids_param(request: httpx.Request) -> list[str]:
    return request.url.params["ids"].split(",")


def uris_payload(request: httpx.Request) -> list[str]:
    return json.loads(request.content)["uris"]


def cursor_paging(items: list[Any], after: str | None, total: int) -> dict[str, Any]:
    next_url = None if after is None else f"{API_URL}/me/following?type=artist&limit=50&after={after}"
    return {
        "href": f"{API_URL}/paging",
        "items": items,
        "limit": 50,
        "next": next_url,
        "cursors": {"after": after},
        "total": total,
    }


def followed_artists(request: httpx.Request) -> dict[str, Any]:
    """Answer with the followed artists in two pages, the second asked for with the first's cursor."""
    if request.url.params.get("after") == "artist_2":
        return {"artists": cursor_paging([full_artist("artist_3")], after=None, total=3)}

    return {"artists": cursor_paging([full_artist("artist_1"), full_artist("artist_2")], after="artist_2", total=3)}


def artist_albums(request: httpx.Request, artist_id: str) -> dict[str, Any]:
    """Answer with an album of each type asked for."""
    album_types = request.url.params["include_groups"].split(",")
    return paging([simple_album(f"{artist_id}_{t}", t) for t in album_types])


ROUTES: list[tuple[str, str, Callable[[httpx.Request, re.Match], Any]]] = [
    ("GET", r"/v1/me/", lambda _, __: private_user("current_user")),
    (
        "GET",
        r"/v1/me/tracks",
        lambda _, __: paging([{"added_at": "2025-01-01T00:00:00Z", "track": full_track("saved")}]),
    ),
    ("DELETE", r"/v1/me/tracks", lambda _, __: None),
    (
        "GET",
        r"/v1/search",
        lambda _, __: {
            "artists": paging([full_artist("found_artist")]),
            "playlists": paging([simple_playlist("found_playlist"), None]),
        },
    ),
    ("GET", r"/v1/artists", lambda r, _: {"artists": [full_artist(i) for i in ids_param(r)]}),
    ("GET", r"/v1/artists/(\w+)", lambda _, m: full_artist(m[1])),
    ("GET", r"/v1/artists/(\w+)/albums", lambda r, m: artist_albums(r, m[1])),
    ("GET", r"/v1/albums", lambda r, _: {"albums": [full_album(i) for i in ids_param(r)]}),
    ("GET", r"/v1/albums/(\w+)", lambda _, m: full_album(m[1])),
    ("GET", r"/v1/albums/(\w+)/tracks", lambda _, m: paging([simple_track(f"{m[1]}_track")])),
    ("GET", r"/v1/tracks", lambda r, _: {"tracks": [full_track(i) for i in ids_param(r)]}),
    ("POST", r"/v1/users/(\w+)/playlists", lambda r, _: full_playlist(json.loads(r.content)["name"])),
    ("GET", r"/v1/me/playlists", lambda _, __: paging([simple_playlist("followed_playlist")])),
    ("GET", r"/v1/me/following", lambda r, _: followed_artists(r)),
    ("GET", r"/v1/playlists/(\w+)", lambda _, m: full_playlist(m[1])),
    ("PUT", r"/v1/playlists/(\w+)", lambda _, __: None),
    ("PUT", r"/v1/playlists/(\w+)/images", lambda _, __: None),
    ("PUT", r"/v1/playlists/(\w+)/followers", lambda _, __: None),
    ("DELETE", r"/v1/playlists/(\w+)/followers", lambda _, __: None),
    ("GET", r"/v1/playlists/(\w+)/tracks", lambda _, m: paging([playlist_track(f"{m[1]}_track")])),
    ("POST", r"/v1/playlists/(\w+)/tracks", lambda _, __: {"snapshot_id": "snapshot"}),
    ("DELETE", r"/v1/playlists/(\w+)/tracks", lambda _, __: {"snapshot_id": "snapshot"}),
]


class SpotifyAPI:
    """Answers the Spotify Web API with objects named after the IDs asked for, and records the requests it gets.

    A request `fails` picks is answered with a 404.
    """

    def __init__(self, fails: Callable[[httpx.Request], bool] = lambda _: False) -> None:
        self.fails = fails
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)

        if self.fails(request):
            return httpx.Response(404, json={"error": {"status": 404, "message": "Not found"}})

        for method, path, answer in ROUTES:
            if request.method == method and (match := re.fullmatch(path, request.url.path)):
                data = answer(request, match)
                return httpx.Response(200) if data is None else httpx.Response(200, json=data)

        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    def requested(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and re.fullmatch(path, r.url.path)]


def make_client(api: SpotifyAPI) -> MottleSpotifyClient:
    client = MottleSpotifyClient("test_access_token")
    client.spotify_client = Spotify(
        "test_access_token",
        sender=AsyncSender(httpx.AsyncClient(transport=httpx.MockTransport(api))),
        max_limits_on=True,
        chunked_on=True,
    )
    return client


@pytest.fixture
def api() -> SpotifyAPI:
    return SpotifyAPI()


@pytest.fixture
def client(api: SpotifyAPI) -> MottleSpotifyClient:
    return make_client(api)


@pytest.mark.asyncio
class TestResults:
    async def test_get_current_user(self, client: MottleSpotifyClient) -> None:
        """The current user is returned as Spotify sends it."""
        user = await client.get_current_user()

        assert user.id == "current_user"

    async def test_get_current_user_saved_tracks(self, client: MottleSpotifyClient) -> None:
        """The user's saved tracks are returned."""
        saved_tracks = await client.get_current_user_saved_tracks()

        assert [t.track.id for t in saved_tracks] == ["saved"]

    async def test_remove_user_saved_tracks(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """Saved tracks are removed with one request per 50 IDs."""
        track_ids = [f"track_{i}" for i in range(60)]

        await client.remove_user_saved_tracks(track_ids)

        assert [ids_param(r) for r in api.requested("DELETE", r"/v1/me/tracks")] == [track_ids[:50], track_ids[50:]]

    async def test_find_artists_and_playlists(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """One search finds the artists and the playlists, leaving out the null playlists."""
        artists, playlists = await client.find_artists_and_playlists("query")

        assert [a.id for a in artists] == ["found_artist"]
        assert [p.id for p in playlists] == ["found_playlist"]
        assert api.requested("GET", r"/v1/search")[0].url.params["type"] == "artist,playlist"

    async def test_get_artists(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """Artists are fetched in requests of up to 50 IDs, and returned in the order asked for."""
        artist_ids = [f"artist_{i}" for i in range(120)]

        artists = await client.get_artists(artist_ids)

        assert [a.id for a in artists] == artist_ids
        assert sorted(len(ids_param(r)) for r in api.requested("GET", r"/v1/artists")) == [20, 50, 50]

    async def test_get_artist(self, client: MottleSpotifyClient) -> None:
        """The artist is returned."""
        artist = await client.get_artist("artist_1")

        assert artist.id == "artist_1"

    async def test_get_artist_albums(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """The artist's albums of every type are asked for in one request."""
        albums = await client.get_artist_albums("artist_1")

        assert [a.id for a in albums] == ["artist_1_album", "artist_1_single", "artist_1_compilation"]
        assert len(api.requested("GET", r"/v1/artists/artist_1/albums")) == 1

    async def test_get_artist_albums_separately_by_type(self, client: MottleSpotifyClient) -> None:
        """Each album type is asked for on its own, and the albums are returned in the order of the types."""
        albums = await client.get_artist_albums_separately_by_type("artist_1")

        assert [a.id for a in albums] == ["artist_1_album", "artist_1_single", "artist_1_compilation"]

    async def test_get_album(self, client: MottleSpotifyClient) -> None:
        """The album is returned."""
        album = await client.get_album("album_1")

        assert album.id == "album_1"

    async def test_get_albums(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """Albums are fetched in requests of up to 20 IDs, and returned in the order asked for."""
        album_ids = [f"album_{i}" for i in range(45)]

        albums = await client.get_albums(album_ids)

        assert [a.id for a in albums] == album_ids
        assert [len(ids_param(r)) for r in api.requested("GET", r"/v1/albums")] == [20, 20, 5]

    async def test_get_tracks(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """Tracks are fetched in requests of up to 50 IDs, and returned in the order asked for."""
        track_ids = [f"track_{i}" for i in range(120)]

        tracks = await client.get_tracks(track_ids)

        assert [t.id for t in tracks] == track_ids
        assert sorted(len(ids_param(r)) for r in api.requested("GET", r"/v1/tracks")) == [20, 50, 50]

    async def test_get_album_tracks(self, client: MottleSpotifyClient) -> None:
        """The album's tracks are returned."""
        tracks = await client.get_album_tracks("album_1")

        assert [t.id for t in tracks] == ["album_1_track"]

    async def test_get_tracks_in_albums(self, client: MottleSpotifyClient) -> None:
        """The tracks of all the albums are returned, album by album."""
        tracks = await client.get_tracks_in_albums(["album_1", "album_2"])

        assert [t.id for t in tracks] == ["album_1_track", "album_2_track"]

    async def test_add_tracks_to_playlist(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """Tracks are added with one request per 100, in order."""
        track_uris = [f"spotify:track:{i}" for i in range(150)]

        await client.add_tracks_to_playlist("playlist_1", track_uris)

        requests = api.requested("POST", r"/v1/playlists/playlist_1/tracks")
        assert [uris_payload(r) for r in requests] == [track_uris[:100], track_uris[100:]]

    async def test_remove_tracks_from_playlist(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """Every occurrence of the tracks is removed from the playlist."""
        await client.remove_tracks_from_playlist("playlist_1", ["spotify:track:1"])

        [request] = api.requested("DELETE", r"/v1/playlists/playlist_1/tracks")
        assert json.loads(request.content) == {"tracks": [{"uri": "spotify:track:1"}]}

    async def test_create_playlist(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """The playlist is created for the user with the name and visibility given."""
        created_playlist = await client.create_playlist("user_1", "new_playlist", is_public=False)

        assert created_playlist.id == "new_playlist"
        [request] = api.requested("POST", r"/v1/users/user_1/playlists")
        assert json.loads(request.content) == {"name": "new_playlist", "public": False, "description": ""}

    async def test_upload_playlist_cover_image(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """The image is uploaded as the playlist's cover."""
        await client.upload_playlist_cover_image("playlist_1", "aW1hZ2U=")

        [request] = api.requested("PUT", r"/v1/playlists/playlist_1/images")
        assert request.content == b"aW1hZ2U="

    async def test_get_current_user_playlists(self, client: MottleSpotifyClient) -> None:
        """The playlists the user follows are returned."""
        playlists = await client.get_current_user_playlists()

        assert [p.id for p in playlists] == ["followed_playlist"]

    async def test_get_current_user_followed_artists(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """The followed artists of every page are returned, each page asked for with the previous page's cursor."""
        artists = await client.get_current_user_followed_artists()

        assert [a.id for a in artists] == ["artist_1", "artist_2", "artist_3"]
        assert len(api.requested("GET", r"/v1/me/following")) == 2

    async def test_get_playlist(self, client: MottleSpotifyClient) -> None:
        """The playlist is returned."""
        found_playlist = await client.get_playlist("playlist_1")

        assert found_playlist.id == "playlist_1"

    async def test_change_playlist_details(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """Only the details given are sent."""
        await client.change_playlist_details("playlist_1", name="Renamed")

        [request] = api.requested("PUT", r"/v1/playlists/playlist_1")
        assert json.loads(request.content) == {"name": "Renamed"}

    async def test_follow_playlist(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """The user follows the playlist."""
        await client.follow_playlist("playlist_1")

        assert len(api.requested("PUT", r"/v1/playlists/playlist_1/followers")) == 1

    async def test_unfollow_playlist(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """The user unfollows the playlist."""
        await client.unfollow_playlist("playlist_1")

        assert len(api.requested("DELETE", r"/v1/playlists/playlist_1/followers")) == 1

    async def test_get_playlist_tracks(self, client: MottleSpotifyClient) -> None:
        """The playlist's tracks are returned."""
        [playlist_item] = await client.get_playlist_tracks("playlist_1")

        assert playlist_item.track is not None
        assert playlist_item.track.id == "playlist_1_track"


@pytest.mark.asyncio
class TestCreatePlaylistWithTracks:
    async def test_creates_playlist_and_adds_tracks(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """The playlist is created, then the tracks are added to it in order, and the created playlist is returned."""
        track_uris = [f"spotify:track:{i}" for i in range(150)]

        created_playlist = await client.create_playlist_with_tracks("user_1", "new_playlist", track_uris)

        assert created_playlist.id == "new_playlist"
        assert [(r.method, r.url.path) for r in api.requests] == [
            ("POST", "/v1/users/user_1/playlists"),
            ("POST", "/v1/playlists/new_playlist/tracks"),
            ("POST", "/v1/playlists/new_playlist/tracks"),
        ]
        requests = api.requested("POST", r"/v1/playlists/new_playlist/tracks")
        assert [uris_payload(r) for r in requests] == [track_uris[:100], track_uris[100:]]

    async def test_refuses_no_tracks(self, client: MottleSpotifyClient, api: SpotifyAPI) -> None:
        """A playlist with no tracks is not created."""
        with pytest.raises(MottleException, match=r"^No tracks to add to playlist$"):
            await client.create_playlist_with_tracks("user_1", "new_playlist", [])

        assert api.requests == []

    async def test_failing_creation(self) -> None:
        """A playlist Spotify does not create gets no tracks."""
        api = SpotifyAPI(fails=lambda r: r.url.path == "/v1/users/user_1/playlists")

        with pytest.raises(MottleException, match=r"^Failed to create playlist$"):
            await make_client(api).create_playlist_with_tracks("user_1", "new_playlist", ["spotify:track:1"])

        assert len(api.requests) == 1

    async def test_failing_track_addition(self) -> None:
        """Tracks Spotify does not add fail the call."""
        api = SpotifyAPI(fails=lambda r: r.url.path == "/v1/playlists/new_playlist/tracks")

        with pytest.raises(MottleException, match=r"^Failed to add tracks to playlist"):
            await make_client(api).create_playlist_with_tracks("user_1", "new_playlist", ["spotify:track:1"])


CALLS_AND_ERRORS: list[tuple[Callable[[MottleSpotifyClient], Awaitable[Any]], str]] = [
    (lambda c: c.get_current_user(), "Failed to get current user"),
    (lambda c: c.get_current_user_saved_tracks(), "Failed to get items"),
    (lambda c: c.remove_user_saved_tracks(["track_1"]), "Failed to remove saved tracks"),
    (
        lambda c: c.find_artists_and_playlists("query"),
        "Failed to search for artists and playlists with query {query}",
    ),
    (lambda c: c.get_artists(["artist_1"]), "Failed to get artists"),
    (lambda c: c.get_artist("artist_1"), "Failed to get artist artist_1"),
    (lambda c: c.get_artist_albums("artist_1"), "Failed to get items"),
    (lambda c: c.get_artist_albums_separately_by_type("artist_1"), "Failed to get artist albums"),
    (lambda c: c.get_album("album_1"), "Failed to get album"),
    (lambda c: c.get_albums(["album_1"]), "Failed to perform parallel chunked requests"),
    (lambda c: c.get_tracks(["track_1"]), "Failed to get tracks"),
    (lambda c: c.get_album_tracks("album_1"), "Failed to get items"),
    (lambda c: c.get_tracks_in_albums(["album_1"]), "Failed to get tracks"),
    (
        lambda c: c.add_tracks_to_playlist("playlist_1", ["spotify:track:1"]),
        "Failed to add tracks to playlist playlist_1",
    ),
    (
        lambda c: c.remove_tracks_from_playlist("playlist_1", ["spotify:track:1"]),
        "Failed to remove tracks from playlist playlist_1",
    ),
    (lambda c: c.create_playlist("user_1", "new_playlist"), "Failed to create playlist"),
    (lambda c: c.upload_playlist_cover_image("playlist_1", "aW1hZ2U="), "Failed to upload cover image to playlist"),
    (lambda c: c.get_current_user_playlists(), "Failed to get items"),
    (lambda c: c.get_current_user_followed_artists(), "Failed to get items"),
    (lambda c: c.get_playlist("playlist_1"), "Failed to get playlist playlist_1"),
    (
        lambda c: c.change_playlist_details("playlist_1", name="Renamed"),
        "Failed to change playlist details playlist_1",
    ),
    (lambda c: c.follow_playlist("playlist_1"), "Failed to follow playlist playlist_1"),
    (lambda c: c.unfollow_playlist("playlist_1"), "Failed to unfollow playlist playlist_1"),
    (lambda c: c.get_playlist_tracks("playlist_1"), "Failed to get items"),
    (lambda c: c.find_duplicate_tracks_in_playlist("playlist_1"), "Failed to get items"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("call", "error"), CALLS_AND_ERRORS)
async def test_failing_request(call: Callable[[MottleSpotifyClient], Awaitable[Any]], error: str) -> None:
    """A request Spotify refuses fails the call with a MottleException that says what failed, raised from the cause."""
    client = make_client(SpotifyAPI(fails=lambda _: True))

    with pytest.raises(MottleException) as exc_info:
        await call(client)

    assert str(exc_info.value) == error
    assert exc_info.value.__cause__ is not None
