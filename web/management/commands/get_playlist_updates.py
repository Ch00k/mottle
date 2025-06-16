import logging
from functools import partial
from typing import Any

from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand, CommandError

from web.models import Playlist, SpotifyUser
from web.spotify import get_client_token
from web.tasks import acheck_playlists_for_updates, check_playlist_for_updates, check_user_playlists_for_updates
from web.utils import MottleSpotifyClient

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Check playlists for updates"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--user-id", type=str, help="The ID of the user to check playlists for")
        parser.add_argument("--playlist-id", type=str, help="The ID of the plylist to check")
        parser.add_argument("--send-notifications", action="store_true", help="Send notifications for updates")

    def handle(self, *_: tuple, **options: dict) -> None:
        user_id = options.get("user_id")
        playlist_id = options.get("playlist_id")
        send_notifications = bool(options.get("send_notifications", False))

        token = get_client_token()
        spotify_client = MottleSpotifyClient(token.access_token)

        if user_id:
            try:
                user = SpotifyUser.objects.get(spotify_id=user_id)
            except SpotifyUser.DoesNotExist:
                raise CommandError("User does not exist")
            if user.playlists is None:  # pyright: ignore
                raise CommandError("User has no playlists")

            async_to_sync(partial(check_user_playlists_for_updates, user, send_notifications))()  # pyright: ignore
        elif playlist_id:
            try:
                playlist = Playlist.objects.get(id=playlist_id)  # type: ignore
            except Playlist.DoesNotExist:
                raise CommandError("Playlist does not exist")

            async_to_sync(partial(check_playlist_for_updates, playlist, spotify_client))()
        else:
            async_to_sync(partial(acheck_playlists_for_updates, send_notifications))()  # pyright: ignore
