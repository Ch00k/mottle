from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from django.test import AsyncClient, RequestFactory, TestCase
from tekore.model import FullTrack, Image, SimpleAlbum, SimpleTrack

from tests.conftest import create_mock_artist_getter, create_mock_playlist_getter
from web.models import Artist, Playlist, PlaylistUpdate, SpotifyUser
from web.utils import MottleSpotifyClient
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
            response = await playlist_updates(request, "watching_playlist")

        assert response.status_code == 200
        content = response.content.decode()
        assert 'href="https://open.spotify.com/track/track1"' in content
        assert 'src="https://i.scdn.co/image/album1"' in content
        spotify_client.get_tracks_in_albums.assert_awaited_once_with(["album1"])
        spotify_client.get_tracks.assert_awaited_once_with(["track1"])
