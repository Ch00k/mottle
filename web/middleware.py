import logging
from typing import Awaitable, Callable

from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse

from .models import SpotifyAuth
from .utils import MottleException, MottleSpotifyClient

logger = logging.getLogger(__name__)


class SpotifyAuthMiddleware:
    async_capable = True
    sync_capable = False

    def __init__(self, get_response: Callable[..., Awaitable[HttpResponse]]) -> None:
        self.get_response = get_response
        if iscoroutinefunction(self.get_response):
            markcoroutinefunction(self)

    async def __call__(self, request: HttpRequest) -> HttpResponse:
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

        try:
            await spotify_auth.maybe_refresh()
        except MottleException as e:
            logger.error(e)
            return redirect_to_login(request.get_full_path())

        request.spotify_client = MottleSpotifyClient(spotify_auth.access_token)  # type: ignore[attr-defined]
        response = await self.get_response(request)
        return response
