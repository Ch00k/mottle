import datetime
import uuid

import pytest
from django.conf import settings
from django.contrib.gis.geos import Point

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
