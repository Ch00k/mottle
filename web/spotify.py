from typing import Optional

from django.conf import settings
from httpx import AsyncClient, Client, Timeout
from tekore import AsyncSender, Credentials, RetryingSender, Spotify, SyncSender, Token, UserAuth, scope
from tekore._client.chunked import chunked, return_last
from tekore._client.decor import scopes, send_and_process
from tekore._client.process import top_item


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


def get_auth(credentials: Credentials, scope: str, state: Optional[str] = None) -> UserAuth:
    auth = UserAuth(cred=credentials, scope=scope)

    if state is not None:
        auth.state = state

    return auth


def get_client(
    access_token: str,
    http_timeout: int,
    retries: int = 2,
    max_limits_on: bool = True,
    chunked_on: bool = True,
    async_on: bool = True,
) -> SpotifyClient:
    if async_on:
        httpx_client_class = AsyncClient
        tekore_sender_class = AsyncSender
    else:
        httpx_client_class = Client
        tekore_sender_class = SyncSender

    httpx_client = httpx_client_class(timeout=Timeout(http_timeout))
    sender = RetryingSender(retries=retries, sender=tekore_sender_class(httpx_client))
    return SpotifyClient(token=access_token, sender=sender, max_limits_on=max_limits_on, chunked_on=chunked_on)


def authenticate(code: str, state: str) -> Token:
    auth = get_auth(credentials=settings.SPOTIFY_CREDEINTIALS, scope=settings.SPOTIFY_TOKEN_SCOPE, state=state)
    return auth.request_token(code, state)  # pyright: ignore


def get_client_token() -> Token:
    return settings.SPOTIFY_CREDEINTIALS.request_client_token()
