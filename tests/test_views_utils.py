import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from django.test import TestCase
from minify_html import minify
from tekore.model import FullAlbum, FullArtist, FullPlaylist, FullTrack, PublicUser

from web.events.enums import EventType
from web.models import Artist, Event, EventArtist, EventUpdate, Playlist, PlaylistUpdate
from web.utils import MottleSpotifyClient
from web.views_utils import camel_to_snake, compile_event_updates_email, compile_playlist_updates_email


def test_camel_to_snake() -> None:
    assert camel_to_snake("Playlist") == "playlist"
    assert camel_to_snake("PlaylistMetadata") == "playlist_metadata"
    assert camel_to_snake("HTTPResponse") == "h_t_t_p_response"
    assert camel_to_snake("Artist") == "artist"
    assert camel_to_snake("already_snake") == "already_snake"


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

        async def mock_get_artist(spotify_id: str) -> Mock:
            mock_artist = Mock(spec=FullArtist)
            if spotify_id == "artist1":
                mock_artist.name = "The Beatles"
            elif spotify_id == "artist2":
                mock_artist.name = "Pink Floyd"
            else:
                mock_artist.name = "Led Zeppelin"
            return mock_artist

        mock_spotify_client.get_artist = mock_get_artist

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
            tracks_removed=None,
            albums_added=None,
            albums_removed=None,
            update_hash="hash1_1",
            is_accepted=True,
        )

        # Update 1.2: Albums added from watched artist (auto-accept failed)
        update1_2 = await PlaylistUpdate.objects.acreate(
            target_playlist=target_playlist1,
            source_playlist=None,
            source_artist=watched_artist1,
            tracks_added=None,
            tracks_removed=None,
            albums_added=["album1", "album2"],
            albums_removed=None,
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
            tracks_removed=None,
            albums_added=None,
            albums_removed=None,
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
            tracks_removed=None,
            albums_added=None,
            albums_removed=None,
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
            tracks_removed=None,
            albums_added=album_ids,
            albums_removed=None,
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
            tracks_removed=None,
            albums_added=None,
            albums_removed=None,
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
            tracks_removed=None,
            albums_added=album_ids_2,
            albums_removed=None,
            update_hash="hash6_1",
            is_accepted=None,
        )

        # Mock Spotify client
        mock_spotify_client = Mock(spec=MottleSpotifyClient)

        # Mock get_playlist
        async def mock_get_playlist(spotify_id: str) -> Mock:
            mock_playlist = Mock(spec=FullPlaylist)
            mock_owner = Mock(spec=PublicUser)
            if spotify_id == "target_playlist1":
                mock_playlist.name = "My Chill Playlist"
                mock_owner.display_name = "John Doe"
            elif spotify_id == "watched_playlist1":
                mock_playlist.name = "Indie Discoveries"
                mock_owner.display_name = "Jane Smith"
            elif spotify_id == "target_playlist2":
                mock_playlist.name = "Workout Mix"
                mock_owner.display_name = "John Doe"
            elif spotify_id == "watched_playlist2":
                mock_playlist.name = "Top Rock Hits"
                mock_owner.display_name = "Bob Johnson"
            elif spotify_id == "target_playlist3":
                mock_playlist.name = "Many Tracks Playlist"
                mock_owner.display_name = "John Doe"
            elif spotify_id == "watched_playlist3":
                mock_playlist.name = "Mega Playlist"
                mock_owner.display_name = "Jane Smith"
            elif spotify_id == "target_playlist4":
                mock_playlist.name = "Album Collection"
                mock_owner.display_name = "John Doe"
            elif spotify_id == "target_playlist5":
                mock_playlist.name = "Exactly Ten Tracks"
                mock_owner.display_name = "John Doe"
            elif spotify_id == "watched_playlist5":
                mock_playlist.name = "Boundary Playlist"
                mock_owner.display_name = "Jane Smith"
            elif spotify_id == "target_playlist6":
                mock_playlist.name = "Few Albums Playlist"
                mock_owner.display_name = "John Doe"
            mock_playlist.owner = mock_owner
            return mock_playlist

        mock_spotify_client.get_playlist = mock_get_playlist

        # Mock get_artist
        async def mock_get_artist(spotify_id: str) -> Mock:
            mock_artist = Mock(spec=FullArtist)
            if spotify_id == "watched_artist1":
                mock_artist.name = "The Weeknd"
            elif spotify_id == "watched_artist2":
                mock_artist.name = "Taylor Swift"
            elif spotify_id == "watched_artist6":
                mock_artist.name = "Dua Lipa"
            return mock_artist

        mock_spotify_client.get_artist = mock_get_artist

        # Mock get_tracks
        async def mock_get_tracks(track_ids: list[str]) -> list[Mock]:
            tracks = []
            for track_id in track_ids:
                mock_track = Mock(spec=FullTrack)
                if track_id == "track1":
                    mock_track.name = "Blinding Lights"
                    mock_artist = Mock(spec=FullArtist)
                    mock_artist.name = "The Weeknd"
                    mock_track.artists = [mock_artist]
                elif track_id == "track2":
                    mock_track.name = "Save Your Tears"
                    mock_artist = Mock(spec=FullArtist)
                    mock_artist.name = "The Weeknd"
                    mock_track.artists = [mock_artist]
                elif track_id == "track3":
                    mock_track.name = "Starboy"
                    mock_artist1 = Mock(spec=FullArtist)
                    mock_artist1.name = "The Weeknd"
                    mock_artist2 = Mock(spec=FullArtist)
                    mock_artist2.name = "Daft Punk"
                    mock_track.artists = [mock_artist1, mock_artist2]
                elif track_id == "track4":
                    mock_track.name = "Bohemian Rhapsody"
                    mock_artist = Mock(spec=FullArtist)
                    mock_artist.name = "Queen"
                    mock_track.artists = [mock_artist]
                elif track_id == "track5":
                    mock_track.name = "Stairway to Heaven"
                    mock_artist = Mock(spec=FullArtist)
                    mock_artist.name = "Led Zeppelin"
                    mock_track.artists = [mock_artist]
                else:
                    # Handle generic tracks (track6, track7, etc.)
                    track_num = int(track_id.replace("track", ""))
                    mock_track.name = f"Track {track_num}"
                    mock_artist = Mock(spec=FullArtist)
                    mock_artist.name = f"Artist {track_num}"
                    mock_track.artists = [mock_artist]
                tracks.append(mock_track)
            return tracks

        mock_spotify_client.get_tracks = mock_get_tracks

        # Mock get_albums
        async def mock_get_albums(album_ids: list[str]) -> list[Mock]:
            albums = []
            for album_id in album_ids:
                mock_album = Mock(spec=FullAlbum)
                if album_id == "album1":
                    mock_album.name = "After Hours"
                    mock_album.release_date = "2020-03-20"
                elif album_id == "album2":
                    mock_album.name = "Starboy"
                    mock_album.release_date = "2016-11-25"
                else:
                    # Handle generic albums (album3, album4, etc.)
                    album_num = int(album_id.replace("album", ""))
                    mock_album.name = f"Album {album_num}"
                    mock_album.release_date = f"202{album_num % 5}-0{(album_num % 9) + 1}-01"
                albums.append(mock_album)
            return albums

        mock_spotify_client.get_albums = mock_get_albums

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
