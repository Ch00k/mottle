from typing import Optional

from tekore import Credentials, Spotify, UserAuth, scope
from tekore._client.chunked import chunked, return_last
from tekore._client.decor import scopes, send_and_process
from tekore._client.process import top_item


def get_auth(credentials: Credentials, scope: str, state: Optional[str] = None) -> UserAuth:
    auth = UserAuth(cred=credentials, scope=scope)

    if state is not None:
        auth.state = state

    return auth


# https://github.com/felix-hilden/tekore/issues/321
class SpotifyClient(Spotify):
    @scopes([scope.playlist_modify_public], [scope.playlist_modify_private])
    @chunked("refs", 2, 100, return_last)
    @send_and_process(top_item("snapshot_id"))
    def playlist_remove_occurrences(self, playlist_id: str, refs: list[dict], snapshot_id: str) -> str:
        """
        Remove items by URI and position.

        Parameters
        ----------
        playlist_id
            playlist ID
        refs
            a list of tuples containing the URI and index of items to remove
        snapshot_id
            snapshot ID for the playlist

        Returns
        -------
        str
            snapshot ID for the playlist
        """
        return self._generic_playlist_remove(  # type: ignore[no-any-return]
            playlist_id, {"tracks": refs}, snapshot_id  # pyright: ignore
        )
