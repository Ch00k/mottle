import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

from django.conf import settings
from httpx import AsyncClient, Client, Timeout
from tekore import (
    AsyncSender,
    CachingSender,
    Credentials,
    Request,
    Response,
    RetryingSender,
    Spotify,
    SyncSender,
    Token,
    UserAuth,
)

from .metrics import SPOTIFY_API_RESPONSE_TIME_SECONDS, SPOTIFY_API_RESPONSES

if TYPE_CHECKING:
    from collections.abc import Coroutine

SPOTIFY_ID_PATTERN = r"[a-zA-Z0-9]{22}"
SPOTIFY_ID_PLACEHOLDER = "<sid>"

logger = logging.getLogger(__name__)


# https://github.com/felix-hilden/tekore/issues/321
# class SpotifyClient(Spotify):
#     @scopes([scope.playlist_modify_public], [scope.playlist_modify_private])
#     @chunked("refs", 2, 100, return_last)
#     @send_and_process(top_item("snapshot_id"))
#     def playlist_remove_occurrences(self, playlist_id: str, refs: list[dict], snapshot_id: str) -> str:
#         """
#         Remove items by URI and position.

#         Parameters
#         ----------
#         playlist_id
#             playlist ID
#         refs
#             a list of tuples containing the URI and index of items to remove
#         snapshot_id
#             snapshot ID for the playlist

#         Returns
#         -------
#         str
#             snapshot ID for the playlist
#         """
#         return self._generic_playlist_remove(playlist_id, {"tracks": refs}, snapshot_id)


class MottleRetryingSender(RetryingSender):
    def send(self, request: Request) -> Response | Coroutine[None, None, Response]:
        """Delegate request to underlying sender and retry if failed."""
        if self.is_async:
            return self._async_send(request)

        tries = self.retries + 1
        delay_seconds = 1

        while tries > 0:
            with SPOTIFY_API_RESPONSE_TIME_SECONDS.time():
                r = self.sender.send(request)

            # A synchronous sender returns the response itself
            assert isinstance(r, Response)

            if r.status_code >= 400:
                metric_url = re.sub(SPOTIFY_ID_PATTERN, SPOTIFY_ID_PLACEHOLDER, request.url)
                SPOTIFY_API_RESPONSES.labels(request.method, metric_url, r.status_code).inc()

            if r.status_code == 401 and tries > 1:
                logger.warning(f"Retrying request {request.method} {request.url} due to 401")
                tries -= 1
                time.sleep(delay_seconds)
                delay_seconds *= 2
            elif r.status_code == 429:
                logger.warning(f"Retrying request {request.method} {request.url} due to 429")
                seconds = r.headers.get("Retry-After", 1)
                time.sleep(int(seconds) + 1)
            elif r.status_code >= 500 and tries > 1:
                logger.warning(f"Retrying request {request.method} {request.url} due to {r.status_code}")
                tries -= 1
                time.sleep(delay_seconds)
                delay_seconds *= 2
            else:
                return r

        raise RuntimeError("Retry loop exited without returning a response")

    async def _async_send(self, request: Request) -> Response:
        tries = self.retries + 1
        delay_seconds = 1

        while tries > 0:
            with SPOTIFY_API_RESPONSE_TIME_SECONDS.time():
                # tekore types send as returning a response or a coroutine; the async sender returns a coroutine
                r = await self.sender.send(request)  # ty: ignore[invalid-await]

            if r.status_code >= 400:
                metric_url = re.sub(SPOTIFY_ID_PATTERN, SPOTIFY_ID_PLACEHOLDER, request.url)
                SPOTIFY_API_RESPONSES.labels(request.method, metric_url, r.status_code).inc()

            if r.status_code == 401 and tries > 1:
                logger.warning(f"Retrying request {request.method} {request.url} due to 401")
                tries -= 1
                await asyncio.sleep(delay_seconds)
                delay_seconds *= 2
            elif r.status_code == 429:
                logger.warning(f"Retrying request {request.method} {request.url} due to 429")
                seconds = r.headers.get("Retry-After", 1)
                await asyncio.sleep(int(seconds) + 1)
            elif r.status_code >= 500 and tries > 1:
                logger.warning(f"Retrying request {request.method} {request.url} due to {r.status_code}")
                tries -= 1
                await asyncio.sleep(delay_seconds)
                delay_seconds *= 2
            else:
                return r

        raise RuntimeError("Retry loop exited without returning a response")


def get_auth(credentials: Credentials, scope: list[str], state: str | None = None) -> UserAuth:
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
) -> Spotify:
    tekore_sender: SyncSender | AsyncSender
    if async_on:
        tekore_sender = AsyncSender(AsyncClient(timeout=Timeout(http_timeout)))
    else:
        tekore_sender = SyncSender(Client(timeout=Timeout(http_timeout)))

    sender = CachingSender(sender=MottleRetryingSender(retries=retries, sender=tekore_sender))
    return Spotify(token=access_token, sender=sender, max_limits_on=max_limits_on, chunked_on=chunked_on)


def authenticate(code: str, state: str) -> Token:
    auth = get_auth(credentials=settings.SPOTIFY_CREDEINTIALS, scope=settings.SPOTIFY_TOKEN_SCOPE, state=state)
    token = auth.request_token(code, state)

    # Plain Credentials, as opposed to RefreshingCredentials, issue a plain Token
    assert isinstance(token, Token)
    return token


def get_client_token() -> Token:
    return settings.SPOTIFY_CREDEINTIALS.request_client_token()
