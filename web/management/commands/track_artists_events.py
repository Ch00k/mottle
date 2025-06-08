import logging
from functools import partial
from typing import Any, cast

from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand, CommandError
from tekore.model import FullPlaylistTrack

from taskrunner.tasks import task_track_artists_events
from web.data import TrackData
from web.models import SpotifyUser
from web.spotify import get_client_token
from web.utils import MottleException, MottleSpotifyClient

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Add artists to those that need to be checked for event updates"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--user-id", type=str, help="The Spotify ID of the user whose artists to track")
        parser.add_argument("--artist-id", type=str, help="The Spotify ID of the artist to track")
        parser.add_argument("--playlist-id", type=str, help="The Spotify ID of the playlist whose artists to track")
        parser.add_argument(
            "--force-reevaluate",
            action="store_true",
            default=False,
            help="Force reevaluation of artists from event sources",
        )
        parser.add_argument(
            "--concurrent-execution",
            action="store_true",
            default=False,
            help="Process all artists concurrently (useful for large playlists)",
        )

    def handle(self, *_: Any, **options: str) -> None:
        user_id: str | None = options.get("user_id")  # pyright: ignore[reportAssignmentType]
        artist_id: str | None = options.get("artist_id")  # pyright: ignore[reportAssignmentType]
        playlist_id: str | None = options.get("playlist_id")  # pyright: ignore[reportAssignmentType]
        force_reevaluate: bool = cast(bool, options.get("force_reevaluate", False))  # pyright: ignore[reportAssignmentType]
        concurrent_execution: bool = cast(bool, options.get("concurrent_execution", False))  # pyright: ignore[reportAssignmentType]

        if not user_id:
            raise CommandError("You must provide a user ID")

        if not SpotifyUser.objects.filter(pk=user_id).exists():
            raise CommandError(f"User with ID {user_id} does not exist")

        if not artist_id and not playlist_id:
            raise CommandError("You must provide either an artist ID or a playlist ID")

        if artist_id and playlist_id:
            raise CommandError("You cannot provide both an artist ID and a playlist ID")

        token = get_client_token()
        spotify_client = MottleSpotifyClient(token.access_token)

        if artist_id:
            try:
                artist = async_to_sync(partial(spotify_client.get_artist, artist_id))()
            except MottleException as e:
                raise CommandError(f"Failed to fetch artist with ID {artist_id}: {e}")
            else:
                artists = {artist.id: artist.name}
        elif playlist_id:
            try:
                playlist_tracks = async_to_sync(partial(spotify_client.get_playlist_tracks, playlist_id))()
            except MottleException as e:
                raise CommandError(f"Failed to fetch playlist with ID {playlist_id}: {e}")
            else:
                tracks = [
                    TrackData.from_tekore_model(track.track, added_at=track.added_at.date())
                    for track in playlist_tracks
                    if isinstance(track.track, FullPlaylistTrack)
                ]
                artists = {}
                for track in tracks:
                    for artist in track.artists:
                        artists[artist.id] = artist.name

        task_track_artists_events(
            artists_data=artists,
            spotify_user_id=user_id,
            force_reevaluate=force_reevaluate,
            concurrent_execution=concurrent_execution,
        )
