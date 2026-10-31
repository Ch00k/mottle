import asyncio
import datetime
import uuid
from unittest.mock import AsyncMock, Mock, patch

import httpx2
import pytest
from django.conf import settings
from django.contrib.gis.geos import Point
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.test import TestCase, TransactionTestCase
from tekore import Token
from tekore.model import FullArtist

from tests.conftest import create_mock_playlist_getter, create_mock_tracks_getter
from web.events.constants import BANDSINTOWN_BASE_URL
from web.events.data import EventSourceArtist, MusicBrainzArtist
from web.events.exceptions import EventDataSourceException
from web.events.http import AsyncRetryingClient
from web.models import (
    Artist,
    Event,
    EventArtist,
    EventUpdate,
    Playlist,
    PlaylistUpdate,
    PlaylistWatchConfig,
    SpotifyAuth,
    SpotifyUser,
    User,
)
from web.tasks import (
    acheck_artists_for_event_updates,
    acheck_playlists_for_updates,
    atrack_artists_events,
    check_playlist_for_updates,
    check_user_playlists_for_updates,
    track_artist_events,
    upload_cover_image,
)
from web.utils import MottleException, MottleSpotifyClient


def create_spotify_client(
    playlist_tracks: dict[str, list[str]] | None = None,
    artist_albums: dict[str, list[str]] | None = None,
    album_tracks: dict[str, list[str]] | None = None,
) -> Mock:
    """Create a Spotify client that answers with the track IDs of the playlists and albums and the artists' album IDs.

    Getting the tracks of a playlist it does not know fails as the real client does.
    """
    playlist_tracks = playlist_tracks or {}
    artist_albums = artist_albums or {}
    album_tracks = album_tracks or {}

    async def get_playlist_tracks(playlist_id: str) -> list[Mock]:
        if playlist_id not in playlist_tracks:
            raise MottleException("Failed to get items")
        return [Mock(track=Mock(id=track_id)) for track_id in playlist_tracks[playlist_id]]

    async def get_artist_albums(artist_id: str) -> list[Mock]:
        return [Mock(id=album_id) for album_id in artist_albums[artist_id]]

    async def get_album_tracks(album_id: str) -> list[Mock]:
        return [Mock(id=track_id) for track_id in album_tracks[album_id]]

    spotify_client = Mock(spec=MottleSpotifyClient)
    spotify_client.get_playlist_tracks = AsyncMock(side_effect=get_playlist_tracks)
    spotify_client.get_artist_albums = AsyncMock(side_effect=get_artist_albums)
    spotify_client.get_album_tracks = AsyncMock(side_effect=get_album_tracks)
    spotify_client.add_tracks_to_playlist = AsyncMock()
    spotify_client.get_playlist = AsyncMock(side_effect=create_mock_playlist_getter())
    spotify_client.get_tracks = AsyncMock(side_effect=create_mock_tracks_getter())
    return spotify_client


async def create_spotify_user(
    spotify_id: str = "user_1", email: str | None = "user@example.com", playlist_notifications: bool = True
) -> SpotifyUser:
    """Create a Spotify user with their settings and a token that does not need refreshing."""
    spotify_user = await SpotifyUser.objects.acreate(spotify_id=spotify_id, email=email)
    await User.objects.acreate(spotify_user=spotify_user, playlist_notifications=playlist_notifications)
    await SpotifyAuth.objects.acreate(
        spotify_user=spotify_user,
        access_token="access_token",
        refresh_token="refresh_token",
        expires_at=datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(hours=1),
        token_scope=settings.SPOTIFY_TOKEN_SCOPE,
    )
    return spotify_user


async def watch_playlist(
    target_playlist: Playlist,
    source_spotify_id: str,
    tracks_ignored: list[str] | None = None,
    auto_accept: bool = False,
) -> Playlist:
    """Make the target playlist watch the playlist with the Spotify ID, returning the watched playlist."""
    source_playlist = await Playlist.objects.acreate(spotify_id=source_spotify_id)
    await PlaylistWatchConfig.objects.acreate(
        watching_playlist=target_playlist,
        watched_playlist=source_playlist,
        tracks_ignored=tracks_ignored,
        auto_accept_updates=auto_accept,
    )
    return source_playlist


@pytest.mark.asyncio
class TestCheckPlaylistForUpdates(TestCase):
    async def test_new_tracks_of_watched_playlist(self) -> None:
        """The tracks of the watched playlist the target playlist lacks make an update, which is returned."""
        target_playlist = await Playlist.objects.acreate(spotify_id="target")
        source_playlist = await watch_playlist(target_playlist, "source")
        spotify_client = create_spotify_client({"target": ["track1"], "source": ["track1", "track2", "track3"]})

        updates = await check_playlist_for_updates(target_playlist, spotify_client)

        update = await PlaylistUpdate.objects.aget()
        assert updates == [update]
        assert update.target_playlist_id == target_playlist.id
        assert update.source_playlist_id == source_playlist.id
        assert sorted(update.tracks_added) == ["track2", "track3"]
        assert update.albums_added is None

    async def test_ignored_tracks_are_left_out(self) -> None:
        """A new track the watch config ignores is not part of the update."""
        target_playlist = await Playlist.objects.acreate(spotify_id="target")
        await watch_playlist(target_playlist, "source", tracks_ignored=["track3"])
        spotify_client = create_spotify_client({"target": ["track1"], "source": ["track1", "track2", "track3"]})

        await check_playlist_for_updates(target_playlist, spotify_client)

        update = await PlaylistUpdate.objects.aget()
        assert update.tracks_added == ["track2"]

    async def test_no_new_tracks(self) -> None:
        """A watched playlist whose tracks are all in the target playlist, or ignored, makes no update."""
        target_playlist = await Playlist.objects.acreate(spotify_id="target")
        await watch_playlist(target_playlist, "source", tracks_ignored=["track2"])
        spotify_client = create_spotify_client({"target": ["track1"], "source": ["track1", "track2"]})

        updates = await check_playlist_for_updates(target_playlist, spotify_client)

        assert updates == []
        assert await PlaylistUpdate.objects.acount() == 0

    async def test_target_playlist_tracks_fail(self) -> None:
        """When the target playlist's tracks cannot be got, the error is logged and no update is made."""
        target_playlist = await Playlist.objects.acreate(spotify_id="target")
        await watch_playlist(target_playlist, "source")
        spotify_client = create_spotify_client({"source": ["track1"]})

        with self.assertLogs("web.tasks", level="ERROR") as logs:
            updates = await check_playlist_for_updates(target_playlist, spotify_client)

        assert updates == []
        assert await PlaylistUpdate.objects.acount() == 0
        assert logs.output == [
            f"ERROR:web.tasks:Failed to get tracks for playlist {target_playlist}: Failed to get items"
        ]

    async def test_watched_playlist_tracks_fail(self) -> None:
        """A watched playlist whose tracks cannot be got is logged and skipped, and the other ones are still checked."""
        target_playlist = await Playlist.objects.acreate(spotify_id="target")
        broken_playlist = await watch_playlist(target_playlist, "broken")
        await watch_playlist(target_playlist, "source")
        spotify_client = create_spotify_client({"target": [], "source": ["track1"]})

        with self.assertLogs("web.tasks", level="ERROR") as logs:
            updates = await check_playlist_for_updates(target_playlist, spotify_client)

        update = await PlaylistUpdate.objects.aget()
        assert updates == [update]
        assert update.tracks_added == ["track1"]
        assert logs.output == [
            f"ERROR:web.tasks:Failed to get tracks for watched playlist {broken_playlist}: Failed to get items"
        ]

    async def test_new_albums_of_watched_artist(self) -> None:
        """The watched artist's albums none of whose tracks are in the target playlist, and not ignored, make an update.

        Of the artist's three albums one has a track in the target playlist, one is ignored, and one is new.
        """
        target_playlist = await Playlist.objects.acreate(spotify_id="target")
        artist = await Artist.objects.acreate(spotify_id="artist")
        await PlaylistWatchConfig.objects.acreate(
            watching_playlist=target_playlist, watched_artist=artist, albums_ignored=["ignored_album"]
        )
        spotify_client = create_spotify_client(
            playlist_tracks={"target": ["track1"]},
            artist_albums={"artist": ["old_album", "ignored_album", "new_album"]},
            album_tracks={"old_album": ["track1", "track2"], "ignored_album": ["track3"], "new_album": ["track4"]},
        )

        updates = await check_playlist_for_updates(target_playlist, spotify_client)

        update = await PlaylistUpdate.objects.aget()
        assert updates == [update]
        assert update.source_artist_id == artist.id
        assert update.albums_added == ["new_album"]
        assert update.tracks_added is None

    async def test_no_new_albums_of_watched_artist(self) -> None:
        """A watched artist whose every album has a track in the target playlist makes no update."""
        target_playlist = await Playlist.objects.acreate(spotify_id="target")
        artist = await Artist.objects.acreate(spotify_id="artist")
        await PlaylistWatchConfig.objects.acreate(watching_playlist=target_playlist, watched_artist=artist)
        spotify_client = create_spotify_client(
            playlist_tracks={"target": ["track1"]},
            artist_albums={"artist": ["old_album"]},
            album_tracks={"old_album": ["track1", "track2"]},
        )

        updates = await check_playlist_for_updates(target_playlist, spotify_client)

        assert updates == []
        assert await PlaylistUpdate.objects.acount() == 0

    async def test_existing_update_not_yet_notified_of(self) -> None:
        """An update found again, which the user has not been notified of, is returned once more and not duplicated."""
        target_playlist = await Playlist.objects.acreate(spotify_id="target")
        await watch_playlist(target_playlist, "source")
        spotify_client = create_spotify_client({"target": [], "source": ["track1"]})

        first_updates = await check_playlist_for_updates(target_playlist, spotify_client)
        second_updates = await check_playlist_for_updates(target_playlist, spotify_client)

        assert second_updates == first_updates
        assert await PlaylistUpdate.objects.acount() == 1

    async def test_existing_update_already_notified_of(self) -> None:
        """An update found again, which the user has already been notified of, is not returned."""
        target_playlist = await Playlist.objects.acreate(spotify_id="target")
        await watch_playlist(target_playlist, "source")
        spotify_client = create_spotify_client({"target": [], "source": ["track1"]})

        await check_playlist_for_updates(target_playlist, spotify_client)
        await PlaylistUpdate.objects.aupdate(is_notified_of=True)
        updates = await check_playlist_for_updates(target_playlist, spotify_client)

        assert updates == []
        assert await PlaylistUpdate.objects.acount() == 1


@pytest.mark.asyncio
class TestCheckUserPlaylistsForUpdates(TestCase):
    async def create_watching_playlist(self, spotify_user: SpotifyUser, auto_accept: bool = False) -> Playlist:
        """Create the user's playlist "target", which watches the playlist "source"."""
        target_playlist = await Playlist.objects.acreate(spotify_id="target", spotify_user=spotify_user)
        await watch_playlist(target_playlist, "source", auto_accept=auto_accept)
        return target_playlist

    async def check(self, spotify_client: Mock, spotify_user: SpotifyUser, send_notifications: bool = True) -> None:
        """Check the user's playlists with the Spotify client in place of a real one."""
        with patch("web.tasks.MottleSpotifyClient", return_value=spotify_client):
            await check_user_playlists_for_updates(spotify_user, send_notifications=send_notifications)

    async def test_email_sent_and_updates_marked_notified_of(self) -> None:
        """The updates are emailed to the user, and marked as notified of."""
        spotify_user = await create_spotify_user()
        await self.create_watching_playlist(spotify_user)
        spotify_client = create_spotify_client({"target": [], "source": ["track1"]})

        await self.check(spotify_client, spotify_user)

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert isinstance(message, EmailMultiAlternatives)
        assert message.to == ["user@example.com"]
        assert message.subject == "We've got updates for you"
        assert message.from_email == f"{settings.MAIL_FROM_NAME} <{settings.MAIL_FROM_EMAIL}>"
        html_message, _ = message.alternatives[0]
        assert isinstance(html_message, str)
        assert "Track 1" in html_message
        update = await PlaylistUpdate.objects.aget()
        assert update.is_notified_of is True
        assert update.is_accepted is None
        spotify_client.add_tracks_to_playlist.assert_not_awaited()

    async def test_no_updates(self) -> None:
        """A user whose playlists have no updates gets no email."""
        spotify_user = await create_spotify_user()
        await self.create_watching_playlist(spotify_user)
        spotify_client = create_spotify_client({"target": ["track1"], "source": ["track1"]})

        await self.check(spotify_client, spotify_user)

        assert mail.outbox == []

    async def test_auto_accepted_update(self) -> None:
        """An update of a playlist set to accept updates automatically has its tracks added, and is emailed."""
        spotify_user = await create_spotify_user()
        await self.create_watching_playlist(spotify_user, auto_accept=True)
        spotify_client = create_spotify_client({"target": [], "source": ["track1"]})

        await self.check(spotify_client, spotify_user)

        spotify_client.add_tracks_to_playlist.assert_awaited_once_with("target", ["spotify:track:track1"])
        update = await PlaylistUpdate.objects.aget()
        assert update.is_accepted is True
        assert update.is_notified_of is True
        assert len(mail.outbox) == 1

    async def test_auto_accept_failure(self) -> None:
        """An update that fails to be accepted automatically is logged, left pending, and still emailed."""
        spotify_user = await create_spotify_user()
        await self.create_watching_playlist(spotify_user, auto_accept=True)
        spotify_client = create_spotify_client({"target": [], "source": ["track1"]})
        spotify_client.add_tracks_to_playlist.side_effect = MottleException("Failed to add tracks")

        with self.assertLogs("web.tasks", level="ERROR") as logs:
            await self.check(spotify_client, spotify_user)

        update = await PlaylistUpdate.objects.aget()
        assert update.is_accepted is None
        assert len(mail.outbox) == 1
        assert len(logs.output) == 1
        assert logs.output[0].startswith("ERROR:web.tasks:Failed to accept update {'update': ")
        assert logs.output[0].endswith(": Failed to add tracks")

    async def test_notifications_not_sent(self) -> None:
        """When notifications are not to be sent, there is no email and the updates are not marked as notified of."""
        spotify_user = await create_spotify_user()
        await self.create_watching_playlist(spotify_user)
        spotify_client = create_spotify_client({"target": [], "source": ["track1"]})

        with self.assertLogs("web.tasks", level="WARNING") as logs:
            await self.check(spotify_client, spotify_user, send_notifications=False)

        assert mail.outbox == []
        assert (await PlaylistUpdate.objects.aget()).is_notified_of is False
        assert logs.output == ["WARNING:web.tasks:Email notifications disabled"]

    async def test_playlist_notifications_disabled_for_user(self) -> None:
        """A user who turned playlist notifications off gets no email, and the updates are not marked as notified of."""
        spotify_user = await create_spotify_user(playlist_notifications=False)
        await self.create_watching_playlist(spotify_user)
        spotify_client = create_spotify_client({"target": [], "source": ["track1"]})

        with self.assertLogs("web.tasks", level="WARNING") as logs:
            await self.check(spotify_client, spotify_user)

        assert mail.outbox == []
        assert (await PlaylistUpdate.objects.aget()).is_notified_of is False
        assert logs.output == [f"WARNING:web.tasks:Playlist notifications disabled for user {spotify_user}"]

    async def test_user_without_email(self) -> None:
        """A user Spotify gave no email address gets no email, and the updates are not marked as notified of."""
        spotify_user = await create_spotify_user(email=None)
        await self.create_watching_playlist(spotify_user)
        spotify_client = create_spotify_client({"target": [], "source": ["track1"]})

        with self.assertLogs("web.tasks", level="WARNING") as logs:
            await self.check(spotify_client, spotify_user)

        assert mail.outbox == []
        assert (await PlaylistUpdate.objects.aget()).is_notified_of is False
        assert logs.output == [f"WARNING:web.tasks:No email for user {spotify_user}"]

    async def test_email_send_failure(self) -> None:
        """An email that fails to send is logged, and the updates are not marked as notified of."""
        spotify_user = await create_spotify_user()
        await self.create_watching_playlist(spotify_user)
        spotify_client = create_spotify_client({"target": [], "source": ["track1"]})

        with (
            patch("web.tasks.send_mail", side_effect=OSError("Connection refused")),
            self.assertLogs("web.tasks", level="ERROR") as logs,
        ):
            await self.check(spotify_client, spotify_user)

        assert (await PlaylistUpdate.objects.aget()).is_notified_of is False
        assert logs.output == ["ERROR:web.tasks:Failed to send email to user@example.com: Connection refused"]

    async def test_token_refresh_failure(self) -> None:
        """A user whose expired token fails to refresh is logged, and the failure is raised."""
        spotify_user = await create_spotify_user()
        await SpotifyAuth.objects.filter(spotify_user=spotify_user).aupdate(
            expires_at=datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(minutes=1)
        )

        with (
            patch.object(settings.SPOTIFY_CREDEINTIALS, "refresh", side_effect=ConnectionError("Spotify is down")),
            self.assertLogs("web.tasks", level="ERROR") as logs,
            pytest.raises(MottleException, match=r"^Failed to refresh token$"),
        ):
            await self.check(create_spotify_client(), spotify_user)

        assert logs.output == [
            (
                f"ERROR:web.tasks:Failed to check for playlist updates for user {spotify_user}: "
                "failed to refresh token: Failed to refresh token"
            )
        ]

    async def test_all_users_with_playlists(self) -> None:
        """Every user with a playlist is checked, and a user with none is not."""
        spotify_user = await create_spotify_user()
        await self.create_watching_playlist(spotify_user)
        await create_spotify_user(spotify_id="user_without_playlists", email="other@example.com")
        spotify_client = create_spotify_client({"target": [], "source": ["track1"]})

        with patch("web.tasks.MottleSpotifyClient", return_value=spotify_client):
            await acheck_playlists_for_updates(send_notifications=True)

        assert [message.to for message in mail.outbox] == [["user@example.com"]]


@pytest.mark.asyncio
class TestEventUpdateNotifications(TestCase):
    async def test_user_with_notifications_disabled_does_not_stop_the_others(self) -> None:
        """A user who turned event notifications off is skipped, and the users after them still get their email.

        Both users watch an artist with an upcoming live stream, whose creation records an unnotified update. The user
        with notifications off is created first, and the check goes through users oldest first. No artist matches the
        given IDs, so no event source is fetched.
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

    async def create_watched_live_stream(self) -> EventUpdate:
        """Create a user who watches an artist with an upcoming live stream, returning its unnotified update."""
        artist = await Artist.objects.acreate(spotify_id="artist_1")
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=0,
        )
        event = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/live-stream-concerts/1",
            type="live_stream",
            date=datetime.datetime.now(tz=datetime.UTC).date() + datetime.timedelta(days=5),
            stream_urls=["https://example.com/stream"],
        )
        spotify_user = await SpotifyUser.objects.acreate(spotify_id="user_1", email="user@example.com")
        await User.objects.acreate(
            spotify_user=spotify_user,
            event_notifications=True,
            location=Point(-0.1276, 51.5074, srid=settings.GEODJANGO_SRID),
            event_distance_threshold=50.0,
        )
        await spotify_user.watched_event_artists.aadd(event_artist)
        return await EventUpdate.objects.aget(event=event)

    async def check_event_updates(self, send_notifications: bool = True) -> None:
        """Check for event updates with a Spotify client in place of a real one, fetching no event source."""
        spotify_artist = Mock(spec=FullArtist)
        spotify_artist.name = "Artist One"
        spotify_client = Mock(spec=MottleSpotifyClient)
        spotify_client.get_artist = AsyncMock(return_value=spotify_artist)

        with (
            patch("web.tasks.get_client_token", return_value=Mock(access_token="client_token")),
            patch("web.tasks.MottleSpotifyClient", return_value=spotify_client),
        ):
            await acheck_artists_for_event_updates(
                artist_spotify_ids=["untracked_artist"], send_notifications=send_notifications
            )

    async def test_email_sent_and_updates_marked_notified_of(self) -> None:
        """The event updates are emailed to the user, and marked as notified of."""
        update = await self.create_watched_live_stream()

        await self.check_event_updates()

        assert [message.to for message in mail.outbox] == [["user@example.com"]]
        message = mail.outbox[0]
        assert isinstance(message, EmailMultiAlternatives)
        assert message.subject == "We've got updates for you"
        html_message, _ = message.alternatives[0]
        assert isinstance(html_message, str)
        assert "Artist One" in html_message
        await update.arefresh_from_db()
        assert update.is_notified_of is True

    async def test_notifications_not_sent(self) -> None:
        """When notifications are not to be sent, there is no email and the updates are not marked as notified of."""
        update = await self.create_watched_live_stream()

        with self.assertLogs("web.tasks", level="WARNING") as logs:
            await self.check_event_updates(send_notifications=False)

        assert mail.outbox == []
        await update.arefresh_from_db()
        assert update.is_notified_of is False
        assert logs.output == ["WARNING:web.tasks:Email notifications disabled"]

    async def test_email_send_failure(self) -> None:
        """An email that fails to send is logged, and the updates are not marked as notified of."""
        update = await self.create_watched_live_stream()

        with (
            patch("web.tasks.send_mail", side_effect=OSError("Connection refused")),
            self.assertLogs("web.tasks", level="ERROR") as logs,
        ):
            await self.check_event_updates()

        await update.arefresh_from_db()
        assert update.is_notified_of is False
        assert logs.output == ["ERROR:web.tasks:Failed to send email to user@example.com: Connection refused"]

    async def test_counts_the_artists_and_their_events(self) -> None:
        """Each artist's events are updated, a failed one is logged, and the summary counts the artists and events.

        One artist has no event source and keeps its one live stream. The other's only source, Bandsintown, refuses
        the request, which fails that artist.
        """
        update = await self.create_watched_live_stream()
        failing_event_artist = await EventArtist.objects.acreate(
            artist=await Artist.objects.acreate(spotify_id="artist_2"),
            bandsintown_url=f"{BANDSINTOWN_BASE_URL}/a/2",
            songkick_name_match_accuracy=0,
            bandsintown_name_match_accuracy=100,
        )
        refusing_bandsintown = AsyncRetryingClient(
            attempts=1,
            base_url=BANDSINTOWN_BASE_URL,
            transport=httpx2.MockTransport(lambda _: httpx2.Response(403, text="Just a moment...")),
        )

        with (
            patch("web.events.data.async_bandsintown_client", refusing_bandsintown),
            self.assertLogs("web", level="DEBUG") as logs,
        ):
            await acheck_artists_for_event_updates(
                artist_spotify_ids=["artist_1", "artist_2"], compile_notifications=False
            )

        errors = [record for record in logs.records if record.levelname == "ERROR"]
        assert [record.name for record in errors] == ["web.events.data", "web.tasks"]
        assert errors[0].getMessage().startswith("Failed to fetch data from https://www.bandsintown.com/a/2: ")
        assert errors[1].getMessage() == "Failed to process artist: Failed to fetch events from bandsintown"
        assert isinstance(errors[1].exc_info, tuple)
        assert isinstance(errors[1].exc_info[1], EventDataSourceException)

        summary = logs.records[-1].getMessage()
        assert summary.startswith("Processed 2 artists in ")
        assert "success: 1, fail: 1, events: 1, URLs: 1," in summary
        assert await failing_event_artist.events.acount() == 0
        await update.arefresh_from_db()
        assert update.is_notified_of is False
        assert mail.outbox == []

    async def test_already_notified_of(self) -> None:
        """Updates the user has been notified of are not emailed again."""
        update = await self.create_watched_live_stream()
        update.is_notified_of = True
        await update.asave()

        await self.check_event_updates()

        assert mail.outbox == []


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
            await upload_cover_image("Road Trip", "playlist_1", str(spotify_user.id))

        auth = await SpotifyAuth.objects.aget(spotify_user=spotify_user)
        assert auth.access_token == "refreshed_token"
        assert auth.refresh_token == "new_refresh_token"
        refresh.assert_called_once()
        create_cover_image.assert_called_once_with("Road Trip")
        client_class.assert_called_once_with("refreshed_token")
        spotify_client.upload_playlist_cover_image.assert_awaited_once_with("playlist_1", b"jpeg")

    async def test_user_without_spotify_auth_gets_no_image(self) -> None:
        """A user with no Spotify authorization is logged as an error, and no image is generated."""
        missing_user_id = str(uuid.uuid4())

        with (
            patch("web.tasks.create_cover_image") as create_cover_image,
            self.assertLogs("web.tasks", level="ERROR") as logs,
        ):
            await upload_cover_image("Road Trip", "playlist_1", missing_user_id)

        create_cover_image.assert_not_called()
        assert logs.output == [f"ERROR:web.tasks:SpotifyAuth for user ID {missing_user_id} does not exist"]

    async def test_failed_token_refresh_stops_upload(self) -> None:
        """A token that fails to refresh is logged as an error, and no image is generated or uploaded."""
        spotify_user = await SpotifyUser.objects.acreate(spotify_id="cover_user")
        await SpotifyAuth.objects.acreate(
            spotify_user=spotify_user,
            access_token="expired_token",
            refresh_token="refresh_token",
            expires_at=datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(minutes=1),
            token_scope=["ugc-image-upload"],
        )

        with (
            patch.object(settings.SPOTIFY_CREDEINTIALS, "refresh", side_effect=Exception("Spotify is down")),
            patch("web.tasks.create_cover_image") as create_cover_image,
            self.assertLogs("web.tasks", level="ERROR") as logs,
        ):
            await upload_cover_image("Road Trip", "playlist_1", str(spotify_user.id))

        create_cover_image.assert_not_called()
        assert logs.output == ["ERROR:web.tasks:Failed to refresh token"]


# Creating an EventArtist that already exists fails the insert, which would abort the transaction a TestCase wraps the
# test in.
@pytest.mark.asyncio
class TestTrackArtistEvents(TransactionTestCase):
    async def test_overlapping_jobs_both_watch_the_artist(self) -> None:
        """Two jobs tracking the same new artist at once create one EventArtist, and both users end up watching it.

        The event source search is replaced by one that waits until both jobs are searching, so both have seen that
        the artist has no EventArtist before either creates it.
        """
        spotify_users = [
            await SpotifyUser.objects.acreate(spotify_id=spotify_id) for spotify_id in ("first_user", "second_user")
        ]
        both_searching = asyncio.Barrier(2)

        async def find_event_data_sources(_: str, artist_name: str) -> tuple[None, EventSourceArtist]:
            await both_searching.wait()
            return None, EventSourceArtist(artist_name)

        with patch("web.tasks.find_event_data_sources", find_event_data_sources):
            first, second = await asyncio.gather(
                *(track_artist_events("artist_1", "Artist One", str(user.id)) for user in spotify_users)
            )

        assert first.id == second.id
        assert await EventArtist.objects.acount() == 1
        watching_user_ids = {user.id async for user in first.watching_users.all()}
        assert watching_user_ids == {user.id for user in spotify_users}


@pytest.mark.asyncio
class TestTrackArtistsEvents(TestCase):
    async def test_counts_the_artists_found_at_each_source(self) -> None:
        """Every named artist is tracked, a failed one is logged, and the summary counts what each source found.

        The event source search is replaced: one artist is found at MusicBrainz and Songkick, one nowhere, and the
        search for one fails. The artist with no name is not searched for, but counts towards the artists to process.
        """
        spotify_user = await SpotifyUser.objects.acreate(spotify_id="user_1")
        musicbrainz_id = str(uuid.uuid4())

        async def find_event_data_sources(
            artist_spotify_id: str, artist_name: str
        ) -> tuple[MusicBrainzArtist | None, EventSourceArtist]:
            if artist_spotify_id == "failing_artist":
                raise MottleException("Search failed")

            if artist_spotify_id == "found_artist":
                return (
                    MusicBrainzArtist(id=musicbrainz_id, names=[artist_name]),
                    EventSourceArtist(artist_name, songkick_url="https://www.songkick.com/artists/1-found/calendar"),
                )

            return None, EventSourceArtist(artist_name)

        with (
            patch("web.tasks.find_event_data_sources", side_effect=find_event_data_sources) as find,
            self.assertLogs("web.tasks", level="DEBUG") as logs,
        ):
            await atrack_artists_events(
                {
                    "found_artist": "Found Artist",
                    "unfound_artist": "Unfound Artist",
                    "failing_artist": "Failing Artist",
                    "nameless_artist": "",
                },
                str(spotify_user.id),
            )

        assert sorted(call.args[0] for call in find.call_args_list) == [
            "failing_artist",
            "found_artist",
            "unfound_artist",
        ]
        event_artists = {
            event_artist.artist.spotify_id: event_artist
            async for event_artist in EventArtist.objects.select_related("artist")
        }
        assert set(event_artists) == {"found_artist", "unfound_artist"}
        assert str(event_artists["found_artist"].musicbrainz_id) == musicbrainz_id

        errors = [record for record in logs.records if record.levelname == "ERROR"]
        assert [record.getMessage() for record in errors] == ["Failed to process artist: Search failed"]
        assert isinstance(errors[0].exc_info, tuple)
        assert isinstance(errors[0].exc_info[1], MottleException)

        summary = logs.records[-1].getMessage()
        assert summary.startswith("Processed 4 artists in ")
        assert "success: 2, fail: 1, no MB: 1, no SK: 1, no BT: 2," in summary
