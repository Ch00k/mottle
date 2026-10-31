import inspect
import logging
from typing import TYPE_CHECKING

import sentry_sdk
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse, HttpResponseServerError

from .models import SpotifyAuth
from .utils import MottleException, MottleSpotifyClient
from .views_utils import toast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from django.contrib.sessions.backends.base import SessionBase
    from django_hosts import host
    from django_htmx.middleware import HtmxDetails

logger = logging.getLogger(__name__)


async def get_signed_in_auth(session: SessionBase) -> SpotifyAuth | None:
    """Return the Spotify authorization of the session's user, refreshed if it is about to expire.

    Return None, signing the user out, when the session names no user with an authorization, or the authorization has
    no refresh token, is for another scope than the app asks for, or fails to refresh.
    """
    spotify_user_id = await sync_to_async(session.get)("spotify_user_id")
    logger.debug(f"SpotifyUser ID: {spotify_user_id}")
    if spotify_user_id is None:
        return None

    try:
        spotify_auth = await SpotifyAuth.objects.aget(spotify_user__id=spotify_user_id)
    except SpotifyAuth.DoesNotExist:
        logger.debug(f"SpotifyAuth for spotify user ID {spotify_user_id} does not exist")
        return None

    logger.debug(spotify_auth)

    if spotify_auth.refresh_token is None:
        logger.debug(f"{spotify_auth} refresh_token is None")
        return None

    if set(spotify_auth.token_scope) != set(settings.SPOTIFY_TOKEN_SCOPE):
        logger.debug(f"{spotify_auth} token scope {spotify_auth.token_scope} is not {settings.SPOTIFY_TOKEN_SCOPE}")
        return None

    try:
        await spotify_auth.maybe_refresh()
    except MottleException as e:
        logger.error(e)
        return None

    return spotify_auth


class MottleHttpRequest(HttpRequest):
    host: host
    spotify_client: MottleSpotifyClient
    htmx: HtmxDetails


class SpotifyAuthMiddleware:
    async_capable = True
    sync_capable = False

    def __init__(self, get_response: Callable[..., Awaitable[HttpResponse]]) -> None:
        self.get_response = get_response
        if inspect.iscoroutinefunction(self.get_response):
            inspect.markcoroutinefunction(self)

    async def __call__(self, request: MottleHttpRequest) -> HttpResponse:
        if request.host.name == "urlshortener":
            logger.debug(f"Request host: {request.host.name}. Skipping {self.__class__.__name__} middleware")
            return await self.get_response(request)

        logger.debug(f"Request path: {request.path_info}")

        if request.path_info in settings.AUTH_EXEMPT_PATHS:
            logger.debug(f"Skipping {self.__class__.__name__} middleware")
            return await self.get_response(request)

        spotify_auth = await get_signed_in_auth(request.session)
        if spotify_auth is None:
            return redirect_to_login(request.get_full_path())

        request.spotify_client = MottleSpotifyClient(spotify_auth.access_token)
        return await self.get_response(request)


class MottleExceptionMiddleware:
    """Answers a MottleException a view raises with a 500 that shows the error to the user as a toast."""

    async_capable = True
    sync_capable = False

    def __init__(self, get_response: Callable[..., Awaitable[HttpResponse]]) -> None:
        self.get_response = get_response
        if inspect.iscoroutinefunction(self.get_response):
            inspect.markcoroutinefunction(self)

    async def __call__(self, request: HttpRequest) -> HttpResponse:
        return await self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:  # noqa: ARG002
        if not isinstance(exception, MottleException):
            return None

        logger.error(exception, exc_info=exception)
        sentry_sdk.capture_exception(exception)

        return toast(HttpResponseServerError(), "error", f"Error: {exception}")
