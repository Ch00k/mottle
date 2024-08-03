import logging
from datetime import datetime
from typing import Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseServerError, QueryDict
from django.shortcuts import aget_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST, require_safe
from tekore.model import AlbumType

from .jobs import check_playlist_for_updates
from .models import Playlist, PlaylistUpdate, PlaylistWatchConfig, SpotifyAuth, SpotifyAuthRequest, SpotifyUser
from .spotify import get_auth
from .utils import MottleException, MottleSpotifyClient, list_has
from .views_utils import (
    get_artist_name,
    get_duplicates_message,
    get_playlist_data,
    get_playlist_modal_response,
    get_playlist_name,
)

require_DELETE = require_http_methods(["DELETE"])

ALBUM_SORT_ORDER = {AlbumType.album: 0, AlbumType.single: 1, AlbumType.compilation: 2}

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
async def login(request: HttpRequest) -> HttpResponse:
    redirect_uri: Optional[str]

    if request.method == "GET":
        # Django template does not undestand None (it will treat it as a literal 'None'),
        # so we need to pass an empty string
        redirect_uri = request.GET.get("next", "")

        spotify_user_id = await sync_to_async(request.session.get)("spotify_user_id")
        logger.debug(f"SpotifyAuth ID: {spotify_user_id}")

        if spotify_user_id is None:
            return render(request, "web/login.html", {"redirect_uri": redirect_uri})
        else:
            try:
                spotify_auth = await SpotifyAuth.objects.aget(spotify_user__id=spotify_user_id)
            except SpotifyAuth.DoesNotExist:
                logger.debug(f"SpotifyAuth for user ID {spotify_user_id} does not exist")
                return render(request, "web/login.html", {"redirect_uri": redirect_uri})

            logger.debug(spotify_auth)

            if spotify_auth.access_token is None:
                logger.debug(f"{spotify_auth} access_token is None")
                return render(request, "web/login.html", {"redirect_uri": redirect_uri})

            if spotify_auth.refresh_token is None:
                logger.debug(f"{spotify_auth} refresh_token is None")
                return render(request, "web/login.html", {"redirect_uri": redirect_uri})

            try:
                await spotify_auth.maybe_refresh()
            except MottleException as e:
                logger.error(e)
                return render(request, "web/login.html", {"redirect_uri": redirect_uri})

            return redirect("index")
    else:
        redirect_uri = request.POST.get("redirect_uri")

        # Convert it back from an empty string to None
        if not redirect_uri:
            redirect_uri = None

        auth = get_auth(credentials=settings.SPOTIFY_CREDEINTIALS, scope=settings.SPOTIFY_TOKEN_SCOPE)
        await SpotifyAuthRequest.objects.acreate(redirect_uri=redirect_uri, state=auth.state)
        return redirect(auth.url)


@require_GET
async def logout(request: HttpRequest) -> HttpResponse:
    await sync_to_async(request.session.flush)()
    return redirect("login")


@require_GET
async def callback(request: HttpRequest) -> HttpResponse:
    code = request.GET.get("code")
    state = request.GET.get("state")
    logger.debug(f"Received callback. Code: [REDACTED], state: {state}")

    if code is None or state is None:
        return HttpResponseBadRequest("Invalid request")

    try:
        spotify_auth_request = await SpotifyAuthRequest.objects.aget(state=state)
        logger.debug(spotify_auth_request)
    except SpotifyAuth.DoesNotExist:
        logger.debug(f"SpotifyAuthRequest with state {state} does not exist")
        return HttpResponseBadRequest("Invalid request")

    token = await spotify_auth_request.request_token(code)
    logger.debug(f"Received token, expires at {token.expires_at}")
    spotify_client = MottleSpotifyClient(access_token=token.access_token)

    try:
        user = await spotify_client.get_current_user()  # pyright: ignore
    except MottleException as e:
        logger.error(e)
        return HttpResponseServerError("Failed to get user")

    spotify_user, created = await SpotifyUser.objects.aupdate_or_create(
        spotify_id=user.id, defaults={"display_name": user.display_name, "email": user.email}
    )
    if created:
        logger.debug(f"Created new user: {spotify_user}")
    else:
        logger.debug(f"Updated existing user: {spotify_user}")

    spotify_auth = await SpotifyAuth.objects.aupdate_or_create(
        spotify_user=spotify_user,
        defaults={
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "expires_at": datetime.fromtimestamp(token.expires_at),
        },
    )
    logger.debug(spotify_auth)

    # TODO: New versions of Django should suppport async methods on sessions (aget, aset, aupdate etc.)
    request.session["spotify_user_id"] = str(spotify_user.id)
    request.session["spotify_user_spotify_id"] = spotify_user.spotify_id
    request.session["spotify_user_display_name"] = spotify_user.display_name
    request.session["spotify_user_email"] = spotify_user.email

    await spotify_auth_request.adelete()

    return redirect(spotify_auth_request.redirect_uri or "index")


@require_safe  # Accept HEAD requests from UptimeRobot
def index(request: HttpRequest) -> HttpResponse:
    return render(request, "web/index.html")


@require_GET
async def playback(request: HttpRequest) -> HttpResponse:
    playback = await request.spotify_client.get_current_user_playback()  # type: ignore[attr-defined]
    print(playback)
    # __import__("pdb").set_trace()
    return render(request, "web/playback.html")


@require_GET
async def search_artists(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("query")

    if query is None:
        return render(request, "web/search_artists.html", context={"artists": [], "query": ""})

    if query == "":
        return render(request, "web/parts/artists.html", context={"artists": [], "query": ""})

    try:
        artists = await request.spotify_client.get_artists(query)  # type: ignore[attr-defined]
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to search for artists")

    if request.htmx:  # type: ignore[attr-defined]
        return render(request, "web/parts/artists.html", context={"artists": artists, "query": query})
    else:
        return render(request, "web/search_artists.html", context={"artists": artists, "query": query})


@require_GET
async def search_playlists(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("query")

    if query is None:
        return render(request, "web/search_playlists.html", context={"playlists": [], "query": ""})

    if query == "":
        return render(request, "web/parts/playlists.html", context={"artists": [], "query": ""})

    try:
        playlists = await request.spotify_client.get_playlists(query)  # type: ignore[attr-defined]
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to search for playlists")

    if request.htmx:  # type: ignore[attr-defined]
        return render(
            request, "web/parts/playlists.html", context={"playlists": playlists, "query": query, "actions": "search"}
        )
    else:
        return render(request, "web/search_playlists.html", context={"playlists": playlists, "query": query})


@require_http_methods(["GET", "POST"])
async def albums(request: HttpRequest, artist_id: str) -> HttpResponse:
    artist_name = await get_artist_name(request, artist_id)

    try:
        all_albums = await request.spotify_client.get_artist_albums(artist_id)  # type: ignore[attr-defined]
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to get albums")

    all_albums_sorted = sorted(
        sorted(all_albums, key=lambda x: x.release_date, reverse=True), key=lambda x: ALBUM_SORT_ORDER[x.album_type]
    )
    all_album_ids = [album.id for album in all_albums_sorted]

    has_albums = list_has(all_albums_sorted, AlbumType.album)
    has_simgles = list_has(all_albums_sorted, AlbumType.single)
    has_compilations = list_has(all_albums_sorted, AlbumType.compilation)

    if request.method == "GET":
        return render(
            request,
            "web/albums.html",
            context={
                "artist": artist_name,
                "albums": all_albums_sorted,
                "album_ids": all_album_ids,
                "has_albums": has_albums,
                "has_singles": has_simgles,
                "has_compilations": has_compilations,
            },
        )
    else:
        requested_album_ids = request.POST.getlist("album")
        if not requested_album_ids:
            return HttpResponseBadRequest("No albums selected")

        name = request.POST.get("name")
        if not name:
            return HttpResponseBadRequest("No name provided")

        is_public = bool(request.POST.get("is-public", False))

        try:
            tracks = await request.spotify_client.get_tracks_in_albums(  # type: ignore[attr-defined]
                requested_album_ids
            )
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to get album tracks")

        try:
            playlist = await request.spotify_client.create_playlist_with_tracks(  # type: ignore[attr-defined]
                request.session["spotify_user_spotify_id"],
                name=f"{artist_name} discography",
                track_uris=[track.uri for track in tracks],
                is_public=is_public,
            )
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to create playlist")

        if bool(request.POST.get("auto-update", False)):
            albums_ignored = list(set(all_album_ids) - set(requested_album_ids))

            auto_accept = bool(request.POST.get("auto-accept", False))
            if auto_accept:
                logger.info(f"Setting up automatic accept for playlist {playlist.id} from artist {artist_id}")

            await Playlist.watch_artist(
                request.spotify_client,  # type: ignore[attr-defined]
                playlist.id,
                artist_id,
                albums_ignored=albums_ignored,
                auto_accept_updates=auto_accept,
            )

        return redirect("playlist", playlist_id=playlist.id)


@require_GET
async def playlists(request: HttpRequest) -> HttpResponse:
    try:
        playlists = await request.spotify_client.get_current_user_playlists()  # type: ignore[attr-defined]
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to get playlists")

    db_watching_playlists = PlaylistWatchConfig.objects.filter(
        watching_playlist__spotify_user__id=request.session["spotify_user_id"],
    ).values("watching_playlist__spotify_id")

    watching_playlists = [item["watching_playlist__spotify_id"] async for item in db_watching_playlists]

    return render(request, "web/playlists.html", {"playlists": playlists, "watching_playlists": watching_playlists})


@require_GET
async def followed_artists(request: HttpRequest) -> HttpResponse:
    try:
        artists = await request.spotify_client.get_current_user_followed_artists()  # type: ignore[attr-defined]
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to get followed artists")

    context = {"artists": artists}
    return render(request, "web/followed_artists.html", context)


@require_http_methods(["GET", "POST"])
async def playlist_updates(request: HttpRequest, playlist_id: str) -> HttpResponse:
    playlist_name = await get_playlist_name(request, playlist_id)
    playlist = await aget_object_or_404(
        Playlist, spotify_id=playlist_id, spotify_user__id=request.session["spotify_user_id"]
    )

    if request.method == "POST":
        await check_playlist_for_updates(playlist, request.spotify_client)  # type: ignore[attr-defined]

    updates = []

    async for update in playlist.pending_updates:
        watched_playlist = await sync_to_async(lambda: update.source_playlist)()
        watched_artist = await sync_to_async(lambda: update.source_artist)()

        if watched_playlist is not None:
            watched_playlist_data = await request.spotify_client.get_playlist(  # type: ignore[attr-defined]
                watched_playlist.spotify_id
            )
            watched_playlist_tracks = await request.spotify_client.get_tracks(  # type: ignore[attr-defined]
                update.tracks_added
            )
            updates.append((update.id, watched_playlist_data, watched_playlist_tracks))
        elif watched_artist is not None:
            watched_artist_data = await request.spotify_client.get_artist(  # type: ignore[attr-defined]
                watched_artist.spotify_id
            )
            tracks = await request.spotify_client.get_tracks_in_albums(  # type: ignore[attr-defined]
                update.albums_added
            )
            track_ids = [track.id for track in tracks]
            watched_artist_tracks = await request.spotify_client.get_tracks(track_ids)  # type: ignore[attr-defined]
            updates.append((update.id, watched_artist_data, watched_artist_tracks))
        else:
            # XXX: This should never happen
            logger.error(f"PlaylistUpdate {update.id} has no source_playlist or source_artist")
            continue

    if request.method == "POST":
        return render(
            request,
            "web/parts/playlist_updates.html",
            {"playlist_id": playlist_id, "playlist_name": playlist_name, "updates": updates},
        )
    else:
        return render(
            request,
            "web/playlist_updates.html",
            {"playlist_id": playlist_id, "playlist_name": playlist_name, "updates": updates},
        )


@require_POST
async def accept_playlist_update(request: HttpRequest, update_id: str) -> HttpResponse:
    update = await aget_object_or_404(
        PlaylistUpdate, id=update_id, target_playlist__spotify_user__id=request.session["spotify_user_id"]
    )

    try:
        await update.accept(request.spotify_client)  # type: ignore[attr-defined]
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to add tracks to playlist")

    return HttpResponse()


@require_http_methods(["GET", "PUT", "DELETE"])
async def playlist(request: HttpRequest, playlist_id: str) -> HttpResponse:
    playlist_name = await get_playlist_name(request, playlist_id)

    if request.method == "GET":
        try:
            playlist_items = await request.spotify_client.get_playlist_items(playlist_id)  # type: ignore[attr-defined]
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to get playlist items")

        context = {"playlist_name": playlist_name, "playlist_items": playlist_items}
        return render(request, "web/playlist.html", context)
    elif request.method == "PUT":
        # TODO: The status of playlist follow does not survive page refresh
        try:
            await request.spotify_client.follow_playlist(playlist_id)  # type: ignore[attr-defined]
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to follow playlist")

        return render(
            request,
            "web/parts/playlist_row.html",
            {"playlist": playlist, "actions": "search", "is_being_followed": True},
        )
    else:
        try:
            await request.spotify_client.unfollow_playlist(playlist_id)  # type: ignore[attr-defined]
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to unfollow playlist")
        else:
            try:
                db_playlist = await Playlist.objects.aget(spotify_id=playlist_id)
            except Playlist.DoesNotExist:
                logger.info(f"Playlist {playlist_id} not found in database. Skipping")
                pass
            else:
                await db_playlist.unfollow()

        if request.headers.get("M-Operation") == "search_unfollow":
            return render(
                request,
                "web/parts/playlist_row.html",
                {"playlist": playlist, "actions": "search", "is_being_followed": False},
            )
        else:
            return HttpResponse()


@require_http_methods(["GET", "POST"])
async def deduplicate(request: HttpRequest, playlist_id: str) -> HttpResponse:
    playlist_name, playlist_owner_id, _ = await get_playlist_data(request, playlist_id)

    if request.method == "POST":
        if playlist_owner_id != request.session["spotify_user_spotify_id"]:
            return HttpResponseBadRequest("You can only deduplicate your own playlists")

        # XXX: The whole remove_tracks_at_positions_from_playlist is not working as expected,
        # which is probably the reason it is not documented in the Spotify API.
        # The API request is successful, but the tracks' positions in request payload are ignored:
        # all specified tracks are removed from the playlist, regardless of their positions.

        # track_data = dict([item.split("::") for item in request.POST.getlist("track-meta")])
        # tracks_to_remove = [
        #     {"uri": k, "positions": [int(i) for i in v.split(",")][1:]} for k, v in track_data.items()
        # ]
        tracks_to_remove = [item.split("::")[0] for item in request.POST.getlist("track-meta")]

        logger.debug(f"Tracks: {tracks_to_remove}")
        logger.debug(f"Removing {len(tracks_to_remove)} tracks from playlist {playlist_id}")

        try:
            # await request.spotify_client.remove_tracks_at_positions_from_playlist(  # type: ignore[attr-defined]
            #     playlist_id, tracks_to_remove, playlist_snapshot_id
            # )
            await request.spotify_client.remove_tracks_from_playlist(  # type: ignore[attr-defined]
                playlist_id, tracks_to_remove
            )
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to remove tracks from playlist")

        try:
            await request.spotify_client.add_tracks_to_playlist(  # type: ignore[attr-defined]
                playlist_id, tracks_to_remove
            )
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to add tracks to playlist")

        return HttpResponse("<article><aside><h3>No duplicates found</h3></aside></article>")

    else:
        playlist_items = await request.spotify_client.find_duplicate_tracks_in_playlist(  # type: ignore[attr-defined]
            playlist_id
        )

        return render(
            request,
            "web/deduplicate.html",
            context={
                "playlist_owner_id": playlist_owner_id,
                "playlist_id": playlist_id,
                "playlist_name": playlist_name,
                "playlist_items": playlist_items,
                "message": get_duplicates_message(playlist_items),
            },
        )


@require_GET
async def playlist_audio_features(request: HttpRequest, playlist_id: str) -> HttpResponse:
    playlist_name = await get_playlist_name(request, playlist_id)

    # TODO: This view will be navigated to from the playlist view, so playlist items could could be passed from there?
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

    tracks_with_features = list(zip(tracks, tracks_features))

    return render(
        request,
        "web/playlist_audio_features.html",
        context={
            "playlist_name": playlist_name,
            "tracks_with_features": tracks_with_features,
            "use_max_width": True,
        },
    )


@require_POST
async def copy_playlist(request: HttpRequest, playlist_id: str) -> HttpResponse:
    playlist_name = await get_playlist_name(request, playlist_id)

    try:
        playlist_items = await request.spotify_client.get_playlist_items(playlist_id)  # type: ignore[attr-defined]
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to get playlist items")

    # TODO: Uploading playlist cover image is weird. Right after playlist is created, the upload returns a 404 for
    # some time, then it returns a 502 for some time, and only then it returns a 202.
    # Disabling it for now because the whole thing is very flaky.
    # try:
    #     cover_images = await request.spotify_client.get_playlist_cover_images(playlist_id)
    # except MottleException as e:
    #     logger.exception(e)
    #     cover_images = []

    # cover_images = [image for image in cover_images if urlparse(image.url).netloc != "mosaic.scdn.co"]
    # if cover_images:
    #     # The assumption is that there are either 3 mosaic images or 1 non-mosaic image
    #     cover_image = cover_images[0]
    #     resp = await httpx.AsyncClient().get(cover_image.url)
    #     cover_image_base64_data = base64.b64encode(resp.content).decode()
    #     logger.debug(f"Cover image data size: {len(cover_image_base64_data) / 1000} KB")
    # else:
    #     cover_image_base64_data = None

    try:
        playlist = await request.spotify_client.create_playlist_with_tracks(  # type: ignore[attr-defined]
            request.session["spotify_user_spotify_id"],
            name=f"Copy of {playlist_name}",
            track_uris=[track.track.uri for track in playlist_items if not track.track.is_local],
            is_public=True,  # TODO: How do we decide on the value?
            # cover_image=cover_image_base64_data,
            cover_image=None,
            add_tracks_parallelized=settings.PLAYLIST_ADD_TRACKS_PARALLELIZED,
            fail_on_cover_image_upload_error=False,
        )
    except MottleException as e:
        logger.exception(e)
        return HttpResponseServerError("Failed to create playlist")

    # When the playlist is initially created, it is empty, and its attributes (e.g. owner) are still not defined.
    # Therefore we fetch it again.
    playlist = await request.spotify_client.get_playlist(playlist.id)  # type: ignore[attr-defined]

    return render(request, "web/parts/playlist_row.html", context={"playlist": playlist})


@require_http_methods(["GET", "POST"])
async def merge_playlist(request: HttpRequest, playlist_id: str) -> HttpResponse:
    source_playlist_id = playlist_id

    if request.method == "GET":
        return await get_playlist_modal_response(request, playlist_id, "web/modals/playlist_merge.html")
    else:
        target_playlist_id = request.POST.get("merge-target")
        if not target_playlist_id:
            return HttpResponseBadRequest("No merge target provided")

        if request.POST.get("new-playlist-name"):
            target_playlist_name = request.POST.get("new-playlist-name")
            target_playlist = await request.spotify_client.create_playlist(  # type: ignore[attr-defined]
                request.session["spotify_user_spotify_id"], target_playlist_name, is_public=True
            )
            target_playlist_id = target_playlist.id

        try:
            source_playlist_items = await request.spotify_client.get_playlist_items(  # type: ignore[attr-defined]
                source_playlist_id
            )
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to get playlist items")

        try:
            await request.spotify_client.add_tracks_to_playlist(  # type: ignore[attr-defined]
                target_playlist_id, [item.track.uri for item in source_playlist_items]
            )
        except MottleException as e:
            logger.exception(e)
            return HttpResponseServerError("Failed to add items to playlist")

        auto_update = bool(request.POST.get("auto-update", False))
        if auto_update:
            logger.info(
                f"Setting up update notifications for playlist {target_playlist_id} from playlist {source_playlist_id}"
            )

            auto_accept = bool(request.POST.get("auto-accept", False))
            if auto_accept:
                logger.info(
                    f"Setting up automatic accept for playlist {target_playlist_id} from playlist {source_playlist_id}"
                )

            await Playlist.watch_playlist(
                request.spotify_client,  # type: ignore[attr-defined]
                target_playlist_id,  # type: ignore[arg-type]
                source_playlist_id,
                auto_accept_updates=auto_accept,
            )

        return HttpResponse()


@require_GET
async def configure_playlist(request: HttpRequest, playlist_id: str) -> HttpResponse:
    playlist_name = await get_playlist_name(request, playlist_id)

    db_playlist = await aget_object_or_404(
        Playlist, spotify_id=playlist_id, spotify_user__id=request.session["spotify_user_id"]
    )
    configs = PlaylistWatchConfig.objects.filter(watching_playlist=db_playlist)
    watched_playlist_settings = []
    watched_artist_settings = []

    async for config in configs:
        db_watched_playlist = await sync_to_async(lambda: config.watched_playlist)()
        db_watched_artist = await sync_to_async(lambda: config.watched_artist)()

        if db_watched_playlist is not None:
            watched_playlist = await request.spotify_client.get_playlist(  # type: ignore[attr-defined]
                db_watched_playlist.spotify_id
            )
            watched_playlist_settings.append((watched_playlist, config.auto_accept_updates))

        if db_watched_artist is not None:
            watched_artist = await request.spotify_client.get_artist(  # type: ignore[attr-defined]
                db_watched_artist.spotify_id
            )
            watched_artist_settings.append((watched_artist, config.auto_accept_updates))

    return render(
        request,
        "web/modals/playlist_configure.html",
        context={
            "playlist_id": playlist_id,
            "playlist_name": playlist_name,
            "watched_playlists": watched_playlist_settings,
            "watched_artists": watched_artist_settings,
        },
    )


@require_http_methods(["GET", "POST", "DELETE"])
async def watch_playlist(request: HttpRequest, watched_playlist_id: str) -> HttpResponse:
    if request.method == "GET":
        return await get_playlist_modal_response(request, watched_playlist_id, "web/modals/playlist_watch.html")
    elif request.method == "POST":
        watching_playlist_id = request.POST.get("watching-playlist-id")

        if not watching_playlist_id:
            return HttpResponseBadRequest("No watching playlist ID provided")

        if request.POST.get("new-playlist-name"):
            watching_playlist_name = request.POST.get("new-playlist-name")
            watching_playlist = await request.spotify_client.create_playlist(  # type: ignore[attr-defined]
                request.session["spotify_user_spotify_id"], watching_playlist_name, is_public=True
            )
            watching_playlist_id = watching_playlist.id
        else:
            await aget_object_or_404(
                Playlist, spotify_id=watching_playlist_id, spotify_user__id=request.session["spotify_user_id"]
            )

        auto_accept = bool(request.POST.get("auto-accept", False))
        if auto_accept:
            logger.info(
                f"Setting up automatic accept for playlist {watching_playlist_id} from playlist {watched_playlist_id}"
            )

        await Playlist.watch_playlist(
            request.spotify_client,  # type: ignore[attr-defined]
            watching_playlist_id,  # type: ignore[arg-type]
            watched_playlist_id,
            auto_accept_updates=auto_accept,
        )
    else:
        # https://stackoverflow.com/a/22294734
        body = QueryDict(request.body)  # pyright: ignore
        watching_playlist_id = body.get("watching-playlist-id")

        if not watching_playlist_id:
            return HttpResponseBadRequest("No watching playlist ID provided")

        watching_playlist = await aget_object_or_404(
            Playlist, spotify_id=watching_playlist_id, spotify_user__id=request.session["spotify_user_id"]
        )
        watched_playlist = await aget_object_or_404(Playlist, spotify_id=watched_playlist_id)

        await watching_playlist.unwatch(watched_playlist)

    return HttpResponse()


@require_POST
async def auto_accept_updates(request: HttpRequest, playlist_id: str) -> HttpResponse:
    watching_playlist = await aget_object_or_404(
        Playlist, spotify_id=playlist_id, spotify_user__id=request.session["spotify_user_id"]
    )

    watched_playlist_id = request.POST.get("watched-playlist-id")
    watched_artist_id = request.POST.get("watched-artist-id")

    if watched_playlist_id:
        config = await aget_object_or_404(
            PlaylistWatchConfig, watching_playlist=watching_playlist, watched_playlist__spotify_id=watched_playlist_id
        )
    elif watched_artist_id:
        config = await aget_object_or_404(
            PlaylistWatchConfig, watching_playlist=watching_playlist, watched_artist__spotify_id=watched_artist_id
        )
    else:
        return HttpResponseBadRequest("No watched playlist or artist ID provided")

    new_setting = not config.auto_accept_updates
    config.auto_accept_updates = new_setting
    await config.asave()

    return render(request, "web/icons/accept.html", {"enabled": new_setting})
