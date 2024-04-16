import logging
from typing import Optional
from urllib.parse import unquote

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseServerError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_safe
from tekore import AsyncSender, RetryingSender, Spotify
from tekore.model import AlbumType

from .models import SpotifyAuth
from .spotify import get_auth
from .utils import MottleException, augment_tracks_with_audio_features, list_has

ALBUM_SORT_ORDER = {AlbumType.album: 0, AlbumType.single: 1, AlbumType.compilation: 2}

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
async def login(request: HttpRequest) -> HttpResponse:
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
async def logout(request: HttpRequest) -> HttpResponse:
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
async def callback(request: HttpRequest) -> HttpResponse:
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

    try:
        user = await spotify_client.current_user()  # pyright: ignore
    except Exception as e:
        logger.error(f"Failed to get user: {e}")
        return HttpResponseServerError("Failed to get user")

    # TODO: New versions of Django should suppport async methods on sessions (aget, aset, aupdate etc.)
    request.session["spotify_auth_id"] = str(spotify_auth.id)
    request.session["spotify_user_id"] = user.id
    request.session["spotify_user_display_name"] = user.display_name

    return redirect(spotify_auth.redirect_uri or "index")


@require_safe  # Accept HEAD requests from UptimeRobot
def index(request: HttpRequest) -> HttpResponse:
    return render(request, "web/index.html")


@require_GET
async def search(request: HttpRequest) -> HttpResponse:
    return render(request, "web/search.html")


@require_GET
async def search_artists(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("query")

    if query is None:
        return render(request, "web/search_artists.html", context={"artists": [], "query": ""})

    try:
        artists = await request.spotify_client.get_artists(query)  # type: ignore[attr-defined]
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to search for artists")

    if request.htmx:  # type: ignore[attr-defined]
        return render(request, "web/tables/artists.html", context={"artists": artists, "query": query})
    else:
        return render(request, "web/search_artists.html", context={"artists": artists, "query": query})


@require_GET
async def search_playlists(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("query")

    if query is None:
        return render(request, "web/search_playlists.html", context={"playlists": [], "query": ""})

    try:
        playlists = await request.spotify_client.get_playlists(query)  # type: ignore[attr-defined]
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to search for playlists")

    if request.htmx:  # type: ignore[attr-defined]
        return render(request, "web/tables/playlists.html", context={"playlists": playlists, "query": query})
    else:
        return render(request, "web/search_playlists.html", context={"playlists": playlists, "query": query})


@require_http_methods(["GET", "POST"])
async def albums(request: HttpRequest, artist_id: str) -> HttpResponse:
    artist_name = request.headers.get("M-ArtistName")

    if artist_name is None:
        logger.error("Artist name not found in headers")
        artist = await request.spotify_client.get_artist(artist_id)  # type: ignore[attr-defined]
        artist_name = artist.name
    else:
        artist_name = unquote(artist_name)

    try:
        albums = await request.spotify_client.get_artist_albums(artist_id)  # type: ignore[attr-defined]
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
                "artist": artist_name,
                "albums": sorted_albums,
                "has_albums": has_albums,
                "has_singles": has_simgles,
                "has_compilations": has_compilations,
            },
        )
    else:
        albums = request.POST.getlist("album")
        if not albums:
            return HttpResponseBadRequest("No albums selected")

        name = request.POST.get("name")
        if not name:
            return HttpResponseBadRequest("No name provided")

        is_public = bool(request.POST.get("ispublic", True))

        try:
            tracks = await request.spotify_client.get_tracks_in_albums(albums)  # type: ignore[attr-defined]
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to get album tracks")

        try:
            playlist = await request.spotify_client.create_playlist_with_tracks(  # type: ignore[attr-defined]
                request.session["spotify_user_id"],
                name=f"{artist_name} discography",
                track_uris=[track.uri for track in tracks],
                is_public=is_public,
            )
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to create playlist")

        return redirect("playlist", playlist_id=playlist.id)


@require_GET
async def playlists(request: HttpRequest) -> HttpResponse:
    try:
        playlists = await request.spotify_client.get_current_user_playlists()  # type: ignore[attr-defined]
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to get playlists")

    context = {"playlists": playlists}
    return render(request, "web/playlists.html", context)


@require_GET
async def followed_artists(request: HttpRequest) -> HttpResponse:
    try:
        artists = await request.spotify_client.get_current_user_followed_artists()  # type: ignore[attr-defined]
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to get followed artists")

    context = {"artists": artists}
    return render(request, "web/followed_artists.html", context)


@require_http_methods(["GET", "DELETE"])
async def playlist(request: HttpRequest, playlist_id: str) -> HttpResponse:
    playlist_name = request.headers.get("M-PlaylistName")

    if playlist_name is None:
        logger.warning("Playlist name not found in headers")
        playlist = await request.spotify_client.get_playlist(playlist_id)  # type: ignore[attr-defined]
        playlist_name = playlist.name
    else:
        playlist_name = unquote(playlist_name)

    if request.method == "GET":
        try:
            playlist_items = await request.spotify_client.get_playlist_items(playlist_id)  # type: ignore[attr-defined]
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to get playlist items")

        context = {"playlist_name": playlist_name, "playlist_items": playlist_items}
        return render(request, "web/playlist.html", context)
    else:
        try:
            await request.spotify_client.unfollow_playlist(playlist_id)  # type: ignore[attr-defined]
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to unfollow playlist")

        return HttpResponse()


@require_http_methods(["GET", "POST"])
async def deduplicate(request: HttpRequest, playlist_id: str) -> HttpResponse:
    playlist_name = request.headers.get("M-PlaylistName")
    playlist_owner_id = request.headers.get("M-PlaylistOwnerID")

    if playlist_name is None or playlist_owner_id is None:
        logger.warning("Playlist name or owner ID not found in headers")

        playlist = await request.spotify_client.get_playlist(playlist_id)  # type: ignore[attr-defined]
        playlist_name = playlist.name
        playlist_owner_id = playlist.owner.id
    else:
        playlist_name = unquote(playlist_name)
        playlist_owner_id = unquote(playlist_owner_id)

    if request.method == "POST":
        track_data = dict([item.split("::") for item in request.POST.getlist("track-meta")])
        tracks_to_remove = {k: [int(i) for i in v.split(",")][1:] for k, v in track_data.items()}

        logger.debug(f"Tracks: {tracks_to_remove}")
        logger.debug(f"Removing {len(tracks_to_remove)} tracks from playlist {playlist_id}")

        try:
            await request.spotify_client.remove_tracks_at_positions_from_playlist(  # type: ignore[attr-defined]
                playlist_id, tracks_to_remove
            )
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to remove duplicate track from playlist")
        else:
            return HttpResponse("<article><aside><h3>No duplicates found</h3></aside></article>")

    else:
        if playlist_owner_id == request.session["spotify_user_id"]:
            playlist_items = (
                await request.spotify_client.find_duplicate_tracks_in_playlist(  # type: ignore[attr-defined]
                    playlist_id
                )
            )
            num_duplicates = len(playlist_items)

            if not num_duplicates:
                message = "No duplicates found"
            elif num_duplicates == 1:
                message = "1 track has duplicates"
            else:
                if num_duplicates % 10 == 1:
                    message = f"{len(playlist_items)} track has duplicates"
                else:
                    message = f"{len(playlist_items)} tracks have duplicates"

            return render(
                request,
                "web/deduplicate.html",
                context={
                    "playlist_id": playlist_id,
                    "playlist_name": playlist_name,
                    "playlist_items": playlist_items,
                    "message": message,
                },
            )
        else:
            return render(
                request,
                "web/deduplicate.html",
                context={
                    "playlist_id": playlist_id,
                    "playlist_name": playlist_name,
                    "playlist_items": [],
                    "message": "You can only deduplicate your own playlists",
                },
            )


@require_GET
async def playlist_audio_features(request: HttpRequest, playlist_id: str) -> HttpResponse:
    # TODO: This view will be navigated to from the playlist view, so the playlist name will be available in the headers
    try:
        playlist_items = await request.spotify_client.get_playlist_items(playlist_id)  # type: ignore[attr-defined]
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to get playlist items")

    tracks = [item.track for item in playlist_items if item.track is not None and item.track.id is not None]

    track_ids = [track.id for track in tracks]
    tracks_features = await request.spotify_client.get_playlist_tracks_audio_features(  # type: ignore[attr-defined]
        track_ids
    )

    tracks_with_features = await augment_tracks_with_audio_features(tracks, tracks_features)

    return render(
        request,
        "web/playlist_audio_features.html",
        context={"tracks_with_features": tracks_with_features, "use_max_width": True},
    )
