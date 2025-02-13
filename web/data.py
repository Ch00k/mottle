from dataclasses import dataclass, field
from datetime import date
from functools import total_ordering

from tekore.model import (
    FullArtist,
    FullPlaylist,
    FullPlaylistTrack,
    FullTrack,
    SimpleAlbum,
    SimplePlaylist,
    SimpleTrack,
)

from .templatetags.tekore_model_extras import get_largest_image, get_smallest_image, get_spotify_url, humanize_duration
from .views_utils import AlbumMetadata, ArtistMetadata, PlaylistMetadata


@total_ordering
@dataclass
class SpotifyEntityData:
    id: str
    name: str
    url: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SpotifyEntityData):
            raise TypeError(f"Cannot compare {self.__class__} with {other.__class__}")
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SpotifyEntityData):
            raise TypeError(f"Cannot compare {self.__class__} with {other.__class__}")
        return self.name.lower() < other.name.lower()


@total_ordering
@dataclass
class AlbumData(SpotifyEntityData):
    type: str | None = None
    release_year: int | None = None
    image_url_small: str | None = None
    image_url_large: str | None = None
    track_image_url: str | None = None
    num_tracks: int | None = None

    def __eq__(self, other: object) -> bool:
        return super().__eq__(other)

    def __hash__(self) -> int:
        return super().__hash__()

    def __lt__(self, other: object) -> bool:
        return super().__lt__(other)

    @staticmethod
    def from_tekore_model(album: SimpleAlbum) -> "AlbumData":
        return AlbumData(
            id=album.id,
            name=album.name,
            url=get_spotify_url(album),
            type=album.album_type,
            release_year=int(album.release_date.split("-", 1)[0]),
            image_url_small=get_smallest_image(album.images),
            image_url_large=get_largest_image(album.images),
            track_image_url=get_smallest_image(album.images),
            num_tracks=album.total_tracks,
        )

    @staticmethod
    async def from_metadata(metadata: AlbumMetadata) -> "AlbumData":
        return AlbumData(
            id=await metadata.id,
            name=await metadata.name,
            url=await metadata.url,
            image_url_small=await metadata.image_url_small,
            image_url_large=await metadata.image_url_large,
            track_image_url=await metadata.track_image_url,
        )


@total_ordering
@dataclass
class ArtistData(SpotifyEntityData):
    image_url_small: str | None = None
    image_url_large: str | None = None
    genres: list[str] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        return super().__eq__(other)

    def __hash__(self) -> int:
        return super().__hash__()

    def __lt__(self, other: object) -> bool:
        return super().__lt__(other)

    @staticmethod
    def from_tekore_model(artist: FullArtist) -> "ArtistData":
        return ArtistData(
            id=artist.id,
            name=artist.name,
            url=get_spotify_url(artist),
            image_url_small=get_smallest_image(artist.images),
            image_url_large=get_largest_image(artist.images),
            genres=artist.genres,
        )

    @staticmethod
    async def from_metadata(metadata: ArtistMetadata) -> "ArtistData":
        return ArtistData(
            id=await metadata.id,
            name=await metadata.name,
            url=await metadata.url,
            image_url_small=await metadata.image_url_small,
            image_url_large=await metadata.image_url_large,
        )


@total_ordering
@dataclass
class TrackData(SpotifyEntityData):
    duration: str
    album: AlbumData | None
    added_at: date | None = None
    artists: list[ArtistData] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        return super().__eq__(other)

    def __hash__(self) -> int:
        return super().__hash__()

    def __lt__(self, other: object) -> bool:
        return super().__lt__(other)

    @staticmethod
    def from_tekore_model(
        track: FullTrack | FullPlaylistTrack | SimpleTrack,
        album: AlbumData | None = None,
        added_at: date | None = None,
    ) -> "TrackData":
        if isinstance(track, (FullTrack, FullPlaylistTrack)):
            album_data = AlbumData(
                id=track.album.id,
                name=track.album.name,
                url=get_spotify_url(track.album),
                image_url_small=get_smallest_image(track.album.images),
                image_url_large=get_largest_image(track.album.images),
            )
        else:
            if album is not None:
                album_data = album
            else:
                album_data = None

        return TrackData(
            id=track.id,
            name=track.name,
            url=get_spotify_url(track),
            duration=humanize_duration(track.duration_ms),
            album=album_data,
            added_at=added_at,
            artists=[
                ArtistData(
                    id=artist.id,
                    name=artist.name,
                    url=get_spotify_url(artist),
                )
                for artist in track.artists
            ],
        )


@total_ordering
@dataclass
class PlaylistData(SpotifyEntityData):
    image_url_small: str | None
    image_url_large: str | None
    owner_id: str
    owner_name: str | None
    owner_url: str | None
    snapshot_id: str
    num_tracks: int
    tracks: list[TrackData] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        return super().__eq__(other)

    def __hash__(self) -> int:
        return super().__hash__()

    def __lt__(self, other: object) -> bool:
        return super().__lt__(other)

    @staticmethod
    async def from_metadata(
        metadata: PlaylistMetadata,
    ) -> "PlaylistData":
        return PlaylistData(
            id=await metadata.id,
            name=await metadata.name,
            url=await metadata.url,
            image_url_small=await metadata.image_url_small,
            image_url_large=await metadata.image_url_large,
            owner_id=await metadata.owner_id,
            owner_name=await metadata.owner_name,
            owner_url=await metadata.owner_url,
            snapshot_id=await metadata.snapshot_id,
            num_tracks=await metadata.num_tracks,
        )

    @staticmethod
    def from_tekore_model(playlist: FullPlaylist | SimplePlaylist) -> "PlaylistData":
        return PlaylistData(
            id=playlist.id,
            name=playlist.name,
            url=get_spotify_url(playlist),
            image_url_small=get_smallest_image(playlist.images),
            image_url_large=get_largest_image(playlist.images),
            owner_id=playlist.owner.id,
            owner_name=playlist.owner.display_name,
            owner_url=get_spotify_url(playlist.owner),
            snapshot_id=playlist.snapshot_id,
            num_tracks=playlist.tracks.total,
        )
