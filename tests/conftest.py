import datetime
import uuid
from collections.abc import Awaitable, Callable
from unittest.mock import Mock

import pytest
from django.conf import settings
from django.contrib.gis.geos import Point
from django.test import RequestFactory
from tekore.model import FullAlbum, FullArtist, FullPlaylist, FullTrack, PublicUser

from web.middleware import MottleHttpRequest
from web.models import (
    Artist,
    Event,
    EventArtist,
    Playlist,
    PlaylistUpdate,
    PlaylistWatchConfig,
    SpotifyAuth,
    SpotifyUser,
    User,
)
from web.utils import MottleSpotifyClient


@pytest.fixture
async def spotify_user() -> SpotifyUser:
    return await SpotifyUser.objects.acreate(
        spotify_id="test_spotify_user_123",
        display_name="Test User",
        email="test@example.com",
    )


@pytest.fixture
async def spotify_auth(spotify_user: SpotifyUser) -> SpotifyAuth:
    return await SpotifyAuth.objects.acreate(
        spotify_user=spotify_user,
        access_token="test_access_token",
        refresh_token="test_refresh_token",
        expires_at=datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(hours=1),
        token_scope=["user-read-private", "playlist-modify-public"],
    )


@pytest.fixture
async def user(spotify_user: SpotifyUser) -> User:
    return await User.objects.acreate(
        spotify_user=spotify_user,
        playlist_notifications=True,
        release_notifications=False,
        event_notifications=True,
    )


@pytest.fixture
async def artist() -> Artist:
    return await Artist.objects.acreate(spotify_id="test_artist_123")


@pytest.fixture
async def event_artist(artist: Artist) -> EventArtist:
    return await EventArtist.objects.acreate(
        artist=artist,
        musicbrainz_id=uuid.uuid4(),
        songkick_url="https://www.songkick.com/artists/test",
        bandsintown_url="https://www.bandsintown.com/a/test",
        songkick_name_match_accuracy=100,
        bandsintown_name_match_accuracy=95,
    )


@pytest.fixture
async def event(event_artist: EventArtist) -> Event:
    return await Event.objects.acreate(
        artist=event_artist,
        source="songkick",
        source_url="https://www.songkick.com/concerts/12345",
        type="concert",
        date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=30),
        venue="Test Venue",
        postcode="SW1A 1AA",
        address="Test Street 1",
        city="London",
        country="United Kingdom",
        geolocation=Point(-0.1276, 51.5074, srid=settings.GEODJANGO_SRID),
        stream_urls=None,
        tickets_urls=["https://example.com/tickets"],
    )


@pytest.fixture
async def playlist(spotify_user: SpotifyUser) -> Playlist:
    return await Playlist.objects.acreate(
        spotify_id="test_playlist_123",
        spotify_user=spotify_user,
    )


@pytest.fixture
async def watched_playlist() -> Playlist:
    return await Playlist.objects.acreate(spotify_id="watched_playlist_456")


@pytest.fixture
async def playlist_watch_config(playlist: Playlist, watched_playlist: Playlist) -> PlaylistWatchConfig:
    return await PlaylistWatchConfig.objects.acreate(
        watching_playlist=playlist,
        watched_playlist=watched_playlist,
        auto_accept_updates=False,
    )


@pytest.fixture
async def playlist_update(playlist: Playlist, watched_playlist: Playlist) -> PlaylistUpdate:
    return await PlaylistUpdate.objects.acreate(
        target_playlist=playlist,
        source_playlist=watched_playlist,
        tracks_added=["track1", "track2", "track3"],
        tracks_removed=None,
        albums_added=None,
        albums_removed=None,
        is_notified_of=False,
        is_accepted=None,
    )


def create_mock_request(headers: dict[str, str] | None = None) -> MottleHttpRequest:
    """Create a mock MottleHttpRequest with required attributes."""
    factory = RequestFactory()
    request = factory.get("/")
    mock_request = Mock(spec=MottleHttpRequest, wraps=request)
    mock_request.headers = headers or {}
    mock_request.spotify_client = Mock(spec=MottleSpotifyClient)
    return mock_request


def create_mock_artist_getter(
    artist_data: dict[str, str | dict] | None = None,
) -> Callable[[str], Awaitable[Mock]]:
    """
    Create a mock get_artist function for MottleSpotifyClient.

    Args:
        artist_data: Dict mapping artist_id to either:
                    - str: artist name (simple case)
                    - dict: artist details with keys like 'name', 'external_urls', 'images'
                    Example: {"artist1": "The Beatles", "artist2": {"name": "Pink Floyd", "images": [...]}}

    Returns:
        An async function that returns a mocked FullArtist
    """
    if artist_data is None:
        artist_data = {}

    async def mock_get_artist(spotify_id: str) -> Mock:
        data = artist_data.get(spotify_id, {})
        mock_artist = Mock(spec=FullArtist)

        # Handle both string (just name) and dict (full details) formats
        if isinstance(data, str):
            mock_artist.name = data
            mock_artist.external_urls = {"spotify": f"https://spotify.com/artist/{spotify_id}"}
            mock_artist.images = []
        else:
            # data is dict (from .get(..., {}))
            mock_artist.name = data.get("name", f"Artist {spotify_id}")
            mock_artist.external_urls = data.get(
                "external_urls", {"spotify": f"https://spotify.com/artist/{spotify_id}"}
            )
            mock_artist.images = data.get("images", [])

        return mock_artist

    return mock_get_artist


def create_mock_playlist_getter(
    playlist_data: dict[str, dict[str, str | int | dict]] | None = None,
) -> Callable[[str], Awaitable[Mock]]:
    """
    Create a mock get_playlist function for MottleSpotifyClient.

    Args:
        playlist_data: Dict mapping playlist_id to dict with optional keys:
                      'name', 'owner_name', 'owner_id', 'snapshot_id', 'num_tracks', 'external_urls', 'images'
                      Example: {"playlist1": {"name": "My Playlist", "owner_name": "John"}}

    Returns:
        An async function that returns a mocked FullPlaylist
    """
    if playlist_data is None:
        playlist_data = {}

    async def mock_get_playlist(spotify_id: str) -> Mock:
        data = playlist_data.get(spotify_id, {})
        mock_playlist = Mock(spec=FullPlaylist)
        mock_playlist.name = data.get("name", f"Playlist {spotify_id}")
        mock_playlist.external_urls = data.get(
            "external_urls", {"spotify": f"https://spotify.com/playlist/{spotify_id}"}
        )
        mock_playlist.images = data.get("images", [])

        mock_owner = Mock(spec=PublicUser)
        owner_id = data.get("owner_id", "default_owner")
        mock_owner.id = owner_id
        mock_owner.display_name = data.get("owner_name", "Default Owner")
        mock_owner.external_urls = data.get("owner_external_urls", {"spotify": f"https://spotify.com/user/{owner_id}"})
        mock_playlist.owner = mock_owner

        mock_playlist.snapshot_id = data.get("snapshot_id", "snapshot123")
        mock_tracks = Mock()
        mock_tracks.total = data.get("num_tracks", 0)
        mock_playlist.tracks = mock_tracks

        return mock_playlist

    return mock_get_playlist


def create_mock_album_getter(
    album_data: dict[str, dict[str, str | list]] | None = None,
) -> Callable[[str], Awaitable[Mock]]:
    """
    Create a mock get_album function (singular) for MottleSpotifyClient.

    Args:
        album_data: Dict mapping album_id to dict with optional keys:
                   'name', 'release_date', 'external_urls', 'images'
                   Example: {"album1": {"name": "After Hours", "release_date": "2020-03-20"}}

    Returns:
        An async function that returns a mocked FullAlbum
    """
    if album_data is None:
        album_data = {}

    async def mock_get_album(spotify_id: str) -> Mock:
        data = album_data.get(spotify_id, {})
        mock_album = Mock(spec=FullAlbum)

        if data:
            mock_album.name = data.get("name", f"Album {spotify_id}")
            mock_album.release_date = data.get("release_date", "2020-01-01")
            mock_album.external_urls = data.get("external_urls", {"spotify": f"https://spotify.com/album/{spotify_id}"})
            mock_album.images = data.get("images", [])
        else:
            # Generate generic data based on ID
            try:
                album_num = int(spotify_id.replace("album", ""))
                mock_album.name = f"Album {album_num}"
                mock_album.release_date = f"202{album_num % 5}-0{(album_num % 9) + 1}-01"
            except ValueError:
                mock_album.name = f"Album {spotify_id}"
                mock_album.release_date = "2020-01-01"
            mock_album.external_urls = {"spotify": f"https://spotify.com/album/{spotify_id}"}
            mock_album.images = []

        return mock_album

    return mock_get_album


def create_mock_tracks_getter(
    track_data: dict[str, dict[str, str | list[str]]] | None = None,
) -> Callable[[list[str]], Awaitable[list[Mock]]]:
    """
    Create a mock get_tracks function for MottleSpotifyClient.

    Args:
        track_data: Optional dict mapping track_id to dict with 'name' and 'artists' (list of artist names)
                   Example: {"track1": {"name": "Blinding Lights", "artists": ["The Weeknd"]}}
                   If not provided or track_id not found, generates generic data based on ID.

    Returns:
        An async function that returns a list of mocked FullTrack objects
    """
    if track_data is None:
        track_data = {}

    async def mock_get_tracks(track_ids: list[str]) -> list[Mock]:
        tracks = []
        for track_id in track_ids:
            data = track_data.get(track_id)
            mock_track = Mock(spec=FullTrack)

            if data:
                mock_track.name = data.get("name", f"Track {track_id}")
                artist_names = data.get("artists", [f"Artist {track_id}"])
            else:
                # Generate generic data based on ID (e.g., "track6" -> "Track 6", "Artist 6")
                try:
                    track_num = int(track_id.replace("track", ""))
                    mock_track.name = f"Track {track_num}"
                    artist_names = [f"Artist {track_num}"]
                except ValueError:
                    mock_track.name = f"Track {track_id}"
                    artist_names = [f"Artist {track_id}"]

            mock_artists = []
            for artist_name in artist_names:
                mock_artist = Mock(spec=FullArtist)
                mock_artist.name = artist_name
                mock_artists.append(mock_artist)
            mock_track.artists = mock_artists

            tracks.append(mock_track)
        return tracks

    return mock_get_tracks


def create_mock_albums_getter(
    album_data: dict[str, dict[str, str]] | None = None,
) -> Callable[[list[str]], Awaitable[list[Mock]]]:
    """
    Create a mock get_albums function for MottleSpotifyClient.

    Args:
        album_data: Optional dict mapping album_id to dict with 'name' and 'release_date'
                   Example: {"album1": {"name": "After Hours", "release_date": "2020-03-20"}}
                   If not provided or album_id not found, generates generic data based on ID.

    Returns:
        An async function that returns a list of mocked FullAlbum objects
    """
    if album_data is None:
        album_data = {}

    async def mock_get_albums(album_ids: list[str]) -> list[Mock]:
        albums = []
        for album_id in album_ids:
            data = album_data.get(album_id)
            mock_album = Mock(spec=FullAlbum)

            if data:
                mock_album.name = data.get("name", f"Album {album_id}")
                mock_album.release_date = data.get("release_date", "2020-01-01")
            else:
                # Generate generic data based on ID (e.g., "album3" -> "Album 3")
                try:
                    album_num = int(album_id.replace("album", ""))
                    mock_album.name = f"Album {album_num}"
                    mock_album.release_date = f"202{album_num % 5}-0{(album_num % 9) + 1}-01"
                except ValueError:
                    mock_album.name = f"Album {album_id}"
                    mock_album.release_date = "2020-01-01"

            mock_album.external_urls = {"spotify": f"https://spotify.com/album/{album_id}"}
            mock_album.images = []
            albums.append(mock_album)
        return albums

    return mock_get_albums
