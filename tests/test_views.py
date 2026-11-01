import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from django.conf import settings
from django.contrib.gis.geos import Point
from django.contrib.sessions.backends.db import SessionStore
from django.test import AsyncClient, RequestFactory, TestCase
from lxml import html as lh
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
    from django.http import HttpResponse

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
class TestSignIn(SignedInTestCase):
    """A user is signed in while their session names a Spotify authorization that can still be used.

    That is one with a refresh token and the scope the app asks for, which is refreshed when it is about to expire.
    The login page sends a signed-in user on, and any other page sends anyone else to the login page.
    """

    async def assert_signed_out(self) -> None:
        response = await self.async_client.get("/login/", {"next": "/playlists/"})
        assert response.status_code == 200
        assert '<input type="hidden" name="redirect_uri" value="/playlists/">' in response.content.decode()

        response = await self.async_client.get("/playlists/")
        assert response.status_code == 302
        assert response["Location"] == "/login/?next=/playlists/"

    async def test_signed_in(self) -> None:
        """A signed-in user is sent from the login page to the index, and gets other pages."""
        self.spotify.get_current_user_playlists = AsyncMock(return_value=[])

        response = await self.async_client.get("/login/")
        assert response.status_code == 302
        assert response["Location"] == "/"

        response = await self.async_client.get("/playlists/")
        assert response.status_code == 200

    async def test_without_session(self) -> None:
        """A visitor without a session is signed out."""
        self.async_client.cookies.clear()

        await self.assert_signed_out()

    async def test_without_authorization(self) -> None:
        """A session whose user has no authorization is signed out."""
        await SpotifyAuth.objects.all().adelete()

        await self.assert_signed_out()

    async def test_without_refresh_token(self) -> None:
        """An authorization without a refresh token is signed out."""
        await SpotifyAuth.objects.all().aupdate(refresh_token=None)

        await self.assert_signed_out()

    async def test_outdated_scope(self) -> None:
        """An authorization for another scope than the app asks for is signed out, so the user grants the app's."""
        await SpotifyAuth.objects.all().aupdate(token_scope=settings.SPOTIFY_TOKEN_SCOPE[1:])

        await self.assert_signed_out()

    async def test_refresh_failure(self) -> None:
        """An expired authorization that Spotify refuses to refresh is signed out, and the failure is logged."""
        await SpotifyAuth.objects.all().aupdate(expires_at=datetime.now(tz=UTC) - timedelta(minutes=1))

        with (
            patch.object(settings.SPOTIFY_CREDEINTIALS, "refresh", side_effect=Exception("invalid_grant")),
            self.assertLogs("web", level="ERROR") as logs,
        ):
            await self.assert_signed_out()

        assert [record.getMessage() for record in logs.records] == ["Failed to refresh token"] * 2

    async def test_index_sends_a_signed_in_user_to_their_playlists(self) -> None:
        """The index is the landing page for visitors; a signed-in user goes on to their playlists."""
        response = await self.async_client.get("/")
        assert response.status_code == 302
        assert response["Location"] == "/playlists/"

        self.async_client.cookies.clear()

        response = await self.async_client.get("/")
        assert response.status_code == 200
        assert "Continue with Spotify" in response.content.decode()

    async def test_exempt_page_without_session(self) -> None:
        """A page anyone may see is shown without a session."""
        self.async_client.cookies.clear()

        response = await self.async_client.get("/changelog/")

        assert response.status_code == 200


@pytest.mark.asyncio
class TestSearch(SignedInTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.spotify.find_artists_and_playlists = AsyncMock(
            return_value=(
                [make_artist("artist1", "The Artist")],
                [make_playlist("theirs", "Their Playlist", "someone")],
            )
        )
        self.spotify.get_current_user_playlists = AsyncMock(return_value=[])

    async def test_typing_updates_the_results_in_place(self) -> None:
        """A query typed into the search box gets only the results, for the page to swap in, and the query's URL."""
        response = await self.async_client.get("/search/", {"query": "the artist"}, headers={"HX-Request": "true"})

        assert response.status_code == 200
        page = lh.fromstring(response.content)
        assert page.get("id") == "search-results"
        assert page.xpath("//a[@href='/artist/artist1/']//h3/text()") == ["The Artist"]
        assert page.xpath("//a[@href='/playlist/theirs/']//h5/text()") == ["Their Playlist"]
        assert response["HX-Push-Url"] == "?query=the artist"

    async def test_navigating_shows_the_whole_page(self) -> None:
        """Searching from the navigation bar, boosted or not, leads to the whole search page."""
        for headers in ({}, {"HX-Request": "true", "HX-Boosted": "true"}):
            response = await self.async_client.get("/search/", {"query": "the artist"}, headers=headers)

            assert response.status_code == 200
            page = lh.fromstring(response.content)
            assert page.xpath("//input[@id='query']/@value") == ["the artist"]
            assert page.xpath("//div[@id='search-results']")
            assert "HX-Push-Url" not in response


@pytest.mark.asyncio
class TestAlbums(SignedInTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.spotify.get_artist = AsyncMock(return_value=make_artist("artist1", "The Artist"))
        self.spotify.get_artist_albums_separately_by_type = AsyncMock(
            return_value=[make_simple_album("single1", "single"), make_simple_album("album1", "album")]
        )

    async def test_get_from_a_button_opens_the_discography_modal(self) -> None:
        """An HTMX request a button makes gets the discography modal, naming the artist and listing its releases,
        albums before singles, each a checkbox of the form."""
        response = await self.async_client.get("/artist/artist1/", headers={"HX-Request": "true"})

        assert response.status_code == 200
        page = lh.fromstring(response.content)
        assert page.tag == "dialog"
        assert page.xpath("//h2[@class='modal-title']/text()") == ["Create The Artist discography"]
        assert page.xpath("//input[@name='album']/@value") == ["album1", "single1"]
        self.spotify.get_artist.assert_awaited_once_with("artist1")

    async def test_get_from_a_link_shows_the_artist_page(self) -> None:
        """Following a link, boosted or not, leads to the artist's page, listing the releases by type, albums first,
        with a button opening the discography modal."""
        for headers in ({}, {"HX-Request": "true", "HX-Boosted": "true"}):
            response = await self.async_client.get("/artist/artist1/", headers=headers)

            assert response.status_code == 200
            page = lh.fromstring(response.content)
            assert page.xpath("normalize-space(//h1)") == "The Artist"
            assert page.xpath("normalize-space((//section/h2)[1])") == "Albums 1"
            assert page.xpath("normalize-space((//section/h2)[2])") == "Singles and EPs 1"
            assert page.xpath("//main//a[starts-with(@href, '/album/')]/@href") == ["/album/album1/", "/album/single1/"]
            assert page.xpath("//button[normalize-space()='Create discography']/@hx-get") == ["/artist/artist1/"]

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
        page = lh.fromstring(response.content)
        assert page.xpath("normalize-space(//h1)") == "Album album1"
        assert "https://i.scdn.co/image/album1" in str(page.xpath("string(//section[@class='hero']/@style)"))
        assert page.xpath("//a[normalize-space()='Open in Spotify']/@href") == ["https://open.spotify.com/album/album1"]
        assert page.xpath("//table//a[@href='https://open.spotify.com/track/track1']/text()") == ["Track track1"]
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

    async def get_page_with_watch_configs(self, num_configs: int) -> str:
        """Return the playlist page of a playlist of the user's that watches as many playlists as given."""
        self.spotify.get_playlist = AsyncMock(return_value=make_playlist("playlist1", "My Playlist", "me"))
        self.spotify.get_playlist_tracks = AsyncMock(return_value=[])
        self.spotify.get_current_user_playlists = AsyncMock(return_value=[])

        watching = await Playlist.objects.acreate(spotify_id="playlist1", spotify_user=self.spotify_user)
        for i in range(num_configs):
            watched = await Playlist.objects.acreate(spotify_id=f"watched{i}")
            await PlaylistWatchConfig.objects.acreate(watching_playlist=watching, watched_playlist=watched)

        response = await self.async_client.get("/playlist/playlist1/")

        assert response.status_code == 200
        return response.content.decode()

    async def test_offers_updates_of_a_watching_playlist(self) -> None:
        """A playlist watching one or more others gets a button to its updates, and one watching none does not."""
        updates_button = 'hx-get="/playlist/playlist1/updates/"'

        assert updates_button not in await self.get_page_with_watch_configs(0)
        await Playlist.objects.all().adelete()
        assert updates_button in await self.get_page_with_watch_configs(1)
        await PlaylistWatchConfig.objects.all().adelete()
        await Playlist.objects.all().adelete()
        assert updates_button in await self.get_page_with_watch_configs(2)


# How htmx encodes the form fields it posts
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"


@pytest.mark.asyncio
class TestUnwatchPlaylist(SignedInTestCase):
    async def test_stops_watching_the_playlist(self) -> None:
        """The watching playlist's config and updates for the watched playlist go, and the user is told in a toast.

        The configuration modal posts the watching playlist's ID as a form field.
        """
        watching = await Playlist.objects.acreate(spotify_id="watching", spotify_user=self.spotify_user)
        watched = await Playlist.objects.acreate(spotify_id="watched")
        other = await Playlist.objects.acreate(spotify_id="other")
        await PlaylistWatchConfig.objects.acreate(watching_playlist=watching, watched_playlist=watched)
        await PlaylistWatchConfig.objects.acreate(watching_playlist=watching, watched_playlist=other)
        await PlaylistUpdate.objects.acreate(target_playlist=watching, source_playlist=watched, tracks_added=["t1"])

        response = await self.async_client.post(
            "/playlist/watched/unwatch/", "watching-playlist-id=watching", content_type=FORM_CONTENT_TYPE
        )

        assert response.status_code == 200
        assert json.loads(response["HX-Trigger"]) == {
            "HXToast": {"type": "success", "body": "Successfully unwatched playlist"}
        }
        assert [c.watched_playlist_id async for c in PlaylistWatchConfig.objects.all()] == [other.id]
        assert not await PlaylistUpdate.objects.aexists()

    async def test_without_watching_playlist_is_rejected_with_a_toast(self) -> None:
        """A request naming no watching playlist is a bad request, which the user is told about in an error toast."""
        response = await self.async_client.post("/playlist/watched/unwatch/", "", content_type=FORM_CONTENT_TYPE)

        assert response.status_code == 400
        assert json.loads(response["HX-Trigger"]) == {
            "HXToast": {"type": "error", "body": "No watching playlist ID provided"}
        }

    async def test_another_users_playlist_is_not_found(self) -> None:
        """A watching playlist of another user's is not found."""
        someone = await SpotifyUser.objects.acreate(spotify_id="someone")
        await Playlist.objects.acreate(spotify_id="watching", spotify_user=someone)
        await Playlist.objects.acreate(spotify_id="watched")

        response = await self.async_client.post(
            "/playlist/watched/unwatch/", "watching-playlist-id=watching", content_type=FORM_CONTENT_TYPE
        )

        assert response.status_code == 404


@pytest.mark.asyncio
class TestUserSettings(SignedInTestCase):
    async def test_get_shows_saved_settings(self) -> None:
        """The settings page shows the user's notification choices and event location."""
        await User.objects.filter(spotify_user=self.spotify_user).aupdate(
            playlist_notifications=False,
            event_notifications=True,
            location=Point(13.4, 52.5, srid=settings.GEODJANGO_SRID),
            event_distance_threshold=50,
        )

        response = await self.async_client.get("/settings/")

        assert response.status_code == 200
        page = lh.fromstring(response.content)
        assert page.xpath("//input[@type='checkbox'][@checked]/@name") == ["event-notifications"]
        assert page.xpath("//input[@name='latitude']/@value") == ["52.5"]
        assert page.xpath("//input[@name='longitude']/@value") == ["13.4"]
        assert page.xpath("//input[@name='radius']/@value") == ["50.0"]

    async def test_post_saves_settings(self) -> None:
        """Posted notification choices and location are saved, shown again, and confirmed in a toast."""
        response = await self.async_client.post(
            "/settings/",
            {"event-notifications": "on", "latitude": "52.5", "longitude": "13.4", "radius": "25"},
        )

        assert response.status_code == 200
        assert json.loads(response["HX-Trigger"]) == {
            "HXToast": {"type": "success", "body": "Settings updated successfully"}
        }
        assert 'value="25.0"' in response.content.decode()
        user = await User.objects.aget(spotify_user=self.spotify_user)
        assert user.playlist_notifications is False
        assert user.event_notifications is True
        assert user.location is not None
        assert user.location.coords == (13.4, 52.5)
        assert user.event_distance_threshold == 25

    async def test_post_event_notifications_without_location_is_rejected_with_a_toast(self) -> None:
        """Event notifications need a location and radius, without which nothing is saved and the user is told so."""
        response = await self.async_client.post("/settings/", {"event-notifications": "on"})

        assert response.status_code == 400
        assert json.loads(response["HX-Trigger"]) == {
            "HXToast": {"type": "error", "body": "Location and radius must be provided for event notifications"}
        }
        user = await User.objects.aget(spotify_user=self.spotify_user)
        assert user.event_notifications is False


@pytest.mark.asyncio
class TestToasts(SignedInTestCase):
    """Views that answer with nothing but a toast tell the user how the action went."""

    async def assert_toast(self, response: HttpResponse, status_code: int, toast_type: str, body: str) -> None:
        assert response.status_code == status_code
        assert json.loads(response["HX-Trigger"]) == {"HXToast": {"type": toast_type, "body": body}}

    async def test_follow_and_unfollow(self) -> None:
        """Following and unfollowing a playlist is confirmed in a toast."""
        self.spotify.follow_playlist = AsyncMock()
        self.spotify.unfollow_playlist = AsyncMock()

        response = await self.async_client.post("/playlist/playlist1/follow/")
        await self.assert_toast(response, 200, "success", "Successfully added playlist")

        response = await self.async_client.post("/playlist/playlist1/unfollow/")
        await self.assert_toast(response, 200, "success", "Successfully removed playlist")

    async def test_merge_without_target(self) -> None:
        """Merging into a new playlist needs its name, and merging into an existing one needs the target."""
        response = await self.async_client.post("/playlist/playlist1/merge/", {"merge-type": "new"})
        await self.assert_toast(response, 400, "error", "No new playlist name provided")

        response = await self.async_client.post("/playlist/playlist1/merge/", {"merge-type": "existing"})
        await self.assert_toast(response, 400, "error", "No merge target provided")

    async def test_merge(self) -> None:
        """A merge adds the source's tracks to the target and is confirmed in a toast."""
        self.spotify.get_playlist_tracks = AsyncMock(return_value=[make_playlist_item("track1")])
        self.spotify.add_tracks_to_playlist = AsyncMock()

        response = await self.async_client.post(
            "/playlist/playlist1/merge/", {"merge-type": "existing", "merge-target": "target"}
        )

        await self.assert_toast(response, 200, "success", "Successfully merged playlists")
        self.spotify.add_tracks_to_playlist.assert_awaited_once_with("target", ["spotify:track:track1"])

    async def test_watch_without_watching_playlist(self) -> None:
        """Watching needs a watching playlist, and a name for one to create."""
        response = await self.async_client.post("/playlist/playlist1/watch/", {})
        await self.assert_toast(response, 400, "error", "No watching playlist provided")

        response = await self.async_client.post(
            "/playlist/playlist1/watch/", {"watching-playlist-id": "--- Create new ---"}
        )
        await self.assert_toast(response, 400, "error", "No watching playlist name provided")

    async def test_watch(self) -> None:
        """Starting to watch a playlist is confirmed in a toast."""
        self.spotify.get_current_user = AsyncMock(return_value=Mock(id="me", display_name="Me", email=None))

        response = await self.async_client.post("/playlist/watched/watch/", {"watching-playlist-id": "watching"})

        await self.assert_toast(response, 200, "success", "Successfully started watching playlist")
        assert await PlaylistWatchConfig.objects.filter(
            watching_playlist__spotify_id="watching", watched_playlist__spotify_id="watched"
        ).aexists()

    async def test_auto_accept_toggle(self) -> None:
        """Auto-accept needs the watched playlist or artist, and its toggling is confirmed in a toast."""
        watching = await Playlist.objects.acreate(spotify_id="watching", spotify_user=self.spotify_user)
        watched = await Playlist.objects.acreate(spotify_id="watched")
        await PlaylistWatchConfig.objects.acreate(watching_playlist=watching, watched_playlist=watched)

        response = await self.async_client.post("/playlist/watching/auto-accept-updates/", {})
        await self.assert_toast(response, 400, "error", "No watched playlist or artist ID provided")

        response = await self.async_client.post(
            "/playlist/watching/auto-accept-updates/", {"watched-playlist-id": "watched"}
        )
        await self.assert_toast(response, 200, "success", "Auto-accept enabled")

    async def test_accept_updates(self) -> None:
        """Accepting one or all of a playlist's updates is confirmed in a toast."""
        watching = await Playlist.objects.acreate(spotify_id="watching", spotify_user=self.spotify_user)
        watched = await Playlist.objects.acreate(spotify_id="watched")
        update = await PlaylistUpdate.objects.acreate(
            target_playlist=watching, source_playlist=watched, tracks_added=["track1"]
        )
        self.spotify.add_tracks_to_playlist = AsyncMock()

        response = await self.async_client.post(f"/playlist/watching/updates/{update.id}/accept/")
        await self.assert_toast(response, 200, "success", "Update accepted")

        response = await self.async_client.post("/playlist/watching/accept-all-updates/")
        await self.assert_toast(response, 200, "success", "Update accepted")

    async def test_remove_tracks(self) -> None:
        """Removing tracks from a playlist or from the saved tracks is confirmed in a toast."""
        self.spotify.remove_tracks_from_playlist = AsyncMock()
        self.spotify.remove_user_saved_tracks = AsyncMock()

        response = await self.async_client.post("/playlist/playlist1/remove-tracks/", {"track-id": ["track1"]})
        await self.assert_toast(response, 200, "success", "Track(s) removed from playlist")

        response = await self.async_client.post("/remove-saved-tracks/", {"track-id": ["track1"]})
        await self.assert_toast(response, 200, "success", "Saved track(s) removed")

    async def test_events_disabled(self) -> None:
        """With events turned off the events page answers with an error toast."""
        with self.settings(EVENTS_ENABLED=False):
            response = await self.async_client.get("/events/")

        await self.assert_toast(response, 400, "error", "Events are not available yet. Please check back later")


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
        assert json.loads(response["HX-Trigger"]) == {"HXToast": {"type": "success", "body": "Duplicates removed"}}
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
        """A new name is saved at Spotify and shown in place of the form, with the button to rename it again."""
        response = await self.async_client.post("/playlist/playlist1/rename/", {"name": "New Name"})

        assert response.status_code == 200
        assert 'title="New Name">New Name</h5>' in response.content.decode()
        assert 'hx-get="/playlist/playlist1/rename/"' in response.content.decode()
        assert json.loads(response["HX-Trigger"]) == {"HXToast": {"type": "success", "body": "Playlist renamed"}}
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
        assert 'hx-get="/playlist/playlist1/rename/"' in response.content.decode()


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
    """The event modal names the artist, says when and where the event is, and links to its tickets or stream."""

    async def get_modal(self, **event_fields: object) -> lh.HtmlElement:
        artist = await Artist.objects.acreate(spotify_id="artist1")
        event_artist = await EventArtist.objects.acreate(
            artist=artist, songkick_name_match_accuracy=0, bandsintown_name_match_accuracy=0
        )
        event = await Event.objects.acreate(
            artist=event_artist,
            source="songkick",
            source_url="https://www.songkick.com/concerts/1",
            date=date(2100, 1, 1),
            **event_fields,
        )
        self.spotify.get_artist = AsyncMock(return_value=make_artist("artist1", "The Artist"))

        response = await self.async_client.get(f"/event/{event.id}/")

        assert response.status_code == 200
        self.spotify.get_artist.assert_awaited_once_with("artist1")
        return lh.fromstring(response.content)

    async def test_concert(self) -> None:
        """A concert's modal gives its venue and place, a link to each ticket seller, and a map at the venue."""
        modal = await self.get_modal(
            type=EventType.concert,
            venue="The Venue",
            city="Berlin",
            country="Germany",
            geolocation=Point(13.4, 52.5, srid=settings.GEODJANGO_SRID),
            tickets_urls=["https://s.example/a", "https://s.example/b"],
        )

        assert modal.xpath("//h2[@class='modal-title']/text()") == ["The Artist"]
        text = " ".join(modal.text_content().split())
        assert "Jan 1 The Venue Berlin, Germany Friday, January 1, 2100" in text
        links = modal.xpath("//a[@target='_blank']")
        assert [a.get("href") for a in links] == ["https://s.example/a", "https://s.example/b"]
        assert [" ".join(a.text_content().split()) for a in links] == ["Buy tickets 1", "Buy tickets 2"]
        assert modal.xpath("//input[@id='latitude']/@value") == ["52.5"]
        assert modal.xpath("//input[@id='longitude']/@value") == ["13.4"]
        assert modal.xpath("//div[@id='map']")

    async def test_concert_without_tickets_or_place(self) -> None:
        """A concert with no tickets yet says so, and one without a venue, place or location shows none of them."""
        modal = await self.get_modal(type=EventType.concert)

        text = " ".join(modal.text_content().split())
        assert "Not available yet" in text
        assert "None" not in text
        assert not modal.xpath("//a[@target='_blank']")
        assert not modal.xpath("//div[@id='map']")

    async def test_live_stream(self) -> None:
        """A live stream's modal links to the stream, with no venue, place or map."""
        modal = await self.get_modal(type=EventType.live_stream, stream_urls=["https://s.example/stream"])

        text = " ".join(modal.text_content().split())
        assert "Jan 1 Live stream Friday, January 1, 2100" in text
        assert "None" not in text
        links = modal.xpath("//a[@target='_blank']")
        assert [a.get("href") for a in links] == ["https://s.example/stream"]
        assert " ".join(links[0].text_content().split()) == "Watch"
        assert not modal.xpath("//div[@id='map']")


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
