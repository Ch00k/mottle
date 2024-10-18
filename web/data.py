from dataclasses import dataclass, field
from datetime import date
from typing import Optional

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


@dataclass
class AlbumData:
    id: str
    name: str
    url: str
    type: Optional[str] = None
    release_year: Optional[int] = None
    image_url_small: Optional[str] = None
    image_url_large: Optional[str] = None
    track_image_url: Optional[str] = None
    num_tracks: Optional[int] = None

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


@dataclass
class ArtistData:
    id: str
    name: str
    url: str
    image_url_small: Optional[str] = None
    image_url_large: Optional[str] = None
    genres: list[str] = field(default_factory=list)

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


@dataclass
class TrackData:
    id: str
    name: str
    url: str
    duration: str
    album: Optional[AlbumData]
    added_at: Optional[date] = None
    artists: list[ArtistData] = field(default_factory=list)

    @staticmethod
    def from_tekore_model(
        track: FullTrack | FullPlaylistTrack | SimpleTrack,
        album: Optional[AlbumData] = None,
        added_at: Optional[date] = None,
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


@dataclass
class PlaylistData:
    id: str
    name: str
    url: str
    image_url_small: Optional[str]
    image_url_large: Optional[str]
    owner_id: str
    owner_name: Optional[str]
    owner_url: Optional[str]
    snapshot_id: str
    num_tracks: int
    tracks: list[TrackData] = field(default_factory=list)

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
