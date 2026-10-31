import pytest
from django.core.management import CommandError, call_command

from web.models import SpotifyUser

pytestmark = pytest.mark.django_db


class TestGetPlaylistUpdates:
    def test_user_without_playlists(self) -> None:
        """Checking a user who has no playlists stops with an error instead of checking nothing."""
        SpotifyUser.objects.create(spotify_id="user_without_playlists")

        with pytest.raises(CommandError, match=r"^User has no playlists$"):
            call_command("get_playlist_updates", user_id="user_without_playlists")

    def test_unknown_user(self) -> None:
        """Checking a user mottle does not know stops with an error."""
        with pytest.raises(CommandError, match=r"^User does not exist$"):
            call_command("get_playlist_updates", user_id="unknown_user")
