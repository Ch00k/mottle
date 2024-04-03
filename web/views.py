import logging
from typing import Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed, HttpResponseServerError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods
from tekore import AsyncSender, RetryingSender, Spotify
from tekore.model import AlbumType

from .models import SpotifyAuth
from .spotify import get_auth
from .utils import (
    HttpRequestWithSpotifyClient,
    MottleException,
    create_playlist,
    follow_playlist,
    get_artist,
    get_artist_albums,
    get_artists,
    get_current_user_followed_artists,
    get_current_user_playlists,
    get_playlist,
    get_playlist_items,
    get_tracks_in_albums,
    list_has,
    unfollow_playlist,
)

ALBUM_SORT_ORDER = {AlbumType.album: 0, AlbumType.single: 1, AlbumType.compilation: 2}

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
async def login(request: HttpRequestWithSpotifyClient) -> HttpResponse:
    redirect_uri: Optional[str]

    if request.method == "GET":
        # Django template does not undestand None (it will treat it as a literal 'None'),
        # so we need to pass an empty string
        redirect_uri = request.GET.get("next", "")

        spotify_auth_id = await sync_to_async(request.session.get)("spotify_auth_id")
        logger.debug(f"SpotifyAuth ID: {spotify_auth_id}")

        if spotify_auth_id is None:
            return render(request, "web/login.html", {"redirect_uri": redirect_uri})
        else:
            spotify_auth = await SpotifyAuth.objects.aget(id=spotify_auth_id)
            logger.debug(spotify_auth)

            if spotify_auth.access_token is None:
                logger.debug(f"SpotifyAuth ID {spotify_auth_id} access_token is None")
                return render(request, "web/login.html", {"redirect_uri": redirect_uri})
            else:
                if spotify_auth.is_expiring:
                    logger.debug(f"SpotifyAuth ID {spotify_auth_id} is expiring. Refreshing")
                    try:
                        tekore_token = settings.SPOTIFY_CREDEINTIALS.refresh(spotify_auth.as_tekore_token)
                    except Exception as e:
                        logger.error(f"Failed to refresh token: {e}")
                        return render(request, "web/login.html", {"redirect_uri": redirect_uri})

                    await spotify_auth.update_from_tekore_token(tekore_token)
                    logger.debug(f"SpotifyAuth ID {spotify_auth_id} refreshed")
                    logger.debug(spotify_auth)

                return redirect("index")
    else:
        redirect_uri = request.POST.get("redirect_uri")

        # Convert it back from an empty string to None
        if not redirect_uri:
            redirect_uri = None

        auth = get_auth(credentials=settings.SPOTIFY_CREDEINTIALS, scope=settings.SPOTIFY_TOKEN_SCOPE)
        await SpotifyAuth.objects.acreate(redirect_uri=redirect_uri, state=auth.state)
        return redirect(auth.url)


@require_GET
async def logout(request: HttpRequestWithSpotifyClient) -> HttpResponse:
    spotify_auth_id = request.session.get("spotify_auth_id")
    if spotify_auth_id is None:
        return redirect("login")

    try:
        spotify_auth = await SpotifyAuth.objects.aget(id=spotify_auth_id)
    except SpotifyAuth.DoesNotExist:
        return redirect("login")

    await spotify_auth.adelete()
    await sync_to_async(request.session.flush)()
    return redirect("login")


@require_GET
async def callback(request: HttpRequestWithSpotifyClient) -> HttpResponse:
    code = request.GET.get("code")
    state = request.GET.get("state")
    logger.debug(f"Received callback. Code: {code}, state: {state}")

    if code is None or state is None:
        return HttpResponseBadRequest("Invalid request")

    try:
        spotify_auth = await SpotifyAuth.objects.aget(state=state)
        logger.debug(spotify_auth)
    except SpotifyAuth.DoesNotExist:
        logger.debug(f"SpotifyAuth state {state} does not exist")
        return HttpResponseBadRequest("Invalid request")

    auth = get_auth(credentials=settings.SPOTIFY_CREDEINTIALS, scope=settings.SPOTIFY_TOKEN_SCOPE, state=state)

    try:
        tekore_token = auth.request_token(code, state)
        logger.debug(f"Tekore token: {tekore_token}")
    except Exception as e:
        logger.error(f"Failed to request token: {e}")
        return HttpResponseBadRequest("Invalid request")

    await spotify_auth.update_from_tekore_token(tekore_token)  # pyright: ignore[reportArgumentType]

    logger.debug(spotify_auth)

    sender = RetryingSender(sender=AsyncSender())
    spotify_client = Spotify(token=spotify_auth.access_token, sender=sender)

    user = await spotify_client.current_user()  # pyright: ignore

    request.session["spotify_auth_id"] = str(spotify_auth.id)
    request.session["spotify_user_display_name"] = user.display_name

    return redirect(spotify_auth.redirect_uri or "index")


@require_GET
def index(request: HttpRequestWithSpotifyClient) -> HttpResponse:
    return render(request, "web/index.html")


@require_http_methods(["GET", "POST"])
async def search(request: HttpRequestWithSpotifyClient) -> HttpResponse:
    if request.method == "GET":
        return render(request, "web/search_artists.html")
    else:
        try:
            artists = await get_artists(request.spotify_client, request.POST["query"])
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to search for artists")

        return render(request, "web/tables/artists.html", context={"artists": artists})


async def albums(request: HttpRequestWithSpotifyClient, artist_id: str) -> HttpResponse:
    artist = await get_artist(request.spotify_client, artist_id)

    try:
        albums = await get_artist_albums(request.spotify_client, artist_id)
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to get albums")

    sorted_albums = sorted(
        sorted(albums, key=lambda x: x.release_date, reverse=True), key=lambda x: ALBUM_SORT_ORDER[x.album_type]
    )

    has_albums = list_has(sorted_albums, AlbumType.album)
    has_simgles = list_has(sorted_albums, AlbumType.single)
    has_compilations = list_has(sorted_albums, AlbumType.compilation)

    if request.method == "GET":
        return render(
            request,
            "web/albums.html",
            context={
                "artist": artist.name,
                "albums": sorted_albums,
                "has_albums": has_albums,
                "has_singles": has_simgles,
                "has_compilations": has_compilations,
            },
        )
    elif request.method == "POST":
        albums = request.POST.getlist("album")
        if not albums:
            return HttpResponseBadRequest("No albums selected")

        name = request.POST.get("name")
        if not name:
            return HttpResponseBadRequest("No name provided")

        is_public = bool(request.POST.get("ispublic", True))

        try:
            tracks = await get_tracks_in_albums(request.spotify_client, albums)
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to get album tracks")

        try:
            playlist = await create_playlist(
                request.spotify_client,
                name=f"{artist.name} discography",
                track_uris=[track.uri for track in tracks],
                is_public=is_public,
            )
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to create playlist")
        return redirect("playlist", playlist_id=playlist.id)
    else:
        return HttpResponseNotAllowed("Invalid request")


@require_GET
async def playlists(request: HttpRequestWithSpotifyClient) -> HttpResponse:
    try:
        playlists = await get_current_user_playlists(request.spotify_client)
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to get playlists")

    context = {"playlists": playlists}
    return render(request, "web/playlists.html", context)


@require_GET
async def followed_artists(request: HttpRequestWithSpotifyClient) -> HttpResponse:
    try:
        artists = await get_current_user_followed_artists(request.spotify_client)
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to get followed artists")

    context = {"artists": artists}
    return render(request, "web/followed_artists.html", context)


@require_http_methods(["GET", "POST", "DELETE"])
async def playlist(request: HttpRequestWithSpotifyClient, playlist_id: str) -> HttpResponse:
    playlist = await get_playlist(request.spotify_client, playlist_id)

    if request.method == "GET":
        try:
            playlist_items = await get_playlist_items(request.spotify_client, playlist_id)
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to get playlist items")

        context = {"playlist_name": playlist.name, "playlist_items": playlist_items}
        return render(request, "web/playlist.html", context)
    elif request.method == "DELETE":
        try:
            await unfollow_playlist(request.spotify_client, playlist_id)
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to unfollow playlist")

        html = f"""<a href="" hx-post="/playlist/{playlist.id}/" hx-target="this" hx-swap="outerHTML">Undo</a>"""
        return HttpResponse(html)
    else:
        try:
            await follow_playlist(request.spotify_client, playlist_id)
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to follow playlist")

        html = f"""<a href="" hx-delete="/playlist/{playlist.id}/" hx-target="this" hx-swap="outerHTML">Delete</a>"""
        return HttpResponse(html)
