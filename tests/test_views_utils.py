# mypy: disable-error-code="method-assign,assignment"
import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.test import TestCase
from minify_html import minify
from tekore.model import FullPlaylist, Image, PublicUser

from tests.conftest import (
    create_mock_albums_getter,
    create_mock_artist_getter,
    create_mock_playlist_getter,
    create_mock_request,
    create_mock_tracks_getter,
)
from web.events.enums import EventType
from web.models import Artist, Event, EventArtist, EventUpdate, Playlist, PlaylistUpdate
from web.utils import MottleException, MottleSpotifyClient
from web.views_utils import (
    compile_event_updates_email,
    compile_playlist_updates_email,
    get_playlist_modal_response,
)


@pytest.mark.asyncio
class TestCompileEventUpdatesEmail(TestCase):
    async def test_compile_event_updates_email(self) -> None:
        """Test comprehensive event updates email generation against golden files."""
        # Create test data covering all scenarios

        # Artist 1: Concert with tickets and festival without tickets
        artist1 = await Artist.objects.acreate(spotify_id="artist1")
        event_artist1 = await EventArtist.objects.acreate(
            artist=artist1,
            musicbrainz_id="12345678-1234-1234-1234-123456789012",
            songkick_url="https://www.songkick.com/artists/1",
            bandsintown_url="https://www.bandsintown.com/a/1",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )

        # Concert with tickets
        event1_1 = await Event.objects.acreate(
            artist=event_artist1,
            source="songkick",
            source_url="https://www.songkick.com/concerts/1",
            type=EventType.concert,
            date=datetime.date(2025, 12, 1),
            venue="Test Venue",
            city="London",
            country="UK",
            tickets_urls=["https://example.com/tickets1", "https://example.com/tickets2"],
        )
        update1_1 = await EventUpdate.objects.acreate(
            event=event1_1,
            type=EventUpdate.FULL,
            changes=None,
        )

        # Festival without tickets
        event1_2 = await Event.objects.acreate(
            artist=event_artist1,
            source="bandsintown",
            source_url="https://www.bandsintown.com/e/2",
            type=EventType.festival,
            date=datetime.date(2025, 12, 15),
            venue="Festival Grounds",
            city="Manchester",
            country="UK",
            tickets_urls=None,
        )
        update1_2 = await EventUpdate.objects.acreate(
            event=event1_2,
            type=EventUpdate.FULL,
            changes=None,
        )

        # Artist 2: Livestream events
        artist2 = await Artist.objects.acreate(spotify_id="artist2")
        event_artist2 = await EventArtist.objects.acreate(
            artist=artist2,
            musicbrainz_id="22345678-1234-1234-1234-123456789012",
            songkick_url="https://www.songkick.com/artists/2",
            bandsintown_url="https://www.bandsintown.com/a/2",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )

        # Livestream with stream URLs
        event2_1 = await Event.objects.acreate(
            artist=event_artist2,
            source="songkick",
            source_url="https://www.songkick.com/concerts/3",
            type=EventType.live_stream,
            date=datetime.date(2025, 12, 20),
            venue=None,
            city=None,
            country=None,
            stream_urls=["https://example.com/stream1"],
        )
        update2_1 = await EventUpdate.objects.acreate(
            event=event2_1,
            type=EventUpdate.FULL,
            changes=None,
        )

        # Livestream without stream URLs
        event2_2 = await Event.objects.acreate(
            artist=event_artist2,
            source="songkick",
            source_url="https://www.songkick.com/concerts/4",
            type=EventType.live_stream,
            date=datetime.date(2025, 12, 25),
            venue=None,
            city=None,
            country=None,
            stream_urls=None,
        )
        update2_2 = await EventUpdate.objects.acreate(
            event=event2_2,
            type=EventUpdate.FULL,
            changes=None,
        )

        # Artist 3: Partial updates
        artist3 = await Artist.objects.acreate(spotify_id="artist3")
        event_artist3 = await EventArtist.objects.acreate(
            artist=artist3,
            musicbrainz_id="32345678-1234-1234-1234-123456789012",
            songkick_url="https://www.songkick.com/artists/3",
            bandsintown_url="https://www.bandsintown.com/a/3",
            songkick_name_match_accuracy=100,
            bandsintown_name_match_accuracy=95,
        )

        # Livestream with stream URLs changed
        event3_1 = await Event.objects.acreate(
            artist=event_artist3,
            source="songkick",
            source_url="https://www.songkick.com/concerts/5",
            type=EventType.live_stream,
            date=datetime.date(2025, 12, 30),
            venue=None,
            city=None,
            country=None,
            stream_urls=["https://example.com/stream_new"],
        )
        update3_1 = await EventUpdate.objects.acreate(
            event=event3_1,
            type=EventUpdate.PARTIAL,
            changes={
                "stream_urls": {
                    "old": ["https://example.com/stream_old"],
                    "new": ["https://example.com/stream_new"],
                }
            },
        )

        # Concert with tickets URLs changed
        event3_2 = await Event.objects.acreate(
            artist=event_artist3,
            source="songkick",
            source_url="https://www.songkick.com/concerts/6",
            type=EventType.concert,
            date=datetime.date(2026, 1, 5),
            venue="Updated Venue",
            city="Birmingham",
            country="UK",
            tickets_urls=["https://example.com/tickets_new"],
        )
        update3_2 = await EventUpdate.objects.acreate(
            event=event3_2,
            type=EventUpdate.PARTIAL,
            changes={
                "tickets_urls": {
                    "old": ["https://example.com/tickets_old"],
                    "new": ["https://example.com/tickets_new"],
                }
            },
        )

        # Mock Spotify client
        mock_spotify_client = Mock(spec=MottleSpotifyClient)
        mock_spotify_client.get_artist = create_mock_artist_getter(
            {
                "artist1": "The Beatles",
                "artist2": "Pink Floyd",
                "artist3": "Led Zeppelin",
            }
        )

        # Compile email
        updates = {
            event_artist1: [update1_1, update1_2],
            event_artist2: [update2_1, update2_2],
            event_artist3: [update3_1, update3_2],
        }
        html = await compile_event_updates_email(updates, mock_spotify_client)

        # Compare against golden file
        golden_dir = Path(__file__).parent / "golden"
        golden_html = golden_dir / "event_updates_email.html"

        # Uncomment to update golden file
        # golden_dir.mkdir(parents=True, exist_ok=True)
        # golden_html.write_text(html)

        # Verify against golden file
        assert minify(html) == minify(golden_html.read_text())


@pytest.mark.asyncio
class TestCompilePlaylistUpdatesEmail(TestCase):
    async def test_compile_playlist_updates_email(self) -> None:
        """Test comprehensive playlist updates email generation against golden files."""
        # Create test data covering all scenarios

        # Target playlist 1: Receives updates from watched playlist and watched artist
        target_playlist1 = await Playlist.objects.acreate(spotify_id="target_playlist1")

        # Watched playlist 1: Source for track updates
        watched_playlist1 = await Playlist.objects.acreate(spotify_id="watched_playlist1")

        # Watched artist 1: Source for album updates
        watched_artist1 = await Artist.objects.acreate(spotify_id="watched_artist1")

        # Update 1.1: Tracks added from watched playlist (auto-accepted)
        update1_1 = await PlaylistUpdate.objects.acreate(
            target_playlist=target_playlist1,
            source_playlist=watched_playlist1,
            source_artist=None,
            tracks_added=["track1", "track2", "track3"],
            albums_added=None,
            update_hash="hash1_1",
            is_accepted=True,
        )

        # Update 1.2: Albums added from watched artist (auto-accept failed)
        update1_2 = await PlaylistUpdate.objects.acreate(
            target_playlist=target_playlist1,
            source_playlist=None,
            source_artist=watched_artist1,
            tracks_added=None,
            albums_added=["album1", "album2"],
            update_hash="hash1_2",
            is_accepted=None,
        )

        # Target playlist 2: Receives updates from watched playlist (manual acceptance)
        target_playlist2 = await Playlist.objects.acreate(spotify_id="target_playlist2")

        # Watched playlist 2: Source for track updates
        watched_playlist2 = await Playlist.objects.acreate(spotify_id="watched_playlist2")

        # Update 2.1: Tracks added from watched playlist (manual acceptance required)
        update2_1 = await PlaylistUpdate.objects.acreate(
            target_playlist=target_playlist2,
            source_playlist=watched_playlist2,
            source_artist=None,
            tracks_added=["track4", "track5"],
            albums_added=None,
            update_hash="hash2_1",
            is_accepted=None,
        )

        # Target playlist 3: Receives many tracks from watched playlist
        target_playlist3 = await Playlist.objects.acreate(spotify_id="target_playlist3")

        # Watched playlist 3: Source for many tracks
        watched_playlist3 = await Playlist.objects.acreate(spotify_id="watched_playlist3")

        # Update 3.1: Many tracks (15) added from watched playlist
        track_ids = [f"track{i}" for i in range(6, 21)]
        update3_1 = await PlaylistUpdate.objects.acreate(
            target_playlist=target_playlist3,
            source_playlist=watched_playlist3,
            source_artist=None,
            tracks_added=track_ids,
            albums_added=None,
            update_hash="hash3_1",
            is_accepted=None,
        )

        # Target playlist 4: Receives many albums from watched artist
        target_playlist4 = await Playlist.objects.acreate(spotify_id="target_playlist4")

        # Watched artist 2: Source for many albums
        watched_artist2 = await Artist.objects.acreate(spotify_id="watched_artist2")

        # Update 4.1: Many albums (12) added from watched artist
        album_ids = [f"album{i}" for i in range(3, 15)]
        update4_1 = await PlaylistUpdate.objects.acreate(
            target_playlist=target_playlist4,
            source_playlist=None,
            source_artist=watched_artist2,
            tracks_added=None,
            albums_added=album_ids,
            update_hash="hash4_1",
            is_accepted=None,
        )

        # Target playlist 5: Edge case with exactly num_to_show tracks
        target_playlist5 = await Playlist.objects.acreate(spotify_id="target_playlist5")
        watched_playlist5 = await Playlist.objects.acreate(spotify_id="watched_playlist5")

        # Update 5.1: Exactly 10 tracks (boundary case)
        track_ids_10 = [f"track{i}" for i in range(21, 31)]
        update5_1 = await PlaylistUpdate.objects.acreate(
            target_playlist=target_playlist5,
            source_playlist=watched_playlist5,
            source_artist=None,
            tracks_added=track_ids_10,
            albums_added=None,
            update_hash="hash5_1",
            is_accepted=None,
        )

        # Target playlist 6: Edge case with few items
        target_playlist6 = await Playlist.objects.acreate(spotify_id="target_playlist6")
        watched_artist6 = await Artist.objects.acreate(spotify_id="watched_artist6")

        # Update 6.1: Only 2 albums
        album_ids_2 = [f"album{i}" for i in range(15, 17)]
        update6_1 = await PlaylistUpdate.objects.acreate(
            target_playlist=target_playlist6,
            source_playlist=None,
            source_artist=watched_artist6,
            tracks_added=None,
            albums_added=album_ids_2,
            update_hash="hash6_1",
            is_accepted=None,
        )

        # Mock Spotify client
        mock_spotify_client = Mock(spec=MottleSpotifyClient)

        mock_spotify_client.get_playlist = create_mock_playlist_getter(
            {
                "target_playlist1": {"name": "My Chill Playlist", "owner_name": "John Doe"},
                "watched_playlist1": {"name": "Indie Discoveries", "owner_name": "Jane Smith"},
                "target_playlist2": {"name": "Workout Mix", "owner_name": "John Doe"},
                "watched_playlist2": {"name": "Top Rock Hits", "owner_name": "Bob Johnson"},
                "target_playlist3": {"name": "Many Tracks Playlist", "owner_name": "John Doe"},
                "watched_playlist3": {"name": "Mega Playlist", "owner_name": "Jane Smith"},
                "target_playlist4": {"name": "Album Collection", "owner_name": "John Doe"},
                "target_playlist5": {"name": "Exactly Ten Tracks", "owner_name": "John Doe"},
                "watched_playlist5": {"name": "Boundary Playlist", "owner_name": "Jane Smith"},
                "target_playlist6": {"name": "Few Albums Playlist", "owner_name": "John Doe"},
            }
        )

        mock_spotify_client.get_artist = create_mock_artist_getter(
            {
                "watched_artist1": "The Weeknd",
                "watched_artist2": "Taylor Swift",
                "watched_artist6": "Dua Lipa",
            }
        )

        mock_spotify_client.get_tracks = create_mock_tracks_getter(
            {
                "track1": {"name": "Blinding Lights", "artists": ["The Weeknd"]},
                "track2": {"name": "Save Your Tears", "artists": ["The Weeknd"]},
                "track3": {"name": "Starboy", "artists": ["The Weeknd", "Daft Punk"]},
                "track4": {"name": "Bohemian Rhapsody", "artists": ["Queen"]},
                "track5": {"name": "Stairway to Heaven", "artists": ["Led Zeppelin"]},
            }
        )

        mock_spotify_client.get_albums = create_mock_albums_getter(
            {
                "album1": {"name": "After Hours", "release_date": "2020-03-20"},
                "album2": {"name": "Starboy", "release_date": "2016-11-25"},
            }
        )

        # Compile email
        updates = {
            "target_playlist1": [
                {"update": update1_1, "auto_acceptable": True, "auto_accept_successful": True},
                {"update": update1_2, "auto_acceptable": True, "auto_accept_successful": False},
            ],
            "target_playlist2": [
                {"update": update2_1, "auto_acceptable": False, "auto_accept_successful": False},
            ],
            "target_playlist3": [
                {"update": update3_1, "auto_acceptable": False, "auto_accept_successful": False},
            ],
            "target_playlist4": [
                {"update": update4_1, "auto_acceptable": False, "auto_accept_successful": False},
            ],
            "target_playlist5": [
                {"update": update5_1, "auto_acceptable": False, "auto_accept_successful": False},
            ],
            "target_playlist6": [
                {"update": update6_1, "auto_acceptable": False, "auto_accept_successful": False},
            ],
        }
        html = await compile_playlist_updates_email(updates, mock_spotify_client)

        # Compare against golden file
        golden_dir = Path(__file__).parent / "golden"
        golden_html = golden_dir / "playlist_updates_email.html"

        # Uncomment to update golden file
        # golden_dir.mkdir(parents=True, exist_ok=True)
        # golden_html.write_text(html)

        # Verify against golden file
        assert minify(html) == minify(golden_html.read_text())


@pytest.mark.asyncio
class TestGetPlaylistModalResponse(TestCase):
    @patch("web.views_utils.render")
    async def test_get_playlist_modal_response_success(self, mock_render: Mock) -> None:
        """Test successful playlist modal response generation."""
        mock_request = create_mock_request()
        mock_request.session = {"spotify_user_spotify_id": "user123"}

        mock_playlist1 = Mock(spec=FullPlaylist)
        mock_playlist1.id = "other_playlist_1"
        mock_playlist1.name = "Other Playlist 1"
        mock_owner1 = Mock(spec=PublicUser)
        mock_owner1.id = "user123"
        mock_playlist1.owner = mock_owner1

        mock_playlist2 = Mock(spec=FullPlaylist)
        mock_playlist2.id = "other_playlist_2"
        mock_playlist2.name = "Other Playlist 2"
        mock_owner2 = Mock(spec=PublicUser)
        mock_owner2.id = "user123"
        mock_playlist2.owner = mock_owner2

        mock_playlist3 = Mock(spec=FullPlaylist)
        mock_playlist3.id = "target_playlist"
        mock_playlist3.name = "Target Playlist"
        mock_owner3 = Mock(spec=PublicUser)
        mock_owner3.id = "user123"
        mock_playlist3.owner = mock_owner3

        mock_playlist4 = Mock(spec=FullPlaylist)
        mock_playlist4.id = "someone_else_playlist"
        mock_playlist4.name = "Someone Else Playlist"
        mock_owner4 = Mock(spec=PublicUser)
        mock_owner4.id = "other_user"
        mock_playlist4.owner = mock_owner4

        async def mock_get_current_user_playlists() -> list[Mock]:
            return [mock_playlist1, mock_playlist2, mock_playlist3, mock_playlist4]

        mock_spotify_client = Mock(spec=MottleSpotifyClient)
        mock_spotify_client.get_playlist = create_mock_playlist_getter(
            {
                "target_playlist": {
                    "name": "My Playlist",
                    "images": [Image(url="http://example.com/small.jpg", height=64, width=64)],
                }
            }
        )
        mock_spotify_client.get_current_user_playlists = mock_get_current_user_playlists
        mock_request.spotify_client = mock_spotify_client

        mock_response = Mock()
        mock_response.status_code = 200
        mock_render.return_value = mock_response

        response = await get_playlist_modal_response(mock_request, "target_playlist", "test_template.html")

        assert response.status_code == 200
        mock_render.assert_called_once()
        call_args = mock_render.call_args
        assert call_args[0][1] == "test_template.html"
        context = call_args[1]["context"]
        assert context["playlist_id"] == "target_playlist"
        assert context["playlist_name"] == "My Playlist"
        assert context["playlist_image_url"] == "http://example.com/small.jpg"
        assert len(context["playlists"]) == 2
        assert mock_playlist1 in context["playlists"]
        assert mock_playlist2 in context["playlists"]
        assert mock_playlist3 not in context["playlists"]
        assert mock_playlist4 not in context["playlists"]

    async def test_get_playlist_modal_response_spotify_exception(self) -> None:
        """Test that MottleException is raised when Spotify client fails."""
        mock_request = create_mock_request()
        mock_request.session = {"spotify_user_spotify_id": "user123"}

        async def mock_get_current_user_playlists() -> list[Mock]:
            raise MottleException("Spotify API error")

        mock_spotify_client = Mock(spec=MottleSpotifyClient)
        mock_spotify_client.get_playlist = create_mock_playlist_getter()
        mock_spotify_client.get_current_user_playlists = mock_get_current_user_playlists
        mock_request.spotify_client = mock_spotify_client

        with pytest.raises(MottleException, match="Spotify API error"):
            await get_playlist_modal_response(mock_request, "target_playlist", "test_template.html")
