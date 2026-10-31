import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.test import AsyncClient, RequestFactory, TestCase
from tekore.model import (
    Followers,
    FullAlbum,
    FullArtist,
    FullPlaylist,
    FullPlaylistTrack,
    FullTrack,
    Image,
    PlaylistTrack,
    PlaylistTrackPaging,
    PublicUser,
    SimpleAlbum,
    SimpleTrack,
    SimpleTrackPaging,
)

from tests.conftest import create_mock_artist_getter, create_mock_playlist_getter
from web.events.enums import EventType
from web.middleware import MottleExceptionMiddleware
from web.models import (
    Artist,
    Event,
    EventArtist,
    Playlist,
    PlaylistUpdate,
    PlaylistWatchConfig,
    SpotifyAuth,
    SpotifyAuthRequest,
    SpotifyUser,
    User,
)
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


def make_artist(artist_id: str, name: str) -> FullArtist:
    return FullArtist(
        id=artist_id,
        href=f"https://api.spotify.com/v1/artists/{artist_id}",
        type="artist",
        uri=f"spotify:artist:{artist_id}",
        external_urls={"spotify": f"https://open.spotify.com/artist/{artist_id}"},
        name=name,
        followers=Followers(href=None, total=0),
        genres=[],
        images=[Image(url=f"https://i.scdn.co/image/{artist_id}", height=64, width=64)],
        popularity=0,
    )


def make_simple_album(album_id: str, album_type: str = "album") -> SimpleAlbum:
    return SimpleAlbum(
        id=album_id,
        href=f"https://api.spotify.com/v1/albums/{album_id}",
        type="album",
        uri=f"spotify:album:{album_id}",
        external_urls={"spotify": f"https://open.spotify.com/album/{album_id}"},
        name=f"Album {album_id}",
        album_type=album_type,
        release_date="2020-01-01",
        release_date_precision="day",
        total_tracks=1,
        images=[Image(url=f"https://i.scdn.co/image/{album_id}", height=64, width=64)],
        artists=[],
    )


def make_full_album(album_id: str) -> FullAlbum:
    simple_album = make_simple_album(album_id).__dict__
    # Only a SimpleAlbum, listed among an artist's albums, says how the artist appears on it
    del simple_album["album_group"]

    return FullAlbum(
        **simple_album,
        copyrights=[],
        external_ids={},
        genres=[],
        label=None,
        popularity=0,
        tracks=SimpleTrackPaging(href="", items=[], limit=50, next=None, total=0, offset=0, previous=None),
    )


def make_simple_track(track_id: str) -> SimpleTrack:
    return SimpleTrack(
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
        artists=[],
    )


def make_user(user_id: str) -> PublicUser:
    return PublicUser(
        id=user_id,
        href=f"https://api.spotify.com/v1/users/{user_id}",
        type="user",
        uri=f"spotify:user:{user_id}",
        external_urls={"spotify": f"https://open.spotify.com/user/{user_id}"},
        display_name=f"User {user_id}",
        followers=Followers(href=None, total=0),
    )


def make_playlist(playlist_id: str, name: str, owner_id: str) -> FullPlaylist:
    paging = PlaylistTrackPaging(href="", items=[], limit=100, next=None, total=3, offset=0, previous=None)
    return FullPlaylist(
        id=playlist_id,
        href=f"https://api.spotify.com/v1/playlists/{playlist_id}",
        type="playlist",
        uri=f"spotify:playlist:{playlist_id}",
        external_urls={"spotify": f"https://open.spotify.com/playlist/{playlist_id}"},
        name=name,
        snapshot_id="snapshot",
        collaborative=False,
        public=True,
        description=None,
        images=[Image(url=f"https://i.scdn.co/image/{playlist_id}", height=64, width=64)],
        owner=make_user(owner_id),
        followers=Followers(href=None, total=0),
        primary_color=None,
        tracks=paging,
        items=paging,
    )


def make_playlist_track(track_id: str) -> FullPlaylistTrack:
    return FullPlaylistTrack(**make_full_track(track_id, f"album_of_{track_id}").__dict__, episode=False, track=True)


def make_playlist_item(track_id: str) -> PlaylistTrack:
    return PlaylistTrack(
        added_at=datetime(2026, 1, 1, tzinfo=UTC),
        added_by=make_user("adder"),
        is_local=False,
        track=make_playlist_track(track_id),
        primary_color=None,
        video_thumbnail=None,
    )


class SignedInTestCase(TestCase):
    """A signed-in user's requests, which go through the whole middleware stack.

    The middleware hands every view the mock Spotify client in `self.spotify`, whose methods each test sets.
    """

    def setUp(self) -> None:
        self.spotify_user = SpotifyUser.objects.create(spotify_id="me", display_name="Me")
        User.objects.create(spotify_user=self.spotify_user)
        SpotifyAuth.objects.create(
            spotify_user=self.spotify_user,
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            token_scope=settings.SPOTIFY_TOKEN_SCOPE,
        )

        session = SessionStore()
        session["spotify_user_id"] = str(self.spotify_user.id)
        session["spotify_user_spotify_id"] = self.spotify_user.spotify_id
        session["spotify_user_display_name"] = self.spotify_user.display_name
        session["feature_flags"] = {"events_enabled": True}
        session.save()
        self.async_client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key

        self.spotify = Mock(spec=MottleSpotifyClient)
        patcher = patch("web.middleware.MottleSpotifyClient", return_value=self.spotify)
        patcher.start()
        self.addCleanup(patcher.stop)


@pytest.mark.asyncio
class TestAlbums(SignedInTestCase):
    async def test_get_lists_artist_releases_by_type(self) -> None:
        """The discography modal names the artist and lists its releases, albums before singles."""
        self.spotify.get_artist = AsyncMock(return_value=make_artist("artist1", "The Artist"))
        self.spotify.get_artist_albums_separately_by_type = AsyncMock(
            return_value=[make_simple_album("single1", "single"), make_simple_album("album1", "album")]
        )

        response = await self.async_client.get("/artist/artist1/")

        assert response.status_code == 200
        content = response.content.decode()
        assert "Create The Artist Discography" in content
        assert content.index("Album album1") < content.index("Album single1")
        self.spotify.get_artist.assert_awaited_once_with("artist1")

    async def test_post_creates_discography_playlist_watching_the_artist(self) -> None:
        """A discography playlist is named after the artist unless given a name, and gets the chosen albums' tracks.

        With auto-update the playlist watches the artist, ignoring the albums that were not chosen.
        """
        self.spotify.get_artist = AsyncMock(return_value=make_artist("artist1", "The Artist"))
        self.spotify.get_artist_albums_separately_by_type = AsyncMock(
            return_value=[make_simple_album("album1"), make_simple_album("album2")]
        )
        self.spotify.get_tracks_in_albums = AsyncMock(return_value=[make_simple_track("track1")])
        self.spotify.create_playlist_with_tracks = AsyncMock(return_value=make_playlist("new", "New", "me"))
        self.spotify.get_current_user = AsyncMock(return_value=Mock(id="me", display_name="Me", email=None))

        response = await self.async_client.post("/artist/artist1/", {"album": ["album1"], "auto-update": "on"})

        assert response.status_code == 302
        assert response["Location"] == "/playlist/new/"
        self.spotify.get_tracks_in_albums.assert_awaited_once_with(["album1"])
        self.spotify.create_playlist_with_tracks.assert_awaited_once_with(
            "me", name="The Artist discography", track_uris=["spotify:track:track1"], is_public=False
        )
        config = await PlaylistWatchConfig.objects.select_related("watching_playlist", "watched_artist").aget()
        assert config.watching_playlist.spotify_id == "new"
        assert config.watched_artist is not None
        assert config.watched_artist.spotify_id == "artist1"
        assert config.albums_ignored == ["album2"]

    async def test_post_without_albums_is_rejected_with_a_toast(self) -> None:
        """A discography with no album chosen is a bad request, which the user is told about in an error toast."""
        self.spotify.get_artist = AsyncMock(return_value=make_artist("artist1", "The Artist"))
        self.spotify.get_artist_albums_separately_by_type = AsyncMock(return_value=[make_simple_album("album1")])

        response = await self.async_client.post("/artist/artist1/", {})

        assert response.status_code == 400
        assert json.loads(response["HX-Trigger"]) == {"HXToast": {"type": "error", "body": "No albums selected"}}


@pytest.mark.asyncio
class TestAlbum(SignedInTestCase):
    async def test_lists_album_tracks(self) -> None:
        """The album page names the album and lists its tracks, each with the album's cover."""
        self.spotify.get_album = AsyncMock(return_value=make_full_album("album1"))
        self.spotify.get_album_tracks = AsyncMock(return_value=[make_simple_track("track1")])

        response = await self.async_client.get("/album/album1/")

        assert response.status_code == 200
        content = response.content.decode()
        assert '<a href="https://open.spotify.com/album/album1">Album album1</a>' in content
        assert '<a href="https://open.spotify.com/track/track1">Track track1</a>' in content
        assert '<img class="small" src="https://i.scdn.co/image/album1" />' in content
        self.spotify.get_album.assert_awaited_once_with("album1")


@pytest.mark.asyncio
class TestPlaylistItems(SignedInTestCase):
    async def test_lists_playlist_tracks(self) -> None:
        """The playlist page names the playlist and lists its tracks."""
        self.spotify.get_playlist = AsyncMock(return_value=make_playlist("playlist1", "My Playlist", "me"))
        self.spotify.get_playlist_tracks = AsyncMock(return_value=[make_playlist_item("track1")])
        self.spotify.get_current_user_playlists = AsyncMock(return_value=[])

        response = await self.async_client.get("/playlist/playlist1/")

        assert response.status_code == 200
        content = response.content.decode()
        assert 'href="https://open.spotify.com/playlist/playlist1"' in content
        assert "My Playlist" in content
        assert "Track track1" in content
        self.spotify.get_playlist.assert_awaited_once_with("playlist1")


@pytest.mark.asyncio
class TestPlaylistUpdatesPage(SignedInTestCase):
    async def test_accepts_updates_of_the_playlist(self) -> None:
        """The updates modal's buttons accept the playlist's updates, one or all of them."""
        watching = await Playlist.objects.acreate(spotify_id="playlist1", spotify_user=self.spotify_user)
        artist = await Artist.objects.acreate(spotify_id="artist1")
        update = await PlaylistUpdate.objects.acreate(
            target_playlist=watching, source_artist=artist, albums_added=["album1"]
        )
        self.spotify.get_playlist = AsyncMock(return_value=make_playlist("playlist1", "My Playlist", "me"))
        self.spotify.get_artist = AsyncMock(return_value=make_artist("artist1", "The Artist"))
        self.spotify.get_tracks_in_albums = AsyncMock(return_value=[make_simple_track("track1")])
        self.spotify.get_tracks = AsyncMock(return_value=[make_full_track("track1", "album1")])

        with patch("web.views.check_playlist_for_updates", AsyncMock()):
            response = await self.async_client.get("/playlist/playlist1/updates/")

        assert response.status_code == 200
        content = response.content.decode()
        assert 'hx-post="/playlist/playlist1/accept-all-updates/"' in content
        assert f'hx-post="/playlist/playlist1/updates/{update.id}/accept/"' in content


@pytest.mark.asyncio
class TestDeduplicatePlaylist(SignedInTestCase):
    async def test_get_lists_duplicates(self) -> None:
        """The deduplication modal lists each duplicated track of the playlist."""
        self.spotify.get_playlist = AsyncMock(return_value=make_playlist("playlist1", "My Playlist", "me"))
        self.spotify.find_duplicate_tracks_in_playlist = AsyncMock(return_value=[(make_playlist_track("track1"), 2)])

        response = await self.async_client.get("/playlist/playlist1/deduplicate/")

        assert response.status_code == 200
        assert "Track track1" in response.content.decode()

    async def test_post_replaces_every_copy_with_one(self) -> None:
        """Every copy of a duplicated track is removed from the user's own playlist, and one is added back."""
        self.spotify.get_playlist = AsyncMock(return_value=make_playlist("playlist1", "My Playlist", "me"))
        self.spotify.remove_tracks_from_playlist = AsyncMock()
        self.spotify.add_tracks_to_playlist = AsyncMock()

        response = await self.async_client.post("/playlist/playlist1/deduplicate/", {"track-ids": ["track1"]})

        assert response.status_code == 200
        self.spotify.remove_tracks_from_playlist.assert_awaited_once_with("playlist1", ["spotify:track:track1"])
        self.spotify.add_tracks_to_playlist.assert_awaited_once_with("playlist1", ["spotify:track:track1"])

    async def test_post_refuses_another_users_playlist(self) -> None:
        """A playlist someone else owns is not deduplicated."""
        self.spotify.get_playlist = AsyncMock(return_value=make_playlist("playlist1", "Their Playlist", "someone"))
        self.spotify.remove_tracks_from_playlist = AsyncMock()

        response = await self.async_client.post("/playlist/playlist1/deduplicate/", {"track-ids": ["track1"]})

        assert response.status_code == 400
        self.spotify.remove_tracks_from_playlist.assert_not_awaited()


@pytest.mark.asyncio
class TestCopyPlaylist(SignedInTestCase):
    async def test_copies_tracks_into_a_new_playlist(self) -> None:
        """A copy is a new playlist named after the original, with the original's tracks."""
        self.spotify.get_playlist = AsyncMock(
            side_effect=[make_playlist("playlist1", "My Playlist", "me"), make_playlist("copy", "Copy", "me")]
        )
        self.spotify.get_playlist_tracks = AsyncMock(return_value=[make_playlist_item("track1")])
        self.spotify.create_playlist_with_tracks = AsyncMock(return_value=make_playlist("copy", "Copy", "me"))

        response = await self.async_client.post("/playlist/playlist1/copy/")

        assert response.status_code == 200
        create_playlist_kwargs = self.spotify.create_playlist_with_tracks.call_args.kwargs
        assert create_playlist_kwargs["name"] == "Copy of My Playlist"
        assert create_playlist_kwargs["track_uris"] == ["spotify:track:track1"]
        assert "/playlist/copy/" in response.content.decode()


@pytest.mark.asyncio
class TestConfigurePlaylist(SignedInTestCase):
    async def test_lists_watched_playlists_and_artists(self) -> None:
        """The configuration modal names the playlist and what it watches."""
        watching = await Playlist.objects.acreate(spotify_id="playlist1", spotify_user=self.spotify_user)
        watched_playlist = await Playlist.objects.acreate(spotify_id="watched")
        watched_artist = await Artist.objects.acreate(spotify_id="artist1")
        await PlaylistWatchConfig.objects.acreate(watching_playlist=watching, watched_playlist=watched_playlist)
        await PlaylistWatchConfig.objects.acreate(watching_playlist=watching, watched_artist=watched_artist)

        playlists = {
            "playlist1": make_playlist("playlist1", "My Playlist", "me"),
            "watched": make_playlist("watched", "Watched Playlist", "someone"),
        }
        self.spotify.get_playlist = AsyncMock(side_effect=playlists.get)
        self.spotify.get_artist = AsyncMock(return_value=make_artist("artist1", "Watched Artist"))

        response = await self.async_client.get("/playlist/playlist1/configuration/")

        assert response.status_code == 200
        content = response.content.decode()
        assert 'alt="My Playlist cover"' in content
        assert "Watched Playlist" in content
        assert "Watched Artist" in content


@pytest.mark.asyncio
class TestRenamePlaylist(SignedInTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.spotify.get_playlist = AsyncMock(return_value=make_playlist("playlist1", "My Playlist", "me"))
        self.spotify.change_playlist_details = AsyncMock()

    async def test_get_shows_the_name_in_a_form(self) -> None:
        """The rename form starts with the playlist's current name."""
        response = await self.async_client.get("/playlist/playlist1/rename/")

        assert response.status_code == 200
        assert 'value="My Playlist"' in response.content.decode()

    async def test_post_renames(self) -> None:
        """A new name is saved at Spotify and shown in place of the form."""
        response = await self.async_client.post("/playlist/playlist1/rename/", {"name": "New Name"})

        assert response.status_code == 200
        assert 'title="New Name">New Name</h5>' in response.content.decode()
        self.spotify.change_playlist_details.assert_awaited_once_with("playlist1", "New Name")

    async def test_post_without_name_is_rejected_with_a_toast(self) -> None:
        """An empty name is a bad request, which the user is told about in an error toast."""
        response = await self.async_client.post("/playlist/playlist1/rename/", {"name": ""})

        assert response.status_code == 400
        assert json.loads(response["HX-Trigger"]) == {"HXToast": {"type": "error", "body": "No name provided"}}
        self.spotify.change_playlist_details.assert_not_awaited()

    async def test_delete_shows_the_current_name(self) -> None:
        """Cancelling the rename shows the playlist's name again."""
        response = await self.async_client.delete("/playlist/playlist1/rename/")

        assert response.status_code == 200
        assert 'title="My Playlist">My Playlist</h5>' in response.content.decode()


@pytest.mark.asyncio
class TestPlaylistCoverImage(SignedInTestCase):
    async def test_generates_a_cover_for_the_playlist_name(self) -> None:
        """A cover image for the playlist's name is generated in the background."""
        self.spotify.get_playlist = AsyncMock(return_value=make_playlist("playlist1", "My Playlist", "me"))

        with (
            patch("web.views.upload_cover_image", Mock(return_value="coroutine")) as upload_cover_image,
            patch("web.views.run_in_background") as run_in_background,
        ):
            response = await self.async_client.post("/playlist/playlist1/cover-image/")

        assert response.status_code == 200
        assert upload_cover_image.call_args.kwargs["playlist_title"] == "My Playlist"
        assert upload_cover_image.call_args.kwargs["playlist_spotify_id"] == "playlist1"
        assert upload_cover_image.call_args.kwargs["spotify_user_id"] == str(self.spotify_user.id)
        run_in_background.assert_called_once_with("coroutine")


@pytest.mark.asyncio
class TestArtistEvents(SignedInTestCase):
    async def test_tracks_the_artist_and_lists_upcoming_events(self) -> None:
        """The artist's events are tracked for the user under the artist's name, and the upcoming ones are listed."""
        artist = await Artist.objects.acreate(spotify_id="artist1")
        event_artist = await EventArtist.objects.acreate(
            artist=artist, songkick_name_match_accuracy=0, bandsintown_name_match_accuracy=0
        )
        await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/1",
            type=EventType.concert,
            date=date(2100, 1, 1),
            venue="The Venue",
            city="Berlin",
        )
        self.spotify.get_artist = AsyncMock(return_value=make_artist("artist1", "The Artist"))

        with (
            patch("web.views.track_artist_events", AsyncMock(return_value=event_artist)) as track_artist_events,
            patch.object(EventArtist, "update_events", AsyncMock()),
        ):
            response = await self.async_client.get("/artist/artist1/events/")

        assert response.status_code == 200
        assert "The Venue" in response.content.decode()
        track_artist_events.assert_awaited_once_with("artist1", "The Artist", str(self.spotify_user.id))


@pytest.mark.asyncio
class TestEventDetails(SignedInTestCase):
    async def test_names_the_artist(self) -> None:
        """The event modal names the event's artist."""
        artist = await Artist.objects.acreate(spotify_id="artist1")
        event_artist = await EventArtist.objects.acreate(
            artist=artist, songkick_name_match_accuracy=0, bandsintown_name_match_accuracy=0
        )
        event = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/1",
            type=EventType.concert,
            date=date(2100, 1, 1),
            venue="The Venue",
        )
        self.spotify.get_artist = AsyncMock(return_value=make_artist("artist1", "The Artist"))

        response = await self.async_client.get(f"/event/{event.id}/")

        assert response.status_code == 200
        assert "<h2>The Artist</h2>" in response.content.decode()
        self.spotify.get_artist.assert_awaited_once_with("artist1")


@pytest.mark.asyncio
class TestPlaylistModals(SignedInTestCase):
    async def test_merge_offers_the_users_other_playlists(self) -> None:
        """The merge modal names the playlist and offers as targets only the user's own other playlists."""
        self.spotify.get_playlist = AsyncMock(return_value=make_playlist("playlist1", "My Playlist", "me"))
        self.spotify.get_current_user_playlists = AsyncMock(
            return_value=[
                make_playlist("playlist1", "My Playlist", "me"),
                make_playlist("mine", "Another Of Mine", "me"),
                make_playlist("theirs", "Someone Else's", "someone"),
            ]
        )

        response = await self.async_client.get("/playlist/playlist1/merge/")

        assert response.status_code == 200
        content = response.content.decode()
        assert 'title="My Playlist">My Playlist</p>' in content
        assert '<option value="mine">Another Of Mine</option>' in content
        assert '<option value="playlist1">' not in content
        assert "theirs" not in content
