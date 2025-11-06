from datetime import date

import pytest
from tekore.model import (
    Followers,
    FullArtist,
    FullPlaylist,
    FullTrack,
    Image,
    PlaylistTrackPaging,
    PublicUser,
    SimpleAlbum,
    SimpleArtist,
    SimplePlaylist,
    SimpleTrack,
    Tracks,
)

from web.data import AlbumData, ArtistData, PlaylistData, SpotifyEntityData, TrackData


class TestSpotifyEntityData:
    def test_equality_based_on_id(self) -> None:
        """Test that equality is based on ID only."""
        entity1 = SpotifyEntityData(id="123", name="Name 1", url="url1")
        entity2 = SpotifyEntityData(id="123", name="Different Name", url="url2")
        entity3 = SpotifyEntityData(id="456", name="Name 1", url="url1")

        assert entity1 == entity2  # Same ID
        assert entity1 != entity3  # Different ID

    def test_hash_based_on_id(self) -> None:
        """Test that hash is based on ID only."""
        entity1 = SpotifyEntityData(id="123", name="Name 1", url="url1")
        entity2 = SpotifyEntityData(id="123", name="Different Name", url="url2")

        assert hash(entity1) == hash(entity2)

    def test_ordering_by_name_case_insensitive(self) -> None:
        """Test that entities are sorted by name (case-insensitive)."""
        entity_a = SpotifyEntityData(id="1", name="Zebra", url="url1")
        entity_b = SpotifyEntityData(id="2", name="aardvark", url="url2")
        entity_c = SpotifyEntityData(id="3", name="Monkey", url="url3")

        assert entity_b < entity_c < entity_a
        assert sorted([entity_a, entity_b, entity_c]) == [entity_b, entity_c, entity_a]

    def test_comparison_with_wrong_type_raises_error(self) -> None:
        """Test that comparing with incompatible type raises TypeError."""
        entity = SpotifyEntityData(id="123", name="Test", url="url")

        with pytest.raises(TypeError, match="Cannot compare"):
            _ = entity < "not an entity"

        with pytest.raises(TypeError, match="Cannot compare"):
            _ = entity == 123


class TestAlbumData:
    def test_from_tekore_model(self) -> None:
        """Test creating AlbumData from a Tekore SimpleAlbum."""
        album = SimpleAlbum(
            id="album123",
            href="https://api.spotify.com/v1/albums/album123",
            type="album",
            uri="spotify:album:album123",
            external_urls={"spotify": "https://open.spotify.com/album/album123"},
            name="Test Album",
            album_type="album",
            release_date="2023-05-15",
            release_date_precision="day",
            total_tracks=12,
            images=[
                Image(url="https://large.jpg", height=640, width=640),
                Image(url="https://small.jpg", height=64, width=64),
            ],
            artists=[
                SimpleArtist(
                    id="artist1",
                    href="https://api.spotify.com/v1/artists/artist1",
                    type="artist",
                    uri="spotify:artist:artist1",
                    external_urls={"spotify": "https://open.spotify.com/artist/artist1"},
                    name="Artist 1",
                )
            ],
        )

        result = AlbumData.from_tekore_model(album)

        assert result.id == "album123"
        assert result.name == "Test Album"
        assert result.type == "album"
        assert result.release_year == 2023
        assert result.num_tracks == 12
        assert "album123" in result.url
        assert result.image_url_small is not None
        assert result.image_url_large is not None
        assert result.track_image_url is not None

    def test_from_tekore_model_with_partial_release_date(self) -> None:
        """Test creating AlbumData with year-only release date."""
        album = SimpleAlbum(
            id="album456",
            href="https://api.spotify.com/v1/albums/album456",
            type="single",
            uri="spotify:album:album456",
            external_urls={"spotify": "https://open.spotify.com/album/album456"},
            name="Test Single",
            album_type="single",
            release_date="2020",
            release_date_precision="year",
            total_tracks=1,
            images=[],
            artists=[],
        )

        result = AlbumData.from_tekore_model(album)

        assert result.release_year == 2020

    def test_from_tekore_model_no_images(self) -> None:
        """Test creating AlbumData when album has no images."""
        album = SimpleAlbum(
            id="album789",
            href="https://api.spotify.com/v1/albums/album789",
            type="album",
            uri="spotify:album:album789",
            external_urls={"spotify": "https://open.spotify.com/album/album789"},
            name="No Images Album",
            album_type="album",
            release_date="2021-01-01",
            release_date_precision="day",
            total_tracks=10,
            images=[],
            artists=[],
        )

        result = AlbumData.from_tekore_model(album)

        assert result.image_url_small is not None  # Should get default image
        assert result.image_url_large is not None  # Should get default image

    @pytest.mark.asyncio
    async def test_from_metadata(self) -> None:
        """Test creating AlbumData from AlbumMetadata."""
        from typing import Any
        from unittest.mock import Mock

        async def async_property(value: Any) -> Any:
            return value

        metadata = Mock()
        metadata.id = async_property("album123")
        metadata.name = async_property("Test Album")
        metadata.url = async_property("https://open.spotify.com/album/album123")
        metadata.image_url_small = async_property("https://small.jpg")
        metadata.image_url_large = async_property("https://large.jpg")
        metadata.track_image_url = async_property("https://track.jpg")

        result = await AlbumData.from_metadata(metadata)

        assert result.id == "album123"
        assert result.name == "Test Album"
        assert result.url == "https://open.spotify.com/album/album123"
        assert result.image_url_small == "https://small.jpg"
        assert result.image_url_large == "https://large.jpg"
        assert result.track_image_url == "https://track.jpg"

    def test_ordering_inherited_from_parent(self) -> None:
        """Test that AlbumData ordering works correctly."""
        album_a = AlbumData(id="1", name="Zebra Album", url="url1")
        album_b = AlbumData(id="2", name="Alpha Album", url="url2")

        assert album_b < album_a
        assert sorted([album_a, album_b]) == [album_b, album_a]


class TestArtistData:
    def test_from_tekore_model(self) -> None:
        """Test creating ArtistData from a Tekore FullArtist."""
        artist = FullArtist(
            id="artist123",
            href="https://api.spotify.com/v1/artists/artist123",
            type="artist",
            uri="spotify:artist:artist123",
            external_urls={"spotify": "https://open.spotify.com/artist/artist123"},
            name="Test Artist",
            followers=Followers(href=None, total=1000),
            genres=["rock", "indie"],
            images=[
                Image(url="https://large.jpg", height=640, width=640),
                Image(url="https://small.jpg", height=64, width=64),
            ],
            popularity=75,
        )

        result = ArtistData.from_tekore_model(artist)

        assert result.id == "artist123"
        assert result.name == "Test Artist"
        assert result.genres == ["rock", "indie"]
        assert "artist123" in result.url
        assert result.image_url_small is not None
        assert result.image_url_large is not None

    def test_from_tekore_model_no_genres(self) -> None:
        """Test creating ArtistData when artist has no genres."""
        artist = FullArtist(
            id="artist456",
            href="https://api.spotify.com/v1/artists/artist456",
            type="artist",
            uri="spotify:artist:artist456",
            external_urls={"spotify": "https://open.spotify.com/artist/artist456"},
            name="No Genres Artist",
            followers=Followers(href=None, total=500),
            genres=[],
            images=[],
            popularity=50,
        )

        result = ArtistData.from_tekore_model(artist)

        assert result.genres == []

    @pytest.mark.asyncio
    async def test_from_metadata(self) -> None:
        """Test creating ArtistData from ArtistMetadata."""
        from typing import Any
        from unittest.mock import Mock

        async def async_property(value: Any) -> Any:
            return value

        metadata = Mock()
        metadata.id = async_property("artist123")
        metadata.name = async_property("Test Artist")
        metadata.url = async_property("https://open.spotify.com/artist/artist123")
        metadata.image_url_small = async_property("https://small.jpg")
        metadata.image_url_large = async_property("https://large.jpg")

        result = await ArtistData.from_metadata(metadata)

        assert result.id == "artist123"
        assert result.name == "Test Artist"
        assert result.url == "https://open.spotify.com/artist/artist123"
        assert result.image_url_small == "https://small.jpg"
        assert result.image_url_large == "https://large.jpg"

    def test_ordering_by_name(self) -> None:
        """Test that artists are sorted by name (case-insensitive)."""
        artist_a = ArtistData(id="1", name="Zebra", url="url1")
        artist_b = ArtistData(id="2", name="aardvark", url="url2")

        assert artist_b < artist_a
        assert sorted([artist_a, artist_b]) == [artist_b, artist_a]


class TestTrackData:
    def test_from_tekore_model_full_track(self) -> None:
        """Test creating TrackData from a Tekore FullTrack."""
        track = FullTrack(
            id="track123",
            href="https://api.spotify.com/v1/tracks/track123",
            type="track",
            uri="spotify:track:track123",
            external_urls={"spotify": "https://open.spotify.com/track/track123"},
            name="Test Track",
            duration_ms=240000,  # 4 minutes
            explicit=False,
            disc_number=1,
            track_number=1,
            is_local=False,
            popularity=80,
            external_ids={},
            preview_url=None,
            album=SimpleAlbum(
                id="album123",
                href="https://api.spotify.com/v1/albums/album123",
                type="album",
                uri="spotify:album:album123",
                external_urls={"spotify": "https://open.spotify.com/album/album123"},
                name="Test Album",
                album_type="album",
                release_date="2023-01-01",
                release_date_precision="day",
                total_tracks=10,
                images=[Image(url="https://album.jpg", height=300, width=300)],
                artists=[],
            ),
            artists=[
                SimpleArtist(
                    id="artist1",
                    href="https://api.spotify.com/v1/artists/artist1",
                    type="artist",
                    uri="spotify:artist:artist1",
                    external_urls={"spotify": "https://open.spotify.com/artist/artist1"},
                    name="Artist 1",
                ),
                SimpleArtist(
                    id="artist2",
                    href="https://api.spotify.com/v1/artists/artist2",
                    type="artist",
                    uri="spotify:artist:artist2",
                    external_urls={"spotify": "https://open.spotify.com/artist/artist2"},
                    name="Artist 2",
                ),
            ],
        )

        result = TrackData.from_tekore_model(track)

        assert result.id == "track123"
        assert result.name == "Test Track"
        assert result.duration == "4:00"
        assert result.album is not None
        assert result.album.id == "album123"
        assert result.album.name == "Test Album"
        assert len(result.artists) == 2
        assert result.artists[0].id == "artist1"
        assert result.artists[1].id == "artist2"
        assert result.added_at is None

    def test_from_tekore_model_simple_track_with_album(self) -> None:
        """Test creating TrackData from a SimpleTrack with album parameter."""
        track = SimpleTrack(
            id="track456",
            href="https://api.spotify.com/v1/tracks/track456",
            type="track",
            uri="spotify:track:track456",
            external_urls={"spotify": "https://open.spotify.com/track/track456"},
            name="Simple Track",
            duration_ms=180000,  # 3 minutes
            explicit=False,
            disc_number=1,
            track_number=2,
            is_local=False,
            artists=[
                SimpleArtist(
                    id="artist3",
                    href="https://api.spotify.com/v1/artists/artist3",
                    type="artist",
                    uri="spotify:artist:artist3",
                    external_urls={"spotify": "https://open.spotify.com/artist/artist3"},
                    name="Artist 3",
                )
            ],
        )

        album = AlbumData(
            id="album456",
            name="Provided Album",
            url="https://open.spotify.com/album/album456",
        )

        result = TrackData.from_tekore_model(track, album=album)

        assert result.id == "track456"
        assert result.name == "Simple Track"
        assert result.duration == "3:00"
        assert result.album == album
        assert len(result.artists) == 1

    def test_from_tekore_model_with_added_at(self) -> None:
        """Test creating TrackData with added_at date."""
        track = FullTrack(
            id="track789",
            href="https://api.spotify.com/v1/tracks/track789",
            type="track",
            uri="spotify:track:track789",
            external_urls={"spotify": "https://open.spotify.com/track/track789"},
            name="Added Track",
            duration_ms=210000,
            explicit=False,
            disc_number=1,
            track_number=3,
            is_local=False,
            popularity=70,
            external_ids={},
            preview_url=None,
            album=SimpleAlbum(
                id="album789",
                href="https://api.spotify.com/v1/albums/album789",
                type="album",
                uri="spotify:album:album789",
                external_urls={"spotify": "https://open.spotify.com/album/album789"},
                name="Album 789",
                album_type="album",
                release_date="2022-06-01",
                release_date_precision="day",
                total_tracks=8,
                images=[],
                artists=[],
            ),
            artists=[],
        )

        added_date = date(2023, 12, 25)
        result = TrackData.from_tekore_model(track, added_at=added_date)

        assert result.added_at == added_date

    def test_duration_formatting(self) -> None:
        """Test that duration is formatted correctly for various lengths."""
        track_short = FullTrack(
            id="track_short",
            href="https://api.spotify.com/v1/tracks/track_short",
            type="track",
            uri="spotify:track:track_short",
            external_urls={"spotify": "https://open.spotify.com/track/track_short"},
            name="Short Track",
            duration_ms=30000,  # 30 seconds
            explicit=False,
            disc_number=1,
            track_number=1,
            is_local=False,
            popularity=50,
            external_ids={},
            preview_url=None,
            album=SimpleAlbum(
                id="album",
                href="https://api.spotify.com/v1/albums/album",
                type="album",
                uri="spotify:album:album",
                external_urls={"spotify": "https://open.spotify.com/album/album"},
                name="Album",
                album_type="album",
                release_date="2020-01-01",
                release_date_precision="day",
                total_tracks=1,
                images=[],
                artists=[],
            ),
            artists=[],
        )

        result = TrackData.from_tekore_model(track_short)
        assert result.duration == "0:30"

    def test_ordering_inherited(self) -> None:
        """Test that TrackData ordering works correctly."""
        track_a = TrackData(id="1", name="Zebra Track", url="url1", duration="3:00", album=None)
        track_b = TrackData(id="2", name="Alpha Track", url="url2", duration="4:00", album=None)

        assert track_b < track_a


class TestPlaylistData:
    def test_from_tekore_model_full_playlist(self) -> None:
        """Test creating PlaylistData from a Tekore FullPlaylist."""
        playlist = FullPlaylist(
            id="playlist123",
            href="https://api.spotify.com/v1/playlists/playlist123",
            type="playlist",
            uri="spotify:playlist:playlist123",
            external_urls={"spotify": "https://open.spotify.com/playlist/playlist123"},
            name="Test Playlist",
            snapshot_id="snapshot123",
            collaborative=False,
            public=True,
            description="A test playlist",
            images=[
                Image(url="https://large.jpg", height=640, width=640),
                Image(url="https://small.jpg", height=64, width=64),
            ],
            owner=PublicUser(
                id="user123",
                href="https://api.spotify.com/v1/users/user123",
                type="user",
                uri="spotify:user:user123",
                external_urls={"spotify": "https://open.spotify.com/user/user123"},
                display_name="Test User",
                followers=Followers(href=None, total=100),
            ),
            followers=Followers(href=None, total=150),
            primary_color=None,
            tracks=PlaylistTrackPaging(
                href="https://api.spotify.com/v1/playlists/playlist123/tracks",
                items=[],
                limit=100,
                next=None,
                total=42,
                offset=0,
                previous=None,
            ),
        )

        result = PlaylistData.from_tekore_model(playlist)

        assert result.id == "playlist123"
        assert result.name == "Test Playlist"
        assert result.snapshot_id == "snapshot123"
        assert result.owner_id == "user123"
        assert result.owner_name == "Test User"
        assert result.num_tracks == 42
        assert "playlist123" in result.url
        assert result.owner_url is not None
        assert "user123" in result.owner_url
        assert result.image_url_small is not None
        assert result.image_url_large is not None
        assert len(result.tracks) == 0

    def test_from_tekore_model_simple_playlist(self) -> None:
        """Test creating PlaylistData from a Tekore SimplePlaylist."""
        playlist = SimplePlaylist(
            id="playlist456",
            href="https://api.spotify.com/v1/playlists/playlist456",
            type="playlist",
            uri="spotify:playlist:playlist456",
            external_urls={"spotify": "https://open.spotify.com/playlist/playlist456"},
            name="Simple Playlist",
            snapshot_id="snapshot456",
            collaborative=True,
            public=False,
            description=None,
            images=[],
            owner=PublicUser(
                id="user456",
                href="https://api.spotify.com/v1/users/user456",
                type="user",
                uri="spotify:user:user456",
                external_urls={"spotify": "https://open.spotify.com/user/user456"},
                display_name=None,
                followers=Followers(href=None, total=0),
            ),
            primary_color=None,
            tracks=Tracks(
                href="https://api.spotify.com/v1/playlists/playlist456/tracks",
                total=0,
            ),
        )

        result = PlaylistData.from_tekore_model(playlist)

        assert result.id == "playlist456"
        assert result.name == "Simple Playlist"
        assert result.owner_id == "user456"
        assert result.owner_name is None
        assert result.num_tracks == 0

    @pytest.mark.asyncio
    async def test_from_metadata(self) -> None:
        """Test creating PlaylistData from PlaylistMetadata."""
        from typing import Any
        from unittest.mock import Mock

        async def async_property(value: Any) -> Any:
            return value

        metadata = Mock()
        metadata.id = async_property("playlist123")
        metadata.name = async_property("Test Playlist")
        metadata.url = async_property("https://open.spotify.com/playlist/playlist123")
        metadata.image_url_small = async_property("https://small.jpg")
        metadata.image_url_large = async_property("https://large.jpg")
        metadata.owner_id = async_property("user123")
        metadata.owner_name = async_property("Test User")
        metadata.owner_url = async_property("https://open.spotify.com/user/user123")
        metadata.snapshot_id = async_property("snapshot123")
        metadata.num_tracks = async_property(42)

        result = await PlaylistData.from_metadata(metadata)

        assert result.id == "playlist123"
        assert result.name == "Test Playlist"
        assert result.url == "https://open.spotify.com/playlist/playlist123"
        assert result.image_url_small == "https://small.jpg"
        assert result.image_url_large == "https://large.jpg"
        assert result.owner_id == "user123"
        assert result.owner_name == "Test User"
        assert result.owner_url == "https://open.spotify.com/user/user123"
        assert result.snapshot_id == "snapshot123"
        assert result.num_tracks == 42

    def test_ordering_inherited(self) -> None:
        """Test that PlaylistData ordering works correctly."""
        playlist_a = PlaylistData(
            id="1",
            name="Zebra Playlist",
            url="url1",
            image_url_small=None,
            image_url_large=None,
            owner_id="owner1",
            owner_name="Owner",
            owner_url="owner_url",
            snapshot_id="snap1",
            num_tracks=10,
        )
        playlist_b = PlaylistData(
            id="2",
            name="Alpha Playlist",
            url="url2",
            image_url_small=None,
            image_url_large=None,
            owner_id="owner2",
            owner_name="Owner",
            owner_url="owner_url",
            snapshot_id="snap2",
            num_tracks=5,
        )

        assert playlist_b < playlist_a
