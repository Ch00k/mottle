from dataclasses import dataclass, field
from functools import total_ordering
from typing import TYPE_CHECKING

from tekore.model import (
    FullArtist,
    FullPlaylist,
    FullPlaylistTrack,
    FullTrack,
    SimpleAlbum,
    SimplePlaylist,
    SimpleTrack,
)

from .spotify_items import get_largest_image, get_smallest_image, get_spotify_url, humanize_duration

if TYPE_CHECKING:
    from datetime import date


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


# The subclasses keep SpotifyEntityData's comparisons, which a generated __eq__ would replace
@dataclass(eq=False)
class AlbumData(SpotifyEntityData):
    type: str | None = None
    release_year: int | None = None
    image_url_small: str | None = None
    image_url_large: str | None = None
    track_image_url: str | None = None
    num_tracks: int | None = None

    @staticmethod
    def from_tekore_model(album: SimpleAlbum) -> AlbumData:
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


@dataclass(eq=False)
class ArtistData(SpotifyEntityData):
    image_url_small: str | None = None
    image_url_large: str | None = None
    genres: list[str] = field(default_factory=list)

    @staticmethod
    def from_tekore_model(artist: FullArtist) -> ArtistData:
        return ArtistData(
            id=artist.id,
            name=artist.name,
            url=get_spotify_url(artist),
            image_url_small=get_smallest_image(artist.images),
            image_url_large=get_largest_image(artist.images),
            genres=artist.genres,
        )


@dataclass(eq=False)
class TrackData(SpotifyEntityData):
    duration: str
    album: AlbumData | None
    added_at: date | None = None
    artists: list[ArtistData] = field(default_factory=list)

    @staticmethod
    def from_tekore_model(
        track: FullTrack | FullPlaylistTrack | SimpleTrack,
        album: AlbumData | None = None,
        added_at: date | None = None,
    ) -> TrackData:
        album_data: AlbumData | None

        if isinstance(track, (FullTrack, FullPlaylistTrack)):
            album_data = AlbumData(
                id=track.album.id,
                name=track.album.name,
                url=get_spotify_url(track.album),
                image_url_small=get_smallest_image(track.album.images),
                image_url_large=get_largest_image(track.album.images),
            )
        else:
            album_data = album

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


@dataclass(eq=False)
class PlaylistData(SpotifyEntityData):
    image_url_small: str | None
    image_url_large: str | None
    owner_id: str
    owner_name: str | None
    owner_url: str | None
    snapshot_id: str
    num_tracks: int
    tracks: list[TrackData] = field(default_factory=list)

    @staticmethod
    def from_tekore_model(playlist: FullPlaylist | SimplePlaylist) -> PlaylistData:
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
