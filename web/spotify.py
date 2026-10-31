import asyncio
import logging
from typing import TYPE_CHECKING

from django.conf import settings
from httpx import AsyncClient, Timeout
from tekore import (
    AsyncSender,
    CachingSender,
    Credentials,
    Request,
    Response,
    RetryingSender,
    Spotify,
    Token,
    UserAuth,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = logging.getLogger(__name__)


class MottleRetryingSender(RetryingSender):
    def send(self, request: Request) -> Coroutine[None, None, Response]:
        """Delegate request to underlying sender and retry if failed."""
        return self._async_send(request)

    async def _async_send(self, request: Request) -> Response:
        tries = self.retries + 1
        delay_seconds = 1

        while tries > 0:
            # tekore types send as returning a response or a coroutine; the async sender returns a coroutine
            r = await self.sender.send(request)  # ty: ignore[invalid-await]

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
) -> Spotify:
    tekore_sender = AsyncSender(AsyncClient(timeout=Timeout(http_timeout)))
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
