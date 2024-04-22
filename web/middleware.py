import logging
from typing import Any

from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse
from httpx import AsyncClient, Timeout
from tekore import AsyncSender, RetryingSender

from web.utils import MottleSpotifyClient

from .models import SpotifyAuth
from .spotify import SpotifyClient as Spotify

logger = logging.getLogger(__name__)


class SpotifyAuthMiddleware:
    async_capable = True
    sync_capable = False

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response
        if iscoroutinefunction(self.get_response):
            markcoroutinefunction(self)

    async def __call__(self, request: HttpRequest) -> HttpResponse:
        logger.debug(f"Request path: {request.path_info}")

        if request.path_info in settings.AUTH_EXEMPT_PATHS:
            logger.debug(f"Skipping {self.__class__.__name__} middleware")
            response: HttpResponse = await self.get_response(request)
            return response

        spotify_auth_id = await sync_to_async(request.session.get)("spotify_auth_id")
        logger.debug(f"SpotifyAuth ID: {spotify_auth_id}")
        if spotify_auth_id is None:
            return redirect_to_login(request.get_full_path())

        try:
            spotify_auth = await SpotifyAuth.objects.aget(id=spotify_auth_id)
            logger.debug(spotify_auth)
        except SpotifyAuth.DoesNotExist:
            logger.debug(f"SpotifyAuth ID {spotify_auth_id} does not exist")
            return redirect_to_login(request.get_full_path())

        if spotify_auth.access_token is None:
            logger.debug(f"{spotify_auth} access_token is None")
            return redirect_to_login(request.get_full_path())

        if spotify_auth.is_expiring:
            logger.debug(f"Refreshing {spotify_auth}")
            try:
                tekore_token = settings.SPOTIFY_CREDEINTIALS.refresh(spotify_auth.as_tekore_token)
            except Exception as e:
                logger.error(f"Failed to refresh token: {e}")
                return redirect_to_login(request.get_full_path())

            await spotify_auth.update_from_tekore_token(tekore_token)
            logger.debug(f"{spotify_auth} refreshed")
            logger.debug(spotify_auth)

        client = AsyncClient(timeout=Timeout(settings.TEKORE_HTTP_TIMEOUT))
        sender = RetryingSender(retries=2, sender=AsyncSender(client))
        spotify_client = Spotify(token=spotify_auth.access_token, sender=sender, max_limits_on=True, chunked_on=True)

        request.spotify_client = MottleSpotifyClient(spotify_client=spotify_client)  # type: ignore[attr-defined]
        response = await self.get_response(request)
        return response
