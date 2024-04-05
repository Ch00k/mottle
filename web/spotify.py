from typing import Optional

from tekore import Credentials, Spotify, UserAuth, scope
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
    @send_and_process(top_item("snapshot_id"))
    def playlist_remove_occurrences(
        self, playlist_id: str, refs: dict[str, list[int]], snapshot_id: Optional[str] = None
    ) -> str:
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
        tracks = [{"uri": uri, "positions": indices} for uri, indices in refs.items()]
        return self._generic_playlist_remove(  # type: ignore[no-any-return]
            playlist_id, {"tracks": tracks}, snapshot_id  # pyright: ignore
        )
