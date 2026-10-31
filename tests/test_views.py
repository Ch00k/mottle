import json
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from django.test import AsyncClient, RequestFactory, TestCase
from tekore.model import FullTrack, Image, SimpleAlbum, SimpleTrack

from tests.conftest import create_mock_artist_getter, create_mock_playlist_getter
from web.middleware import MottleExceptionMiddleware
from web.models import Artist, Playlist, PlaylistUpdate, SpotifyAuthRequest, SpotifyUser
from web.utils import MottleException, MottleSpotifyClient
from web.views import playlist_updates

if TYPE_CHECKING:
    from web.middleware import MottleHttpRequest


def make_full_track(track_id: str, album_id: str) -> FullTrack:
    return FullTrack(
        id=track_id,
        href=f"https://api.spotify.com/v1/tracks/{track_id}",
        type="track",
        uri=f"spotify:track:{track_id}",
        external_urls={"spotify": f"https://open.spotify.com/track/{track_id}"},
        name=f"Track {track_id}",
        duration_ms=240000,
        explicit=False,
        disc_number=1,
        track_number=1,
        is_local=False,
        popularity=50,
        external_ids={},
        preview_url=None,
        album=SimpleAlbum(
            id=album_id,
            href=f"https://api.spotify.com/v1/albums/{album_id}",
            type="album",
            uri=f"spotify:album:{album_id}",
            external_urls={"spotify": f"https://open.spotify.com/album/{album_id}"},
            name=f"Album {album_id}",
            album_type="album",
            release_date="2026-01-01",
            release_date_precision="day",
            total_tracks=1,
            images=[Image(url=f"https://i.scdn.co/image/{album_id}", height=64, width=64)],
            artists=[],
        ),
        artists=[],
    )


@pytest.mark.asyncio
class TestCallback(TestCase):
    async def test_unknown_state_is_a_bad_request(self) -> None:
        """A callback whose state matches no authorization request is rejected as a bad request, not a server error."""
        response = await AsyncClient().get("/callback/", {"code": "code", "state": "unknown_state"})

        assert response.status_code == 400


@pytest.mark.asyncio
class TestPlaylistUpdates(TestCase):
    async def test_artist_update_lists_its_tracks(self) -> None:
        """An update from a watched artist lists each new track with its link and album cover.

        The tracks of the update's albums are looked up in full, since only full tracks carry their album.
        """
        spotify_user = await SpotifyUser.objects.acreate(spotify_id="updates_user")
        watching = await Playlist.objects.acreate(spotify_id="watching_playlist", spotify_user=spotify_user)
        artist = await Artist.objects.acreate(spotify_id="watched_artist")
        await PlaylistUpdate.objects.acreate(target_playlist=watching, source_artist=artist, albums_added=["album1"])

        request = cast("MottleHttpRequest", RequestFactory().get("/"))
        request.session = {"spotify_user_id": str(spotify_user.id)}
        spotify_client = Mock(spec=MottleSpotifyClient)
        spotify_client.get_playlist = create_mock_playlist_getter()
        spotify_client.get_artist = create_mock_artist_getter({"watched_artist": "Watched Artist"})
        spotify_client.get_tracks_in_albums = AsyncMock(return_value=[Mock(spec=SimpleTrack, id="track1")])
        spotify_client.get_tracks = AsyncMock(return_value=[make_full_track("track1", "album1")])
        request.spotify_client = spotify_client

        with patch("web.views.check_playlist_for_updates", AsyncMock()):
            # ty takes a view wrapped in Django's method decorators for the decorator's type variable
            response = await playlist_updates(request, "watching_playlist")  # ty: ignore[call-non-callable]

        assert response.status_code == 200
        content = response.content.decode()
        assert 'href="https://open.spotify.com/track/track1"' in content
        assert 'src="https://i.scdn.co/image/album1"' in content
        spotify_client.get_tracks_in_albums.assert_awaited_once_with(["album1"])
        spotify_client.get_tracks.assert_awaited_once_with(["track1"])


@pytest.mark.asyncio
class TestMottleExceptionMiddleware(TestCase):
    async def test_mottle_exception_becomes_error_toast(self) -> None:
        """A MottleException a view raises is answered with a 500 whose HTMX trigger shows the error as a toast.

        The callback view raises it when Spotify refuses to issue a token. The error is logged and sent to Sentry.
        """
        await SpotifyAuthRequest.objects.acreate(state="known_state", redirect_uri="/")

        with (
            patch.object(
                SpotifyAuthRequest, "request_token", AsyncMock(side_effect=MottleException("Spotify said no"))
            ),
            patch("web.middleware.sentry_sdk.capture_exception") as capture_exception,
            self.assertLogs("web.middleware", level="ERROR") as logs,
        ):
            response = await AsyncClient().get("/callback/", {"code": "code", "state": "known_state"})

        assert response.status_code == 500
        assert json.loads(response["HX-Trigger"]) == {"HXToast": {"type": "error", "body": "Error: Spotify said no"}}
        assert logs.output[0].startswith("ERROR:web.middleware:Spotify said no")
        capture_exception.assert_called_once()

    def test_other_exceptions_are_left_to_django(self) -> None:
        """An exception other than a MottleException gets no response from the middleware, so Django handles it."""
        middleware = MottleExceptionMiddleware(AsyncMock())

        assert middleware.process_exception(RequestFactory().get("/"), ValueError("unexpected")) is None
