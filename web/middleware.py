import logging
from typing import Awaitable, Callable

from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse
from django_htmx.middleware import HtmxDetails

from .models import SpotifyAuth
from .utils import MottleException, MottleSpotifyClient

logger = logging.getLogger(__name__)


async def get_token_scope_changes(spotify_auth: SpotifyAuth) -> tuple[list, list]:
    common = set(spotify_auth.token_scope) & set(settings.SPOTIFY_TOKEN_SCOPE)
    missing = set(settings.SPOTIFY_TOKEN_SCOPE) - common
    redundant = set(spotify_auth.token_scope) - common
    return list(missing), list(redundant)


class MottleHttpRequest(HttpRequest):
    spotify_client: MottleSpotifyClient
    htmx: HtmxDetails


class SpotifyAuthMiddleware:
    async_capable = True
    sync_capable = False

    def __init__(self, get_response: Callable[..., Awaitable[HttpResponse]]) -> None:
        self.get_response = get_response
        if iscoroutinefunction(self.get_response):
            markcoroutinefunction(self)

    async def __call__(self, request: MottleHttpRequest) -> HttpResponse:
        logger.debug(f"Request path: {request.path_info}")

        if request.path_info in settings.AUTH_EXEMPT_PATHS:
            logger.debug(f"Skipping {self.__class__.__name__} middleware")
            return await self.get_response(request)

        spotify_user_id = await sync_to_async(request.session.get)("spotify_user_id")
        logger.debug(f"SpotifyUser ID: {spotify_user_id}")
        if spotify_user_id is None:
            return redirect_to_login(request.get_full_path())

        try:
            spotify_auth = await SpotifyAuth.objects.aget(spotify_user__id=spotify_user_id)
            logger.debug(spotify_auth)
        except SpotifyAuth.DoesNotExist:
            logger.debug(f"SpotifyAuth for spotify user ID {spotify_user_id} does not exist")
            return redirect_to_login(request.get_full_path())

        if spotify_auth.access_token is None:
            logger.debug(f"{spotify_auth} access_token is None")
            return redirect_to_login(request.get_full_path())

        if spotify_auth.refresh_token is None:
            logger.debug(f"{spotify_auth} refresh_token is None")
            return redirect_to_login(request.get_full_path())

        token_permissions_added, token_permissions_removed = await get_token_scope_changes(spotify_auth)
        if token_permissions_added or token_permissions_removed:
            logger.debug(
                f"{spotify_auth} token scope is outdated: "
                f"permissions missing {token_permissions_added}, permissions redundant {token_permissions_removed}"
            )
            return redirect_to_login(request.get_full_path())

        try:
            await spotify_auth.maybe_refresh()
        except MottleException as e:
            logger.error(e)
            return redirect_to_login(request.get_full_path())

        request.spotify_client = MottleSpotifyClient(spotify_auth.access_token)
        response = await self.get_response(request)
        return response
