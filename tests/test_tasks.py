import datetime
import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from django.conf import settings
from django.contrib.gis.geos import Point
from django.core import mail
from django.test import TestCase
from tekore.model import FullArtist

from web.models import Artist, Event, EventArtist, SpotifyUser, User
from web.tasks import acheck_artists_for_event_updates
from web.utils import MottleSpotifyClient


@pytest.mark.asyncio
class TestEventUpdateNotifications(TestCase):
    async def test_user_with_notifications_disabled_does_not_stop_the_others(self) -> None:
        """A user who turned event notifications off is skipped, and the users after them still get their email.

        Both users watch an artist with an upcoming live stream, whose creation records an unnotified update. The user
        with notifications off is created first, so the check reaches them first. No artist matches the given IDs, so
        no event source is fetched.
        """
        artist = await Artist.objects.acreate(spotify_id="artist_1")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=uuid.uuid4(),
            songkick_url="https://www.songkick.com/artists/1",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=0,
        )
        await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/1",
            type="live_stream",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=5),
            stream_urls=["https://example.com/stream"],
        )

        spotify_users = {}
        for spotify_id, event_notifications in (("muted_user", False), ("subscribed_user", True)):
            spotify_user = await SpotifyUser.objects.acreate(spotify_id=spotify_id, email=f"{spotify_id}@example.com")
            await User.objects.acreate(
                spotify_user=spotify_user,
                event_notifications=event_notifications,
                location=Point(-0.1276, 51.5074, srid=settings.GEODJANGO_SRID),
                event_distance_threshold=50.0,
            )
            await spotify_user.watched_event_artists.aadd(event_artist)
            spotify_users[spotify_id] = spotify_user

        spotify_artist = Mock(spec=FullArtist)
        spotify_artist.name = "Artist One"
        spotify_client = Mock(spec=MottleSpotifyClient)
        spotify_client.get_artist = AsyncMock(return_value=spotify_artist)

        with (
            patch("web.tasks.get_client_token", return_value=Mock(access_token="client_token")),
            patch("web.tasks.MottleSpotifyClient", return_value=spotify_client),
            self.assertLogs("web.tasks", level="WARNING") as logs,
        ):
            await acheck_artists_for_event_updates(artist_spotify_ids=["untracked_artist"])

        assert [message.to for message in mail.outbox] == [["subscribed_user@example.com"]]
        assert logs.output == [
            f"WARNING:web.tasks:Event notifications disabled for user {spotify_users['muted_user']}",
        ]
