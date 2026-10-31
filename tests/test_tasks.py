import datetime
import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from django.conf import settings
from django.contrib.gis.geos import Point
from django.core import mail
from django.test import TestCase
from tekore import Token
from tekore.model import FullArtist

from web.models import Artist, Event, EventArtist, SpotifyAuth, SpotifyUser, User
from web.tasks import acheck_artists_for_event_updates, upload_cover_image
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


@pytest.mark.asyncio
class TestUploadCoverImage(TestCase):
    async def test_refreshes_expiring_token_before_upload(self) -> None:
        """An expiring Spotify token is refreshed and saved, and the image is uploaded with the refreshed token.

        Image generation and Spotify's token and upload endpoints are replaced; the refresh and the upload are
        checked through the token they were given and the database row.
        """
        spotify_user = await SpotifyUser.objects.acreate(spotify_id="cover_user")
        await SpotifyAuth.objects.acreate(
            spotify_user=spotify_user,
            access_token="expired_token",
            refresh_token="refresh_token",
            expires_at=datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(minutes=1),
            token_scope=["ugc-image-upload"],
        )
        refreshed_token = Token(
            token_info={
                "token_type": "Bearer",
                "access_token": "refreshed_token",
                "refresh_token": "new_refresh_token",
                "expires_in": 3600,
            },
            uses_pkce=False,
        )
        spotify_client = Mock(spec=MottleSpotifyClient)
        spotify_client.upload_playlist_cover_image = AsyncMock()

        with (
            patch.object(settings.SPOTIFY_CREDEINTIALS, "refresh", return_value=refreshed_token) as refresh,
            patch("web.tasks.create_cover_image", return_value=b"jpeg") as create_cover_image,
            patch("web.tasks.MottleSpotifyClient", return_value=spotify_client) as client_class,
        ):
            await upload_cover_image("Road Trip", "playlist_1", str(spotify_user.id), dump_to_disk=True)

        auth = await SpotifyAuth.objects.aget(spotify_user=spotify_user)
        assert auth.access_token == "refreshed_token"
        assert auth.refresh_token == "new_refresh_token"
        refresh.assert_called_once()
        create_cover_image.assert_called_once_with("Road Trip", dump_to_disk=True)
        client_class.assert_called_once_with("refreshed_token")
        spotify_client.upload_playlist_cover_image.assert_awaited_once_with("playlist_1", b"jpeg")
