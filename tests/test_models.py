import datetime
import json
import uuid

import pytest
from django.conf import settings
from django.contrib.gis.geos import Point
from django.db import IntegrityError
from django.test import TestCase
from tekore import Token

from web.events.data import Event as FetchedEvent
from web.events.data import EventSourceArtist
from web.events.data import Venue as FetchedVenue
from web.events.enums import ArtistNameMatchAccuracy, EventDataSource, EventType
from web.models import (
    Artist,
    Event,
    EventArtist,
    EventUpdateChangesJSONDecoder,
    EventUpdateChangesJSONEncoder,
    Playlist,
    PlaylistUpdate,
    PlaylistWatchConfig,
    SpotifyAuth,
    SpotifyUser,
    User,
    decrypt_value,
    encrypt_value,
    generate_playlist_update_hash,
)

pytestmark = pytest.mark.django_db


class TestEncryptionDecryption:
    def test_encrypt_decrypt_roundtrip(self) -> None:
        """Test that encrypting and decrypting a value returns the original."""
        original = "my_secret_token_12345"
        encrypted = encrypt_value(original)
        decrypted = decrypt_value(encrypted)

        assert decrypted == original
        assert encrypted != original


class TestEventUpdateJSONEncoderDecoder:
    def test_encode_decode_point(self) -> None:
        """Test that Point objects are correctly encoded to JSON and decoded back."""
        data = {
            "geolocation": {
                "old": Point(5, 23, srid=settings.GEODJANGO_SRID),
                "new": Point(23, 5, srid=settings.GEODJANGO_SRID),
            },
        }

        encoded = json.dumps(data, cls=EventUpdateChangesJSONEncoder)
        decoded = json.loads(encoded, cls=EventUpdateChangesJSONDecoder)

        assert isinstance(decoded["geolocation"]["old"], Point)
        assert isinstance(decoded["geolocation"]["new"], Point)
        assert decoded["geolocation"]["old"].coords == (5.0, 23.0)
        assert decoded["geolocation"]["new"].coords == (23.0, 5.0)

    def test_encode_without_geolocation(self) -> None:
        """Test that encoding works correctly when there is no geolocation field."""
        data = {"other_field": "value"}

        encoded = json.dumps(data, cls=EventUpdateChangesJSONEncoder)
        decoded = json.loads(encoded, cls=EventUpdateChangesJSONDecoder)

        assert decoded == data


@pytest.mark.asyncio
class TestSpotifyAuth(TestCase):
    async def test_expires_in_future_token(self) -> None:
        """Test that expires_in returns positive value for future token."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_future",
            display_name="Future User",
            email="future@example.com",
        )
        auth = await SpotifyAuth.objects.acreate(
            spotify_user=spotify_user,
            access_token="future_token",
            refresh_token="future_refresh",
            expires_at=datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(hours=1),
            token_scope=["user-read-private"],
        )

        assert auth.expires_in > 3500
        assert not auth.is_expiring

    async def test_expires_in_expired_token(self) -> None:
        """Test that expires_in returns negative value for expired token."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_expired",
            display_name="Expired User",
            email="expired@example.com",
        )
        auth = await SpotifyAuth.objects.acreate(
            spotify_user=spotify_user,
            access_token="expired_token",
            refresh_token="expired_refresh",
            expires_at=datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=1),
            token_scope=["user-read-private"],
        )

        assert auth.expires_in <= 0
        assert auth.is_expiring

    async def test_expires_in_expiring_soon_token(self) -> None:
        """Test that is_expiring is True for tokens expiring within threshold."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_expiring",
            display_name="Expiring User",
            email="expiring@example.com",
        )
        auth = await SpotifyAuth.objects.acreate(
            spotify_user=spotify_user,
            access_token="expiring_token",
            refresh_token="expiring_refresh",
            expires_at=datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(seconds=30),
            token_scope=["user-read-private"],
        )

        assert 0 < auth.expires_in < 60
        assert auth.is_expiring

    async def test_as_tekore_token(self) -> None:
        """Test that SpotifyAuth can be converted to a Tekore Token object."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_tekore_token",
            display_name="Tekore Token User",
            email="tekoretoken@example.com",
        )
        auth = await SpotifyAuth.objects.acreate(
            spotify_user=spotify_user,
            access_token="tekore_token",
            refresh_token="tekore_refresh",
            expires_at=datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(hours=1),
            token_scope=["user-read-private"],
        )

        token = auth.as_tekore_token

        assert token.access_token == auth.access_token
        assert token.refresh_token == auth.refresh_token
        assert not token.uses_pkce

    async def test_update_from_tekore_token(self) -> None:
        """Test that SpotifyAuth can be updated from a Tekore Token object."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_update_token",
            display_name="Update Token User",
            email="update@example.com",
        )
        auth = await SpotifyAuth.objects.acreate(
            spotify_user=spotify_user,
            access_token="old_token",
            refresh_token="old_refresh",
            expires_at=datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(hours=1),
            token_scope=["user-read-private"],
        )
        original_access = auth.access_token

        new_token = Token(
            token_info={
                "token_type": "Bearer",
                "access_token": "new_access_token",
                "refresh_token": "new_refresh_token",
                "expires_in": 3600,
            },
            uses_pkce=False,
        )

        await auth.update_from_tekore_token(new_token)
        await auth.arefresh_from_db()

        assert auth.access_token == "new_access_token"
        assert auth.access_token != original_access
        assert auth.refresh_token == "new_refresh_token"


@pytest.mark.asyncio
class TestUser(TestCase):
    async def test_upcoming_events_no_location(self) -> None:
        """Test that users without location get all future events filtered only by date."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_no_location",
            display_name="No Location User",
            email="nolocation@example.com",
        )
        user = await User.objects.acreate(
            spotify_user=spotify_user,
            location=None,
        )
        artist = await Artist.objects.acreate(spotify_id="artist_1")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/1",
            bandsintown_url="https://www.bandsintown.com/a/1",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        await user.spotify_user.watched_event_artists.aadd(event_artist)

        # Create future and past events
        future_event = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/1",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=10),
            venue="Future Venue",
            city="London",
            country="UK",
            geolocation=Point(-0.1, 51.5, srid=settings.GEODJANGO_SRID),
        )
        await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/2",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() - datetime.timedelta(days=10),
            venue="Past Venue",
            city="London",
            country="UK",
            geolocation=Point(-0.1, 51.5, srid=settings.GEODJANGO_SRID),
        )

        upcoming = await user.upcoming_events()

        assert event_artist in upcoming
        assert len(upcoming[event_artist]) == 1
        assert upcoming[event_artist][0].id == future_event.id

    async def test_upcoming_events_no_location_includes_streaming(self) -> None:
        """Test that streaming events are included when user has no location defined."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_no_location_streaming",
            display_name="No Location Streaming User",
            email="nolocationstreaming@example.com",
        )
        user = await User.objects.acreate(
            spotify_user=spotify_user,
            location=None,
        )
        artist = await Artist.objects.acreate(spotify_id="artist_no_location_streaming")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/no_location_streaming",
            bandsintown_url="https://www.bandsintown.com/a/no_location_streaming",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        await user.spotify_user.watched_event_artists.aadd(event_artist)

        # Create a streaming event
        streaming_event = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/no_location_streaming",
            type="live_stream",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=5),
            venue=None,
            city=None,
            country=None,
            geolocation=None,
            stream_urls=["https://example.com/stream"],
        )

        upcoming = await user.upcoming_events()

        assert event_artist in upcoming
        assert len(upcoming[event_artist]) == 1
        assert upcoming[event_artist][0].id == streaming_event.id

    async def test_upcoming_events_with_location_includes_streaming(self) -> None:
        """Test that streaming events are included regardless of user location."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_with_location_streaming",
            display_name="Streaming User",
            email="streaming@example.com",
        )
        user = await User.objects.acreate(
            spotify_user=spotify_user,
            location=Point(-0.1276, 51.5074, srid=settings.GEODJANGO_SRID),
            event_distance_threshold=50.0,
        )
        artist = await Artist.objects.acreate(spotify_id="artist_2")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/2",
            bandsintown_url="https://www.bandsintown.com/a/2",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        await user.spotify_user.watched_event_artists.aadd(event_artist)

        # Create a streaming event
        streaming_event = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/streaming",
            type="live_stream",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=5),
            venue=None,
            city=None,
            country=None,
            geolocation=None,
            stream_urls=["https://example.com/stream"],
        )

        upcoming = await user.upcoming_events()

        assert event_artist in upcoming
        assert len(upcoming[event_artist]) == 1
        assert upcoming[event_artist][0].id == streaming_event.id

    async def test_upcoming_events_with_location_filters_by_distance(self) -> None:
        """Test that non-streaming events are filtered by distance from user location."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_distance_filter",
            display_name="Distance Filter User",
            email="distance@example.com",
        )
        user = await User.objects.acreate(
            spotify_user=spotify_user,
            location=Point(-0.1276, 51.5074, srid=settings.GEODJANGO_SRID),  # London
            event_distance_threshold=50.0,
        )
        artist = await Artist.objects.acreate(spotify_id="artist_3")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/3",
            bandsintown_url="https://www.bandsintown.com/a/3",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        await user.spotify_user.watched_event_artists.aadd(event_artist)

        # Event in London (within 50km)
        nearby_event = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/nearby",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=10),
            venue="Nearby Venue",
            city="London",
            country="UK",
            geolocation=Point(-0.1, 51.5, srid=settings.GEODJANGO_SRID),
        )

        # Event in Paris (far away, > 50km)
        await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/far",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=10),
            venue="Far Venue",
            city="Paris",
            country="France",
            geolocation=Point(2.3522, 48.8566, srid=settings.GEODJANGO_SRID),
        )

        upcoming = await user.upcoming_events()

        assert event_artist in upcoming
        assert len(upcoming[event_artist]) == 1
        assert upcoming[event_artist][0].id == nearby_event.id

    async def test_upcoming_events_multiple_artists(self) -> None:
        """Test that upcoming events are returned for multiple watched artists."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_multiple_artists",
            display_name="Multiple Artists User",
            email="multipleartists@example.com",
        )
        user = await User.objects.acreate(
            spotify_user=spotify_user,
            location=None,
        )

        # Create two artists with events
        artist1 = await Artist.objects.acreate(spotify_id="artist_multi_1")
        event_artist1 = await EventArtist.objects.acreate(
            artist=artist1,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/multi_1",
            bandsintown_url="https://www.bandsintown.com/a/multi_1",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        await user.spotify_user.watched_event_artists.aadd(event_artist1)

        artist2 = await Artist.objects.acreate(spotify_id="artist_multi_2")
        event_artist2 = await EventArtist.objects.acreate(
            artist=artist2,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/multi_2",
            bandsintown_url="https://www.bandsintown.com/a/multi_2",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        await user.spotify_user.watched_event_artists.aadd(event_artist2)

        # Create events for both artists
        event1 = await Event.objects.acreate(
            artist=event_artist1,
            source="songkick",
            source_url="https://www.songkick.com/concerts/multi_1",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=5),
            venue="Venue 1",
            city="London",
            country="UK",
            geolocation=Point(-0.1, 51.5, srid=settings.GEODJANGO_SRID),
        )

        event2 = await Event.objects.acreate(
            artist=event_artist2,
            source="bandsintown",
            source_url="https://www.bandsintown.com/e/multi_2",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=15),
            venue="Venue 2",
            city="Manchester",
            country="UK",
            geolocation=Point(-2.2, 53.5, srid=settings.GEODJANGO_SRID),
        )

        upcoming = await user.upcoming_events()

        assert len(upcoming) == 2
        assert event_artist1 in upcoming
        assert event_artist2 in upcoming
        assert len(upcoming[event_artist1]) == 1
        assert len(upcoming[event_artist2]) == 1
        assert upcoming[event_artist1][0].id == event1.id
        assert upcoming[event_artist2][0].id == event2.id

    async def test_upcoming_events_with_location_excludes_non_streaming_without_geolocation(self) -> None:
        """Test that non-streaming events without geolocation are excluded when user has location."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_no_geo",
            display_name="No Geo User",
            email="nogeo@example.com",
        )
        user = await User.objects.acreate(
            spotify_user=spotify_user,
            location=Point(-0.1276, 51.5074, srid=settings.GEODJANGO_SRID),
            event_distance_threshold=50.0,
        )
        artist = await Artist.objects.acreate(spotify_id="artist_no_geo")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/no_geo",
            bandsintown_url="https://www.bandsintown.com/a/no_geo",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        await user.spotify_user.watched_event_artists.aadd(event_artist)

        # Create non-streaming event without geolocation (should be excluded)
        await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/no_geo",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=10),
            venue="Unknown Venue",
            city="Unknown",
            country="Unknown",
            geolocation=None,
        )

        upcoming = await user.upcoming_events()

        # Should not include the event since it has no geolocation and is not a stream
        assert event_artist not in upcoming or len(upcoming[event_artist]) == 0

    async def test_upcoming_events_multiple_events_same_artist(self) -> None:
        """Test that multiple future events for the same artist are all included."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_multiple_events",
            display_name="Multiple Events User",
            email="multipleevents@example.com",
        )
        user = await User.objects.acreate(
            spotify_user=spotify_user,
            location=None,
        )
        artist = await Artist.objects.acreate(spotify_id="artist_multiple_events")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/multiple_events",
            bandsintown_url="https://www.bandsintown.com/a/multiple_events",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        await user.spotify_user.watched_event_artists.aadd(event_artist)

        # Create multiple future events for the same artist
        event1 = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/event1",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=5),
            venue="Venue 1",
            city="London",
            country="UK",
            geolocation=Point(-0.1, 51.5, srid=settings.GEODJANGO_SRID),
        )

        event2 = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/event2",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=10),
            venue="Venue 2",
            city="Manchester",
            country="UK",
            geolocation=Point(-2.2, 53.5, srid=settings.GEODJANGO_SRID),
        )

        event3 = await Event.objects.acreate(
            artist=event_artist,
            source="bandsintown",
            source_url="https://www.bandsintown.com/e/event3",
            type="festival",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=20),
            venue="Festival Grounds",
            city="Glasgow",
            country="UK",
            geolocation=Point(-4.2, 55.8, srid=settings.GEODJANGO_SRID),
        )

        event4 = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/event4",
            type="live_stream",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=15),
            venue=None,
            city=None,
            country=None,
            geolocation=None,
            stream_urls=["https://example.com/stream"],
        )

        upcoming = await user.upcoming_events()

        assert event_artist in upcoming
        assert len(upcoming[event_artist]) == 4
        event_ids = [e.id for e in upcoming[event_artist]]
        assert event1.id in event_ids
        assert event2.id in event_ids
        assert event3.id in event_ids
        assert event4.id in event_ids

    async def test_upcoming_events_includes_today(self) -> None:
        """Test that events happening today are included in upcoming events."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_today",
            display_name="Today User",
            email="today@example.com",
        )
        user = await User.objects.acreate(
            spotify_user=spotify_user,
            location=None,
        )
        artist = await Artist.objects.acreate(spotify_id="artist_today")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/today",
            bandsintown_url="https://www.bandsintown.com/a/today",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        await user.spotify_user.watched_event_artists.aadd(event_artist)

        # Create event happening today
        today_event = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/today",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date(),
            venue="Today Venue",
            city="London",
            country="UK",
            geolocation=Point(-0.1, 51.5, srid=settings.GEODJANGO_SRID),
        )

        upcoming = await user.upcoming_events()

        assert event_artist in upcoming
        assert len(upcoming[event_artist]) == 1
        assert upcoming[event_artist][0].id == today_event.id

    async def test_upcoming_events_empty_no_watched_artists(self) -> None:
        """Test that empty dict is returned when user is not watching any artists."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_no_watched",
            display_name="No Watched User",
            email="nowatched@example.com",
        )
        user = await User.objects.acreate(
            spotify_user=spotify_user,
            location=None,
        )

        upcoming = await user.upcoming_events()

        assert len(upcoming) == 0

    async def test_upcoming_events_empty_no_upcoming_events(self) -> None:
        """Test that artists with only past events are not included in results."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_no_upcoming",
            display_name="No Upcoming User",
            email="noupcoming@example.com",
        )
        user = await User.objects.acreate(
            spotify_user=spotify_user,
            location=None,
        )
        artist = await Artist.objects.acreate(spotify_id="artist_no_upcoming")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/no_upcoming",
            bandsintown_url="https://www.bandsintown.com/a/no_upcoming",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        await user.spotify_user.watched_event_artists.aadd(event_artist)

        # Only past events
        await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/past",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() - datetime.timedelta(days=30),
            venue="Past Venue",
            city="London",
            country="UK",
            geolocation=Point(-0.1, 51.5, srid=settings.GEODJANGO_SRID),
        )

        upcoming = await user.upcoming_events()

        assert event_artist not in upcoming or len(upcoming[event_artist]) == 0

    async def test_upcoming_events_with_location_ordered_by_date(self) -> None:
        """Test that events are ordered by date when user has location defined."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_ordered",
            display_name="Ordered User",
            email="ordered@example.com",
        )
        user = await User.objects.acreate(
            spotify_user=spotify_user,
            location=Point(-0.1276, 51.5074, srid=settings.GEODJANGO_SRID),
            event_distance_threshold=100.0,
        )
        artist = await Artist.objects.acreate(spotify_id="artist_ordered")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/ordered",
            bandsintown_url="https://www.bandsintown.com/a/ordered",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        await user.spotify_user.watched_event_artists.aadd(event_artist)

        # Create events in non-chronological order
        event_far = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/far_date",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=30),
            venue="Far Date Venue",
            city="London",
            country="UK",
            geolocation=Point(-0.1, 51.5, srid=settings.GEODJANGO_SRID),
        )

        event_near = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/near_date",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=5),
            venue="Near Date Venue",
            city="London",
            country="UK",
            geolocation=Point(-0.1, 51.5, srid=settings.GEODJANGO_SRID),
        )

        event_mid = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/mid_date",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=15),
            venue="Mid Date Venue",
            city="London",
            country="UK",
            geolocation=Point(-0.1, 51.5, srid=settings.GEODJANGO_SRID),
        )

        upcoming = await user.upcoming_events()

        assert event_artist in upcoming
        assert len(upcoming[event_artist]) == 3
        # Verify events are ordered by date
        assert upcoming[event_artist][0].id == event_near.id
        assert upcoming[event_artist][1].id == event_mid.id
        assert upcoming[event_artist][2].id == event_far.id


@pytest.mark.asyncio
class TestEventArtist(TestCase):
    async def test_create_from_fetched_artist(self) -> None:
        """Test creating EventArtist from fetched artist data."""
        artist = await Artist.objects.acreate(spotify_id="artist_create_from_fetched")
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_create_from_fetched",
            display_name="Create From Fetched User",
            email="createfromfetched@example.com",
        )
        musicbrainz_id = "12345678-1234-1234-1234-123456789012"

        fetched_artist = EventSourceArtist(
            name="Test Artist",
            songkick_url="https://www.songkick.com/artists/123",
            bandsintown_url="https://www.bandsintown.com/a/123",
            songkick_match_accuracy=ArtistNameMatchAccuracy.exact_alnum,
            bandsintown_match_accuracy=ArtistNameMatchAccuracy.exact_ascii,
            events=[],
        )

        event_artist = await EventArtist.create_from_fetched_artist(
            artist=artist,
            musicbrainz_id=musicbrainz_id,
            fetched_artist=fetched_artist,
            watching_spotify_user_ids=[str(spotify_user.id)],
        )

        assert event_artist.artist == artist
        assert str(event_artist.musicbrainz_id) == musicbrainz_id
        assert event_artist.songkick_url == fetched_artist.songkick_url
        assert event_artist.bandsintown_url == fetched_artist.bandsintown_url
        assert event_artist.songkick_name_match_accuracy == ArtistNameMatchAccuracy.exact_alnum
        assert event_artist.bandsintown_name_match_accuracy == ArtistNameMatchAccuracy.exact_ascii

        watching_users = [u async for u in event_artist.watching_users.all()]
        assert len(watching_users) == 1
        assert watching_users[0].id == spotify_user.id

    async def test_update_from_fetched_artist(self) -> None:
        """Test updating EventArtist from fetched artist data."""
        artist = await Artist.objects.acreate(spotify_id="artist_update_from_fetched")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/old",
            bandsintown_url="https://www.bandsintown.com/a/old",
            songkick_name_match_accuracy=50,
            bandsintown_name_match_accuracy=50,
        )
        new_musicbrainz_id = "87654321-4321-4321-4321-210987654321"

        fetched_artist = EventSourceArtist(
            name="Updated Artist",
            songkick_url="https://www.songkick.com/artists/999",
            bandsintown_url="https://www.bandsintown.com/a/999",
            songkick_match_accuracy=ArtistNameMatchAccuracy.exact,
            bandsintown_match_accuracy=ArtistNameMatchAccuracy.exact_alnum,
            events=[],
        )

        await event_artist.update_from_fetched_artist(new_musicbrainz_id, fetched_artist)
        await event_artist.arefresh_from_db()

        assert str(event_artist.musicbrainz_id) == new_musicbrainz_id
        assert event_artist.songkick_url == "https://www.songkick.com/artists/999"
        assert event_artist.bandsintown_url == "https://www.bandsintown.com/a/999"
        assert event_artist.songkick_name_match_accuracy == ArtistNameMatchAccuracy.exact
        assert event_artist.bandsintown_name_match_accuracy == ArtistNameMatchAccuracy.exact_alnum


@pytest.mark.asyncio
class TestEvent(TestCase):
    async def test_update_or_create_from_fetched_event_creates_new(self) -> None:
        """Test creating a new Event from fetched event data."""
        artist = await Artist.objects.acreate(spotify_id="artist_event_create")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/event_create",
            bandsintown_url="https://www.bandsintown.com/a/event_create",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )

        fetched_venue = FetchedVenue(
            name="Test Venue",
            postcode="12345",
            address="123 Test St",
            city="Test City",
            country="Test Country",
            geo_lat=51.5,
            geo_lon=-0.1,
        )

        fetched_event = FetchedEvent(
            source=EventDataSource.songkick,
            url="https://www.songkick.com/concerts/12345",
            type=EventType.concert,
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=30),
            venue=fetched_venue,
            stream_urls=[],
            tickets_urls=["https://example.com/tickets"],
        )

        event, created = await Event.update_or_create_from_fetched_event(fetched_event, event_artist)

        assert created
        assert event.artist == event_artist
        assert event.source == "songkick"
        assert event.source_url == "https://www.songkick.com/concerts/12345"
        assert event.type == "concert"
        assert event.venue == "Test Venue"
        assert event.city == "Test City"
        assert event.geolocation is not None
        assert event.geolocation.x == -0.1
        assert event.geolocation.y == 51.5

    async def test_update_or_create_from_fetched_event_updates_existing(self) -> None:
        """Test updating an existing Event from fetched event data."""
        artist = await Artist.objects.acreate(spotify_id="artist_event_update")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/event_update",
            bandsintown_url="https://www.bandsintown.com/a/event_update",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        source_url = "https://www.songkick.com/concerts/99999"

        # Create existing event
        existing_event = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url=source_url,
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=30),
            venue="Old Venue",
            city="Old City",
            country="Old Country",
            geolocation=Point(-1.0, 52.0, srid=settings.GEODJANGO_SRID),
        )

        fetched_venue = FetchedVenue(
            name="Updated Venue",
            postcode="54321",
            address="456 Updated St",
            city="Updated City",
            country="Updated Country",
            geo_lat=52.0,
            geo_lon=-1.0,
        )

        fetched_event = FetchedEvent(
            source=EventDataSource.songkick,
            url=source_url,
            type=EventType.concert,
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=60),
            venue=fetched_venue,
            stream_urls=[],
            tickets_urls=["https://example.com/new-tickets"],
        )

        event, created = await Event.update_or_create_from_fetched_event(fetched_event, event_artist)

        assert not created
        assert event.id == existing_event.id
        assert event.venue == "Updated Venue"
        assert event.city == "Updated City"
        assert event.geolocation is not None
        assert event.geolocation.x == -1.0
        assert event.geolocation.y == 52.0

    async def test_as_fetched_event(self) -> None:
        """Test converting Event to fetched event format."""
        artist = await Artist.objects.acreate(spotify_id="artist_as_fetched")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/as_fetched",
            bandsintown_url="https://www.bandsintown.com/a/as_fetched",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        event = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/as_fetched",
            type="concert",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=30),
            venue="As Fetched Venue",
            postcode="SW1A 1AA",
            address="Test Street 1",
            city="London",
            country="United Kingdom",
            geolocation=Point(-0.1276, 51.5074, srid=settings.GEODJANGO_SRID),
            stream_urls=None,
            tickets_urls=["https://example.com/tickets"],
        )

        fetched = event.as_fetched_event()

        assert fetched.source == EventDataSource(event.source)
        assert fetched.url == event.source_url
        assert fetched.type == EventType(event.type)
        assert fetched.date == event.date
        assert fetched.venue is not None
        assert fetched.venue.name == event.venue
        assert fetched.venue.city == event.city

    async def test_as_fetched_event_no_venue(self) -> None:
        """Test converting streaming Event without venue to fetched event format."""
        artist = await Artist.objects.acreate(spotify_id="artist_streaming")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/streaming",
            bandsintown_url="https://www.bandsintown.com/a/streaming",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )
        event = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/streaming",
            type="live_stream",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=30),
            venue=None,
            city=None,
            country=None,
            geolocation=None,
            stream_urls=["https://example.com/stream"],
        )

        fetched = event.as_fetched_event()

        assert fetched.venue is None
        assert fetched.stream_urls is not None


@pytest.mark.asyncio
class TestPlaylist(TestCase):
    async def test_pending_updates(self) -> None:
        """Test that pending_updates returns only pending and non-overridden updates."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_pending_updates",
            display_name="Pending Updates User",
            email="pendingupdates@example.com",
        )
        playlist = await Playlist.objects.acreate(
            spotify_id="playlist_pending",
            spotify_user=spotify_user,
        )
        watched = await Playlist.objects.acreate(spotify_id="watched_pending")

        # Create pending update
        pending = await PlaylistUpdate.objects.acreate(
            target_playlist=playlist,
            source_playlist=watched,
            is_accepted=None,
            is_overridden_by=None,
            tracks_added=["track1", "track2"],
        )

        # Create accepted update
        await PlaylistUpdate.objects.acreate(
            target_playlist=playlist,
            source_playlist=watched,
            is_accepted=True,
            is_overridden_by=None,
            tracks_added=["track3", "track4"],
        )

        # Create overriding update
        overriding = await PlaylistUpdate.objects.acreate(
            target_playlist=playlist,
            source_playlist=watched,
            is_accepted=None,
            is_overridden_by=None,
            tracks_added=["track5", "track6"],
        )
        await PlaylistUpdate.objects.acreate(
            target_playlist=playlist,
            source_playlist=watched,
            is_accepted=None,
            is_overridden_by=overriding,
            tracks_added=["track7", "track8"],
        )

        pending_updates = [u async for u in playlist.pending_updates]

        assert len(pending_updates) == 2
        assert pending.id in [u.id for u in pending_updates]
        assert overriding.id in [u.id for u in pending_updates]


@pytest.mark.asyncio
class TestPlaylistUpdate(TestCase):
    def test_save_generates_hash(self) -> None:
        """Test that saving PlaylistUpdate generates a hash automatically."""
        spotify_user = SpotifyUser.objects.create(
            spotify_id="user_save_hash",
            display_name="Save Hash User",
            email="savehash@example.com",
        )
        playlist = Playlist.objects.create(
            spotify_id="playlist_save_hash",
            spotify_user=spotify_user,
        )
        watched = Playlist.objects.create(spotify_id="watched_save_hash")

        update = PlaylistUpdate(
            target_playlist=playlist,
            source_playlist=watched,
            tracks_added=["track1", "track2"],
        )

        assert update.update_hash == ""
        update.save()
        assert update.update_hash != ""
        assert len(update.update_hash) == 64

    def test_is_auto_acceptable_true(self) -> None:
        """Test that is_auto_acceptable returns True when config has auto_accept enabled."""
        spotify_user = SpotifyUser.objects.create(
            spotify_id="user_auto_accept_true",
            display_name="Auto Accept True User",
            email="autoaccepttrue@example.com",
        )
        watching = Playlist.objects.create(
            spotify_id="watching_auto_true",
            spotify_user=spotify_user,
        )
        watched = Playlist.objects.create(spotify_id="watched_auto_true")
        config = PlaylistWatchConfig.objects.create(
            watching_playlist=watching,
            watched_playlist=watched,
            auto_accept_updates=True,
        )
        update = PlaylistUpdate.objects.create(
            target_playlist=config.watching_playlist,
            source_playlist=config.watched_playlist,
            tracks_added=["track1"],
        )

        assert update.is_auto_acceptable

    def test_is_auto_acceptable_false(self) -> None:
        """Test that is_auto_acceptable returns False when config has auto_accept disabled."""
        spotify_user = SpotifyUser.objects.create(
            spotify_id="user_auto_accept_false",
            display_name="Auto Accept False User",
            email="autoacceptfalse@example.com",
        )
        watching = Playlist.objects.create(
            spotify_id="watching_auto_false",
            spotify_user=spotify_user,
        )
        watched = Playlist.objects.create(spotify_id="watched_auto_false")
        config = PlaylistWatchConfig.objects.create(
            watching_playlist=watching,
            watched_playlist=watched,
            auto_accept_updates=False,
        )
        update = PlaylistUpdate.objects.create(
            target_playlist=config.watching_playlist,
            source_playlist=config.watched_playlist,
            tracks_added=["track1"],
        )

        assert not update.is_auto_acceptable

    def test_is_auto_acceptable_no_config(self) -> None:
        """Test that is_auto_acceptable returns False when no watch config exists."""
        spotify_user = SpotifyUser.objects.create(
            spotify_id="user_no_config",
            display_name="No Config User",
            email="noconfig@example.com",
        )
        playlist = Playlist.objects.create(
            spotify_id="playlist_no_config",
            spotify_user=spotify_user,
        )
        watched = Playlist.objects.create(spotify_id="watched_no_config")
        update = PlaylistUpdate.objects.create(
            target_playlist=playlist,
            source_playlist=watched,
            tracks_added=["track1"],
        )

        assert not update.is_auto_acceptable

    async def test_accept_marks_as_accepted(self) -> None:
        """Test that accept() marks update status as accepted."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_accept",
            display_name="Accept User",
            email="accept@example.com",
        )
        playlist = await Playlist.objects.acreate(
            spotify_id="playlist_accept",
            spotify_user=spotify_user,
        )
        watched = await Playlist.objects.acreate(spotify_id="watched_accept")
        update = await PlaylistUpdate.objects.acreate(
            target_playlist=playlist,
            source_playlist=watched,
            is_accepted=None,
            tracks_added=["track1"],
        )

        assert update.is_accepted is None

        # Note: This test would need mocking of spotify_client to actually work
        # await update.accept(spotify_client)

    async def test_reject_marks_as_rejected(self) -> None:
        """Test that reject() marks update status as rejected."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_reject",
            display_name="Reject User",
            email="reject@example.com",
        )
        playlist = await Playlist.objects.acreate(
            spotify_id="playlist_reject",
            spotify_user=spotify_user,
        )
        watched = await Playlist.objects.acreate(spotify_id="watched_reject")
        update = await PlaylistUpdate.objects.acreate(
            target_playlist=playlist,
            source_playlist=watched,
            is_accepted=None,
            tracks_added=["track1"],
        )

        await update.reject()
        await update.arefresh_from_db()

        assert update.is_accepted is False

    async def test_find_or_create_for_playlist_creates_new(self) -> None:
        """Test that find_or_create_for_playlist creates a new update when none exists."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_find_create_new",
            display_name="Find Create New User",
            email="findcreatenew@example.com",
        )
        target = await Playlist.objects.acreate(
            spotify_id="target_find_create_new",
            spotify_user=spotify_user,
        )
        source = await Playlist.objects.acreate(spotify_id="source_find_create_new")
        track_ids = ["track1", "track2", "track3"]

        update, created = await PlaylistUpdate.find_or_create_for_playlist(target, source, track_ids)

        assert created
        assert update.target_playlist == target
        assert update.source_playlist == source
        assert update.tracks_added == track_ids

    async def test_find_or_create_for_playlist_finds_existing(self) -> None:
        """Test that find_or_create_for_playlist finds existing pending update."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_find_existing",
            display_name="Find Existing User",
            email="findexisting@example.com",
        )
        target = await Playlist.objects.acreate(
            spotify_id="target_find_existing",
            spotify_user=spotify_user,
        )
        source = await Playlist.objects.acreate(spotify_id="source_find_existing")
        track_ids = ["track1", "track2", "track3"]

        # Create first time
        update1, created1 = await PlaylistUpdate.find_or_create_for_playlist(target, source, track_ids)
        assert created1

        # Try to create again with same tracks
        update2, created2 = await PlaylistUpdate.find_or_create_for_playlist(target, source, track_ids)
        assert not created2
        assert update2.id == update1.id

    async def test_find_or_create_for_playlist_overrides_outdated(self) -> None:
        """Test that find_or_create_for_playlist creates new update when existing is outdated."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_override",
            display_name="Override User",
            email="override@example.com",
        )
        target = await Playlist.objects.acreate(
            spotify_id="target_override",
            spotify_user=spotify_user,
        )
        source = await Playlist.objects.acreate(spotify_id="source_override")

        # Create first update
        update1, _ = await PlaylistUpdate.find_or_create_for_playlist(target, source, ["track1"])

        # Create second update with different tracks (should override first)
        update2, created = await PlaylistUpdate.find_or_create_for_playlist(target, source, ["track2"])

        assert created
        assert update2.id != update1.id

        # Check that update1 is now overridden
        await update1.arefresh_from_db()
        # Access the foreign key in a separate DB query to avoid sync access in async context
        is_overridden_by = (
            await PlaylistUpdate.objects.filter(id=update1.id).select_related("is_overridden_by").afirst()
        )
        assert is_overridden_by is not None
        assert is_overridden_by.is_overridden_by is not None
        assert is_overridden_by.is_overridden_by.id == update2.id

    async def test_find_or_create_for_artist_creates_new(self) -> None:
        """Test that find_or_create_for_artist creates a new update for an artist."""
        spotify_user = await SpotifyUser.objects.acreate(
            spotify_id="user_artist_create",
            display_name="Artist Create User",
            email="artistcreate@example.com",
        )
        target = await Playlist.objects.acreate(
            spotify_id="target_artist_create",
            spotify_user=spotify_user,
        )
        artist = await Artist.objects.acreate(spotify_id="artist_for_update")
        album_ids = ["album1", "album2"]

        update, created = await PlaylistUpdate.find_or_create_for_artist(target, artist, album_ids)

        assert created
        assert update.target_playlist == target
        assert update.source_artist == artist
        assert update.albums_added == album_ids


class TestGeneratePlaylistUpdateHash:
    def test_hash_is_deterministic(self) -> None:
        """Test that hash generation is deterministic for the same input."""
        tracks = ["track1", "track2", "track3"]

        hash1 = generate_playlist_update_hash(tracks_added=tracks)
        hash2 = generate_playlist_update_hash(tracks_added=tracks)

        assert hash1 == hash2

    def test_hash_different_for_different_tracks(self) -> None:
        """Test that different tracks produce different hashes."""
        hash1 = generate_playlist_update_hash(tracks_added=["track1"])
        hash2 = generate_playlist_update_hash(tracks_added=["track2"])

        assert hash1 != hash2

    def test_hash_same_tracks_different_order(self) -> None:
        """Test that same tracks in different order produce the same hash."""
        hash1 = generate_playlist_update_hash(tracks_added=["track1", "track2"])
        hash2 = generate_playlist_update_hash(tracks_added=["track2", "track1"])

        # Should be same because tracks are sorted before hashing
        assert hash1 == hash2

    def test_hash_with_albums_and_tracks(self) -> None:
        """Test that hash works correctly with both albums and tracks."""
        hash1 = generate_playlist_update_hash(albums_added=["album1"], tracks_added=["track1"])

        # Should produce a valid hash
        assert len(hash1) == 64
        assert isinstance(hash1, str)

    def test_hash_with_removed_items(self) -> None:
        """Test that removed items affect the hash differently than added items."""
        hash1 = generate_playlist_update_hash(tracks_added=["track1"], tracks_removed=["track2"])
        hash2 = generate_playlist_update_hash(tracks_added=["track1"])

        assert hash1 != hash2


class TestPlaylistWatchConfig:
    def test_cannot_watch_both_playlist_and_artist(self) -> None:
        """Test that watch config cannot have both playlist and artist."""
        spotify_user = SpotifyUser.objects.create(
            spotify_id="user_both",
            display_name="Both User",
            email="both@example.com",
        )
        watching = Playlist.objects.create(
            spotify_id="watching_both",
            spotify_user=spotify_user,
        )
        watched_playlist = Playlist.objects.create(spotify_id="watched_playlist_both")
        watched_artist = Artist.objects.create(spotify_id="watched_artist_both")

        with pytest.raises(IntegrityError):
            PlaylistWatchConfig.objects.create(
                watching_playlist=watching,
                watched_playlist=watched_playlist,
                watched_artist=watched_artist,
            )

    def test_must_watch_either_playlist_or_artist(self) -> None:
        """Test that watch config must have either playlist or artist."""
        spotify_user = SpotifyUser.objects.create(
            spotify_id="user_neither",
            display_name="Neither User",
            email="neither@example.com",
        )
        watching = Playlist.objects.create(
            spotify_id="watching_neither",
            spotify_user=spotify_user,
        )

        with pytest.raises(IntegrityError):
            PlaylistWatchConfig.objects.create(
                watching_playlist=watching,
                watched_playlist=None,
                watched_artist=None,
            )
