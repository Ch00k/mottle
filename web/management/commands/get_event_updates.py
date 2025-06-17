import logging
from functools import partial
from typing import Any, cast

from asgiref.sync import async_to_sync
from django.core.management import CommandError
from django.core.management.base import BaseCommand
from tekore.model import FullPlaylistTrack

from featureflags.data import FeatureFlag
from taskrunner.tasks import get_event_updates
from web.data import TrackData
from web.spotify import get_client_token
from web.utils import MottleException, MottleSpotifyClient

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Check for event updates"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--playlist-id",
            type=str,
            help="The Spotify ID of the playlist whose artists to check",
        )
        parser.add_argument(
            "--force-refetch",
            action="store_true",
            default=False,
            help="Force refetching events from event sources",
        )
        parser.add_argument(
            "--concurrency-limit",
            type=int,
            default=FeatureFlag.event_fetching_concurrency_limit(),
            help="Limit the number of concurrent executions for event fetching (how many artists to check at once)",
        )
        parser.add_argument(
            "--compile-notifications",
            action="store_true",
            default=False,
            help="Compile notifications for event updates",
        )

    def handle(self, *_: Any, **options: str) -> None:
        playlist_id: str | None = options.get("playlist_id")
        force_refetch: bool = cast("bool", options.get("force_refetch", False))
        concurrency_limit: int = cast(
            "int", options.get("concurrency_limit", FeatureFlag.event_fetching_concurrency_limit())
        )
        compile_notifications: bool = cast("bool", options.get("compile_notifications", False))

        if playlist_id:
            token = get_client_token()
            spotify_client = MottleSpotifyClient(token.access_token)

            try:
                playlist_tracks = async_to_sync(partial(spotify_client.get_playlist_tracks, playlist_id))()
            except MottleException as e:
                raise CommandError(f"Failed to fetch playlist with ID {playlist_id}: {e}") from e
            else:
                tracks = [
                    TrackData.from_tekore_model(track.track, added_at=track.added_at.date())
                    for track in playlist_tracks
                    if isinstance(track.track, FullPlaylistTrack)
                ]
                artist_ids = list({artist.id for track in tracks for artist in track.artists})
        else:
            artist_ids = None

        get_event_updates(
            artist_spotify_ids=artist_ids,
            compile_notifications=compile_notifications,
            send_notifications=False,
            force_refetch=force_refetch,
            concurrent_execution=True,
            concurrency_limit=concurrency_limit,
        )
