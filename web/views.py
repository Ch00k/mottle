import logging
from datetime import UTC, datetime
from urllib.parse import unquote

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.gis.geos import Point
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseServerError,
    QueryDict,
)
from django.shortcuts import aget_object_or_404, redirect, render
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
    require_safe,
)
from django_htmx.http import HttpResponseClientRedirect, push_url, trigger_client_event
from tekore.model import AlbumType, FullPlaylistTrack

from taskrunner.tasks import task_track_artists_events, task_upload_cover_image

from .data import AlbumData, ArtistData, PlaylistData, TrackData
from .jobs import check_playlist_for_updates
from .middleware import MottleHttpRequest, get_token_scope_changes
from .models import (
    Event,
    Playlist,
    PlaylistUpdate,
    PlaylistWatchConfig,
    SpotifyAuth,
    SpotifyAuthRequest,
    SpotifyUser,
)
from .spotify import get_auth
from .tasks import track_artist_events
from .utils import MottleException, MottleSpotifyClient, list_has
from .views_utils import (
    AlbumMetadata,
    ArtistMetadata,
    PlaylistMetadata,
    catch_errors,
    get_duplicates_message,
    get_playlist_modal_response,
)

ALBUM_SORT_ORDER = {AlbumType.album: 0, AlbumType.single: 1, AlbumType.compilation: 2}

logger = logging.getLogger(__name__)


@catch_errors
@require_http_methods(["GET", "POST"])
async def login(request: MottleHttpRequest) -> HttpResponse:
    redirect_uri: str | None

    if request.method == "GET":
        # Django template does not undestand None (it will treat it as a literal 'None'),
        # so we need to pass an empty string
        redirect_uri = request.GET.get("next", "")

        spotify_user_id = await sync_to_async(request.session.get)("spotify_user_id")
        logger.debug(f"SpotifyAuth ID: {spotify_user_id}")

        if spotify_user_id is None:
            return render(request, "web/login.html", {"redirect_uri": redirect_uri})
        else:
            # TODO: This duplicates what already exists in `SpotifyAuthMiddleware.__call__`
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

            (
                token_permissions_added,
                token_permissions_removed,
            ) = await get_token_scope_changes(spotify_auth)
            if token_permissions_added or token_permissions_removed:
                logger.debug(
                    f"{spotify_auth} token scope is outdated: "
                    f"permissions missing {token_permissions_added}, permissions redundant {token_permissions_removed}"
                )
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

        auth = get_auth(
            credentials=settings.SPOTIFY_CREDEINTIALS,
            scope=settings.SPOTIFY_TOKEN_SCOPE,
        )
        await SpotifyAuthRequest.objects.acreate(redirect_uri=redirect_uri, state=auth.state)
        return redirect(auth.url)


@catch_errors
@require_GET
async def logout(request: MottleHttpRequest) -> HttpResponse:
    await sync_to_async(request.session.flush)()
    return redirect("login")


@catch_errors
@require_GET
async def callback(request: MottleHttpRequest) -> HttpResponse:
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

    # client_ip, is_routable = get_client_ip(request)
    # logger.debug(f"User's IP address: {client_ip}, is routable: {is_routable}")

    # user_location = None
    # if client_ip is not None and is_routable:
    #     try:
    #         user_location = await get_ip_location(client_ip)
    #     except GeolocationError as e:
    #         logger.error(e)

    # logger.debug(f"User's location: {user_location}")

    spotify_user, created = await SpotifyUser.objects.aupdate_or_create(
        spotify_id=user.id,
        defaults={"display_name": user.display_name, "email": user.email},
    )
    if created:
        logger.debug(f"Created new user: {spotify_user}")
    else:
        logger.debug(f"Updated existing user: {spotify_user}")

    spotify_auth, _ = await SpotifyAuth.objects.aupdate_or_create(
        spotify_user=spotify_user,
        defaults={
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "expires_at": datetime.fromtimestamp(token.expires_at, tz=UTC),
            "token_scope": list(token.scope),
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


@catch_errors
@require_safe  # Accept HEAD requests from UptimeRobot
def index(request: MottleHttpRequest) -> HttpResponse:
    return render(request, "web/index.html")


@catch_errors
def changelog(request: MottleHttpRequest) -> HttpResponse:
    return render(request, "web/changelog.html")


@catch_errors
@require_GET
async def search_artists(request: MottleHttpRequest) -> HttpResponse:
    query = request.GET.get("query")

    if not query:
        return render(request, "web/search_artists.html", context={"artists": [], "query": ""})

    artists = await request.spotify_client.find_artists(query)
    artists = [ArtistData.from_tekore_model(artist) for artist in artists]

    if request.htmx:
        events_enabled = request.session["spotify_user_spotify_id"] in settings.EVENTS_ENABLED_FOR_SPOTIFY_USER_IDS
        return push_url(
            render(
                request,
                "web/parts/artists.html",
                context={"artists": artists, "query": query, "events_enabled": events_enabled},
            ),
            "?query=" + unquote(query),
        )
    else:
        return render(
            request,
            "web/search_artists.html",
            context={"artists": artists, "query": query},
        )


@catch_errors
@require_GET
async def search_playlists(request: MottleHttpRequest) -> HttpResponse:
    query = request.GET.get("query")

    if not query:
        return render(request, "web/search_playlists.html", context={"playlists": [], "query": ""})

    playlists = await request.spotify_client.find_playlists(query)

    # TODO: There should be another way to check if a playlist belongs to the current user
    user_playlists = await request.spotify_client.get_current_user_playlists()
    user_playlist_ids = [playlist.id for playlist in user_playlists]

    # https://community.spotify.com/t5/Spotify-for-Developers/null-values-when-using-quot-Get-Current-User-s-Playlists-quot/td-p/6549968
    playlists = [PlaylistData.from_tekore_model(playlist) for playlist in playlists if playlist is not None]

    # Skip own playlists
    playlists = [p for p in playlists if p.owner_id != request.session["spotify_user_spotify_id"]]

    if request.htmx:
        return push_url(
            render(
                request,
                "web/parts/playlists.html",
                context={
                    "playlists": playlists,
                    "user_playlist_ids": user_playlist_ids,
                    "query": query,
                    "source": "playlists_search",
                },
            ),
            "?query=" + unquote(query),
        )
    else:
        return render(
            request,
            "web/search_playlists.html",
            context={
                "playlists": playlists,
                "user_playlist_ids": user_playlist_ids,
                "query": query,
                "source": "playlists_search",
            },
        )


@catch_errors
@require_http_methods(["GET", "POST"])
async def albums(request: MottleHttpRequest, artist_id: str) -> HttpResponse:
    artist_metadata = ArtistMetadata(request, artist_id)
    artist = await ArtistData.from_metadata(artist_metadata)

    all_albums = await request.spotify_client.get_artist_albums_separately_by_type(artist_id)
    albums_sorted = sorted(
        sorted(all_albums, key=lambda x: x.release_date, reverse=True),
        key=lambda x: ALBUM_SORT_ORDER[x.album_type],
    )
    albums = [AlbumData.from_tekore_model(album) for album in albums_sorted]
    album_ids = [album.id for album in albums]

    has_albums = list_has(albums_sorted, AlbumType.album)
    has_simgles = list_has(albums_sorted, AlbumType.single)
    has_compilations = list_has(albums_sorted, AlbumType.compilation)

    if request.method == "GET":
        return render(
            request,
            "web/albums.html",
            context={
                "artist": artist,
                "albums": albums,
                "album_ids": album_ids,
                "has_albums": has_albums,
                "has_singles": has_simgles,
                "has_compilations": has_compilations,
                "events_enabled": request.session["spotify_user_spotify_id"]
                in settings.EVENTS_ENABLED_FOR_SPOTIFY_USER_IDS,
            },
        )
    else:
        requested_album_ids = request.POST.getlist("album")
        if not requested_album_ids:
            error_message = "No albums selected"
            return trigger_client_event(
                HttpResponseBadRequest(error_message), "HXToast", {"type": "error", "body": error_message}
            )

        name = request.POST.get("name", f"{artist.name} discography")
        is_public = bool(request.POST.get("is-public", False))

        tracks = await request.spotify_client.get_tracks_in_albums(requested_album_ids)

        playlist = await request.spotify_client.create_playlist_with_tracks(
            request.session["spotify_user_spotify_id"],
            name=name,
            track_uris=[track.uri for track in tracks],
            is_public=is_public,
        )

        if bool(request.POST.get("auto-update", False)):
            albums_ignored = list(set(album_ids) - set(requested_album_ids))

            auto_accept = bool(request.POST.get("auto-accept", False))
            if auto_accept:
                logger.info(f"Setting up automatic accept for playlist {playlist.id} from artist {artist_id}")

            await Playlist.watch_artist(
                request.spotify_client,
                playlist.id,
                artist_id,
                albums_ignored=albums_ignored,
                auto_accept_updates=auto_accept,
            )

        if bool(request.POST.get("generate-cover", False)):
            await sync_to_async(task_upload_cover_image)(
                playlist_title=name,
                playlist_spotify_id=playlist.id,
                spotify_user_id=request.session["spotify_user_id"],
                dump_to_disk=True,
            )

        if request.session["spotify_user_spotify_id"] in settings.EVENTS_ENABLED_FOR_SPOTIFY_USER_IDS and bool(
            request.POST.get("track-events", False)
        ):
            await sync_to_async(task_track_artists_events)(
                artists_data={artist_id: artist.name}, spotify_user_id=request.session["spotify_user_id"]
            )

        # TODO: Toast
        return redirect("playlist", playlist_id=playlist.id)


@catch_errors
@require_GET
async def album(request: MottleHttpRequest, album_id: str) -> HttpResponse:
    album_metadata = AlbumMetadata(request, album_id)
    album = await AlbumData.from_metadata(album_metadata)

    tracks = await request.spotify_client.get_album_tracks(album_id)
    tracks = [TrackData.from_tekore_model(track, album=album) for track in tracks]

    return render(
        request,
        "web/album.html",
        context={"album": album, "tracks": tracks},
    )


@catch_errors
@require_GET
async def playlists(request: MottleHttpRequest) -> HttpResponse:
    playlists = await request.spotify_client.get_current_user_playlists()

    # https://community.spotify.com/t5/Spotify-for-Developers/null-values-when-using-quot-Get-Current-User-s-Playlists-quot/td-p/6549968
    playlists = [PlaylistData.from_tekore_model(playlist) for playlist in playlists if playlist is not None]

    db_watching_playlists = PlaylistWatchConfig.objects.filter(
        watching_playlist__spotify_user__id=request.session["spotify_user_id"],
    ).values("watching_playlist__spotify_id")

    watching_playlists = [item["watching_playlist__spotify_id"] async for item in db_watching_playlists]

    return render(
        request,
        "web/playlists.html",
        {"playlists": playlists, "watching_playlists": watching_playlists},
    )


# @require_GET
# async def followed_artists(request: MottleHttpRequest) -> HttpResponse:
#     artists = await request.spotify_client.get_current_user_followed_artists()
#     context = {"artists": artists}
#     return render(request, "web/followed_artists.html", context)


@catch_errors
@require_GET
async def playlist_updates(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist_metadata = PlaylistMetadata(request, playlist_id)
    playlist = await PlaylistData.from_metadata(playlist_metadata)
    db_playlist = await aget_object_or_404(
        Playlist,
        spotify_id=playlist_id,
        spotify_user__id=request.session["spotify_user_id"],
    )

    await check_playlist_for_updates(db_playlist, request.spotify_client)

    updates = []

    async for update in db_playlist.pending_updates:
        watched_playlist = await sync_to_async(lambda: update.source_playlist)()
        watched_artist = await sync_to_async(lambda: update.source_artist)()

        if watched_playlist is not None:
            watched_playlist_data = await request.spotify_client.get_playlist(watched_playlist.spotify_id)
            watched_playlist_tracks = await request.spotify_client.get_tracks(update.tracks_added)  # type: ignore [arg-type]  # TODO: WTF!?
            tracks = [TrackData.from_tekore_model(track) for track in watched_playlist_tracks]
            updates.append((update.id, watched_playlist_data, tracks))
        elif watched_artist is not None:
            watched_artist_data = await request.spotify_client.get_artist(watched_artist.spotify_id)
            tracks = await request.spotify_client.get_tracks_in_albums(update.albums_added)  # type: ignore [arg-type]  # TODO: WTF!?
            track_ids = [track.id for track in tracks]
            watched_artist_tracks = await request.spotify_client.get_tracks(track_ids)
            tracks = [TrackData.from_tekore_model(track) for track in watched_artist_tracks]
            updates.append((update.id, watched_artist_data, watched_artist_tracks))
        else:
            # XXX: This should never happen
            logger.error(f"PlaylistUpdate {update.id} has no source_playlist or source_artist")
            continue

    if request.method == "POST":
        return render(
            request,
            "web/parts/playlist_updates.html",
            {
                "playlist": playlist,
                "updates": updates,
            },
        )
    else:
        return render(
            request,
            "web/playlist_updates.html",
            {
                "playlist": playlist,
                "updates": updates,
            },
        )


@catch_errors
@require_POST
async def accept_playlist_update(request: MottleHttpRequest, playlist_id: str, update_id: str) -> HttpResponse:
    update = await aget_object_or_404(
        PlaylistUpdate,
        id=update_id,
        target_playlist__spotify_id=playlist_id,
        target_playlist__spotify_user__id=request.session["spotify_user_id"],
    )

    await update.accept(request.spotify_client)
    return trigger_client_event(
        HttpResponse(),
        "HXToast",
        {"type": "success", "body": "Update accepted"},
    )


@catch_errors
@require_GET
async def playlist_items(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist_metadata = PlaylistMetadata(request, playlist_id)
    playlist = await PlaylistData.from_metadata(playlist_metadata)
    playlist_tracks = await request.spotify_client.get_playlist_tracks(playlist_id)

    # TODO: There should be another way to check if a playlist belongs to the current user
    user_playlists = await request.spotify_client.get_current_user_playlists()
    user_playlist_ids = [playlist.id for playlist in user_playlists]

    try:
        await PlaylistWatchConfig.objects.aget(
            watching_playlist__spotify_user__id=request.session["spotify_user_id"],
            watching_playlist__spotify_id=playlist_id,
        )
    except PlaylistWatchConfig.DoesNotExist:
        watching_playlists = []
    except PlaylistWatchConfig.MultipleObjectsReturned:
        watching_playlists = [playlist_id]
    else:
        watching_playlists = [playlist_id]

    tracks = [
        TrackData.from_tekore_model(track.track, added_at=track.added_at.date())
        for track in playlist_tracks
        if isinstance(track.track, FullPlaylistTrack)
    ]
    if request.session["spotify_user_spotify_id"] in settings.EVENTS_ENABLED_FOR_SPOTIFY_USER_IDS and request.GET.get(
        "track-artists", False
    ):
        artists = {}
        for track in tracks:
            for artist in track.artists:
                artists[artist.id] = artist.name

        await sync_to_async(task_track_artists_events)(
            artists_data=artists, spotify_user_id=request.session["spotify_user_id"]
        )

    context = {
        "playlist": playlist,
        "tracks": tracks,
        "watching_playlists": watching_playlists,
        "user_playlist_ids": user_playlist_ids,
    }
    return render(request, "web/playlist.html", context)


@catch_errors
@require_POST
async def follow_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist_metadata = PlaylistMetadata(request, playlist_id)
    playlist = await PlaylistData.from_metadata(playlist_metadata)

    await request.spotify_client.follow_playlist(playlist_id)

    source = request.headers.get("M-Source")
    if not source:
        return trigger_client_event(
            HttpResponseServerError("Failed to follow playlist"),
            "HXToast",
            {"type": "error", "body": "Failed to follow playlist"},
        )

    return render(
        request,
        "web/parts/playlist_unfollow.html",
        {"playlist": playlist, "source": source},
    )


@catch_errors
@require_POST
async def unfollow_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist_metadata = PlaylistMetadata(request, playlist_id)
    playlist = await PlaylistData.from_metadata(playlist_metadata)

    await request.spotify_client.unfollow_playlist(playlist_id)

    try:
        db_playlist = await Playlist.objects.aget(spotify_id=playlist_id)
    except Playlist.DoesNotExist:
        logger.info(f"Playlist {playlist_id} not found in database. Skipping")
        pass
    else:
        await db_playlist.unfollow()

    if request.headers.get("M-Source") == "playlists_search":
        return render(
            request,
            "web/parts/playlist_follow.html",
            {"playlist": playlist, "source": "playlists_search"},
        )
    elif request.headers.get("M-Source") == "playlists_my":
        if playlist.owner_id == request.session["spotify_user_spotify_id"]:
            return trigger_client_event(
                HttpResponse(),
                "HXToast",
                {"type": "success", "body": "Successfully unfollowed playlist"},
            )
        else:
            return trigger_client_event(
                render(
                    request,
                    "web/parts/playlist_follow.html",
                    {"playlist": playlist, "source": "playlists_my"},
                ),
                "HXToast",
                {"type": "success", "body": "Successfully unfollowed playlist"},
            )
    elif request.headers.get("M-Source") == "playlist":
        if playlist.owner_id == request.session["spotify_user_spotify_id"]:
            return HttpResponseClientRedirect("/playlists")
        else:
            return trigger_client_event(
                render(
                    request,
                    "web/parts/playlist_follow.html",
                    {"playlist": playlist, "source": "playlist"},
                ),
                "HXToast",
                {"type": "success", "body": "Successfully unfollowed playlist"},
            )
    else:
        return HttpResponseServerError("Failed to unfollow playlist")


@catch_errors
@require_http_methods(["GET", "POST"])
async def deduplicate_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist_metadata = PlaylistMetadata(request, playlist_id)
    playlist = await PlaylistData.from_metadata(playlist_metadata)

    if request.method == "POST":
        if playlist.owner_id != request.session["spotify_user_spotify_id"]:
            return HttpResponseBadRequest("You can only deduplicate your own playlists")

        # XXX: The whole remove_tracks_at_positions_from_playlist is not working as expected,
        # which is probably the reason it is not documented in the Spotify API.
        # The API request is successful, but the tracks' positions in request payload are ignored:
        # all specified tracks are removed from the playlist, regardless of their positions.

        # track_data = dict([item.split("::") for item in request.POST.getlist("track-meta")])
        # tracks_to_remove = [
        #     {"uri": k, "positions": [int(i) for i in v.split(",")][1:]} for k, v in track_data.items()
        # ]
        tracks_to_remove = request.POST.getlist("track-ids")

        logger.debug(f"Tracks: {tracks_to_remove}")
        logger.debug(f"Removing {len(tracks_to_remove)} tracks from playlist {playlist_id}")

        tracks = [f"spotify:track:{track_id}" for track_id in tracks_to_remove]

        # await request.spotify_client.remove_tracks_at_positions_from_playlist(
        #     playlist_id, tracks_to_remove, playlist_snapshot_id
        # )
        await request.spotify_client.remove_tracks_from_playlist(playlist_id, tracks)
        await request.spotify_client.add_tracks_to_playlist(playlist_id, tracks)

        return HttpResponse("<article><aside><h3>No duplicates found</h3></aside></article>")

    else:
        duplicates = await request.spotify_client.find_duplicate_tracks_in_playlist(playlist_id)
        duplicates = [(TrackData.from_tekore_model(track), _) for track, _ in duplicates]

        # TODO: Toast
        return render(
            request,
            "web/deduplicate.html",
            context={
                "playlist": playlist,
                "duplicates": duplicates,
                "message": get_duplicates_message(duplicates),
            },
        )


@catch_errors
@require_POST
async def remove_tracks_from_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    track_ids = request.POST.getlist("track-id")
    if not track_ids:
        return HttpResponseBadRequest("No tracks selected")

    track_uris = [f"spotify:track:{track_id}" for track_id in track_ids]

    await request.spotify_client.remove_tracks_from_playlist(playlist_id, track_uris)

    return trigger_client_event(
        HttpResponse(),
        "HXToast",
        {"type": "success", "body": "Track(s) removed from playlist"},
    )


@catch_errors
@require_GET
async def playlist_audio_features(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist_metadata = PlaylistMetadata(request, playlist_id)
    playlist_name = await playlist_metadata.name

    # TODO: This view will be navigated to from the playlist view, so playlist items could be passed from there?
    playlist_items = await request.spotify_client.get_playlist_tracks(playlist_id)

    tracks = [item.track for item in playlist_items if item.track is not None and item.track.id is not None]

    track_ids = [track.id for track in tracks]
    tracks_features = await request.spotify_client.get_playlist_tracks_audio_features(track_ids)

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


@catch_errors
@require_POST
async def copy_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist_metadata = PlaylistMetadata(request, playlist_id)
    playlist_name = await playlist_metadata.name
    playlist_items = await request.spotify_client.get_playlist_tracks(playlist_id)

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

    playlist = await request.spotify_client.create_playlist_with_tracks(
        request.session["spotify_user_spotify_id"],
        name=f"Copy of {playlist_name}",
        track_uris=[track.track.uri for track in playlist_items if isinstance(track.track, FullPlaylistTrack)],
        is_public=True,  # TODO: How do we decide on the value?
        # cover_image=cover_image_base64_data,
        cover_image=None,
        add_tracks_parallelized=settings.PLAYLIST_ADD_TRACKS_PARALLELIZED,
        fail_on_cover_image_upload_error=False,
    )

    # When the playlist is initially created, it is empty, and its attributes (e.g. owner) are still not defined.
    # Therefore we fetch it again.
    playlist = await request.spotify_client.get_playlist(playlist.id)

    # TODO: Toast
    return render(request, "web/parts/playlist_row.html", context={"playlist": playlist})


@catch_errors
@require_http_methods(["GET", "POST"])
async def merge_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    source_playlist_id = playlist_id

    if request.method == "GET":
        return await get_playlist_modal_response(request, playlist_id, "web/modals/playlist_merge.html")
    else:
        target_playlist_id = request.POST.get("merge-target")
        target_playlist_name_new = request.POST.get("new-playlist-name")

        if target_playlist_id:
            if target_playlist_id == "--- Create new ---":
                if target_playlist_name_new:
                    target_playlist = await request.spotify_client.create_playlist(
                        request.session["spotify_user_spotify_id"],
                        target_playlist_name_new,
                        is_public=True,
                    )
                    target_playlist_id = target_playlist.id
                else:
                    return trigger_client_event(
                        HttpResponseBadRequest("No new playlist name provided"),
                        "HXToast",
                        {"type": "error", "body": "No new playlist name provided"},
                    )
        else:
            return trigger_client_event(
                HttpResponseBadRequest("No merge target provided"),
                "HXToast",
                {"type": "error", "body": "No merge target provided"},
            )

        source_playlist_items = await request.spotify_client.get_playlist_tracks(source_playlist_id)

        await request.spotify_client.add_tracks_to_playlist(
            target_playlist_id,  # type: ignore [arg-type]  # TODO: WTF!?
            [item.track.uri for item in source_playlist_items if isinstance(item.track, FullPlaylistTrack)],
        )

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
                request.spotify_client,
                target_playlist_id,  # type: ignore [arg-type]  # TODO: WTF!?
                source_playlist_id,
                auto_accept_updates=auto_accept,
            )

        return trigger_client_event(
            HttpResponse(),
            "HXToast",
            {"type": "success", "body": "Successfully merged playlists"},
        )


@catch_errors
@require_GET
async def configure_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist_metadata = PlaylistMetadata(request, playlist_id)
    playlist = await PlaylistData.from_metadata(playlist_metadata)

    try:
        db_playlist = await Playlist.objects.aget(
            spotify_id=playlist_id,
            spotify_user__id=request.session["spotify_user_id"],
        )
    except Playlist.DoesNotExist:
        watched_playlist_settings: list[tuple[PlaylistData, bool]] = []
        watched_artist_settings: list[tuple[ArtistData, bool]] = []
    else:
        configs = PlaylistWatchConfig.objects.filter(watching_playlist=db_playlist)
        watched_playlist_settings = []
        watched_artist_settings = []

        async for config in configs:
            db_watched_playlist = await sync_to_async(lambda: config.watched_playlist)()
            db_watched_artist = await sync_to_async(lambda: config.watched_artist)()

            if db_watched_playlist is not None:
                watched_playlist = await request.spotify_client.get_playlist(db_watched_playlist.spotify_id)
                watched_playlist_settings.append(
                    (
                        PlaylistData.from_tekore_model(watched_playlist),
                        config.auto_accept_updates,
                    )
                )

            if db_watched_artist is not None:
                watched_artist = await request.spotify_client.get_artist(db_watched_artist.spotify_id)
                watched_artist_settings.append(
                    (
                        ArtistData.from_tekore_model(watched_artist),
                        config.auto_accept_updates,
                    )
                )

    return render(
        request,
        "web/modals/playlist_configure.html",
        context={
            "playlist": playlist,
            "watched_playlists": watched_playlist_settings,
            "watched_artists": watched_artist_settings,
        },
    )


@catch_errors
@require_http_methods(["GET", "POST", "DELETE"])
async def rename_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist_metadata = PlaylistMetadata(request, playlist_id)
    playlist = await PlaylistData.from_metadata(playlist_metadata)

    if request.method == "GET":
        return render(request, "web/parts/playlist_rename.html", context={"playlist": playlist})
    elif request.method == "POST":
        name = request.POST.get("name")
        if not name:
            return trigger_client_event(
                HttpResponseBadRequest("No name provided"),
                "HXToast",
                {"type": "error", "body": "No name provided"},
            )

        await request.spotify_client.change_playlist_details(playlist_id, name)

        playlist.name = name
        # TODO: Toast
        return render(request, "web/parts/playlist_name.html", {"playlist": playlist})
    else:
        # TODO: Toast
        return render(request, "web/parts/playlist_name.html", {"playlist": playlist})


@catch_errors
@require_POST
async def playlist_cover_image(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist_metadata = PlaylistMetadata(request, playlist_id)
    playlist = await PlaylistData.from_metadata(playlist_metadata)

    await sync_to_async(task_upload_cover_image)(
        playlist_title=playlist.name,
        playlist_spotify_id=playlist_id,
        spotify_user_id=request.session["spotify_user_id"],
        dump_to_disk=True,
    )
    return trigger_client_event(
        HttpResponse(),
        "HXToast",
        {"type": "success", "body": "Cover image generation requested. It may take a while to update."},
    )


@catch_errors
@require_http_methods(["GET", "POST"])
async def watch_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    if request.method == "GET":
        return await get_playlist_modal_response(request, playlist_id, "web/modals/playlist_watch.html")
    else:
        watching_playlist_id = request.POST.get("watching-playlist-id")
        watching_playlist_name_new = request.POST.get("new-playlist-name")

        if watching_playlist_id:
            if watching_playlist_id == "--- Create new ---":
                if watching_playlist_name_new:
                    watching_playlist = await request.spotify_client.create_playlist(
                        request.session["spotify_user_spotify_id"],
                        watching_playlist_name_new,
                        is_public=True,
                    )
                    watching_playlist_id = watching_playlist.id
                else:
                    return trigger_client_event(
                        HttpResponseBadRequest("No watching playlist name provided"),
                        "HXToast",
                        {"type": "error", "body": "No watching playlist name provided"},
                    )
        else:
            return trigger_client_event(
                HttpResponseBadRequest("No watching playlist provided"),
                "HXToast",
                {"type": "error", "body": "No watching playlist provided"},
            )

        if not watching_playlist_id:
            return trigger_client_event(
                HttpResponseBadRequest("No watching playlist ID provided"),
                "HXToast",
                {"type": "error", "body": "No watching playlist ID provided"},
            )

        auto_accept = bool(request.POST.get("auto-accept", False))
        if auto_accept:
            logger.info(f"Setting up automatic accept for playlist {watching_playlist_id} from playlist {playlist_id}")

        await Playlist.watch_playlist(
            request.spotify_client,
            watching_playlist_id,
            playlist_id,
            auto_accept_updates=auto_accept,
        )

        return trigger_client_event(
            HttpResponse(),
            "HXToast",
            {"type": "success", "body": "Successfully started watching playlist"},
        )


@catch_errors
@require_POST
async def unwatch_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    # https://stackoverflow.com/a/22294734
    # TODO: This is probably not needed anymore since we are using POST. A leftover from an appempt to use DELETE?
    body = QueryDict(request.body)  # pyright: ignore
    watching_playlist_id = body.get("watching-playlist-id")

    if not watching_playlist_id:
        return trigger_client_event(
            HttpResponseBadRequest("No watching playlist ID provided"),
            "HXToast",
            {"type": "error", "body": "No watching playlist ID provided"},
        )

    watching_playlist = await aget_object_or_404(
        Playlist,
        spotify_id=watching_playlist_id,
        spotify_user__id=request.session["spotify_user_id"],
    )
    watched_playlist = await aget_object_or_404(Playlist, spotify_id=playlist_id)

    await watching_playlist.unwatch(watched_playlist)
    return trigger_client_event(
        HttpResponse(),
        "HXToast",
        {"type": "success", "body": "Successfully unwatched playlist"},
    )


@catch_errors
@require_POST
async def auto_accept_playlist_updates(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    watching_playlist = await aget_object_or_404(
        Playlist,
        spotify_id=playlist_id,
        spotify_user__id=request.session["spotify_user_id"],
    )

    watched_playlist_id = request.POST.get("watched-playlist-id")
    watched_artist_id = request.POST.get("watched-artist-id")

    if watched_playlist_id:
        config = await aget_object_or_404(
            PlaylistWatchConfig,
            watching_playlist=watching_playlist,
            watched_playlist__spotify_id=watched_playlist_id,
        )
    elif watched_artist_id:
        config = await aget_object_or_404(
            PlaylistWatchConfig,
            watching_playlist=watching_playlist,
            watched_artist__spotify_id=watched_artist_id,
        )
    else:
        return trigger_client_event(
            HttpResponseBadRequest("No watched playlist or artist ID provided"),
            "HXToast",
            {"type": "error", "body": "No watched playlist or artist ID provided"},
        )

    new_setting = not config.auto_accept_updates
    config.auto_accept_updates = new_setting
    await config.asave()

    # TODO: Toast
    return trigger_client_event(
        render(request, "web/icons/accept.html", {"enabled": new_setting}),
        "HXToast",
        {"type": "success", "body": "Auto-accept setting updated"},
    )


@catch_errors
@require_GET
async def artist_events(request: MottleHttpRequest, artist_id: str) -> HttpResponse:
    artist_metadata = ArtistMetadata(request, artist_id)
    artist = await ArtistData.from_metadata(artist_metadata)

    event_artist = await track_artist_events(artist_id, artist.name, request.session["spotify_user_id"])
    await event_artist.update_events()

    events = [e async for e in event_artist.events.filter(date__gte=datetime.today()).order_by("date")]  # pyright: ignore
    return render(request, "web/events.html", context={"artist": artist, "events": events})


@catch_errors
@require_http_methods(["GET", "POST"])
async def user_settings(request: MottleHttpRequest) -> HttpResponse:
    spotify_user = await SpotifyUser.objects.aget(spotify_id=request.session["spotify_user_spotify_id"])
    events_enabled = request.session["spotify_user_spotify_id"] in settings.EVENTS_ENABLED_FOR_SPOTIFY_USER_IDS
    user = await sync_to_async(lambda: spotify_user.user)()  # pyright: ignore

    if request.method == "GET":
        if user.location:
            longitude, latitude = user.location
            radius = user.event_distance_threshold
        else:
            longitude, latitude, radius = None, None, None

        return render(
            request,
            "web/settings.html",
            context={
                "playlist_notifications": user.playlist_notifications,
                "release_notifications": user.release_notifications,
                "event_notifications": user.event_notifications,
                "latitude": latitude,
                "longitude": longitude,
                "radius": radius,
                "events_enabled": events_enabled,
            },
        )
    else:
        playlist_notifications = bool(request.POST.get("playlist-notifications", False))
        release_notifications = bool(request.POST.get("release-notifications", False))
        event_notifications = bool(request.POST.get("event-notifications", False))

        if lat := request.POST.get("latitude"):
            latitude = float(lat)
        else:
            latitude = None

        if lon := request.POST.get("longitude"):
            longitude = float(lon)
        else:
            longitude = None

        if rad := request.POST.get("radius"):
            radius = float(rad)
        else:
            radius = None

        user.playlist_notifications = playlist_notifications
        user.release_notifications = release_notifications
        user.event_notifications = event_notifications

        if event_notifications:
            if all((latitude, longitude, radius)):
                user.location = Point(longitude, latitude, srid=settings.GEODJANGO_SRID)
                user.event_distance_threshold = radius
            else:
                return trigger_client_event(
                    HttpResponseBadRequest("Location and radius must be provided for event notifications"),
                    "HXToast",
                    {"type": "error", "body": "Location and radius must be provided for event notifications"},
                )

        await user.asave()

        return trigger_client_event(
            render(
                request,
                "web/parts/settings.html",
                context={
                    "playlist_notifications": user.playlist_notifications,
                    "release_notifications": user.release_notifications,
                    "event_notifications": user.event_notifications,
                    "latitude": latitude,
                    "longitude": longitude,
                    "radius": radius,
                    "events_enabled": events_enabled,
                },
            ),
            "HXToast",
            {"type": "success", "body": "Settings updated successfully"},
        )


@catch_errors
@require_GET
async def user_events(request: MottleHttpRequest) -> HttpResponse:
    spotify_user = await SpotifyUser.objects.aget(spotify_id=request.session["spotify_user_spotify_id"])
    events_enabled = request.session["spotify_user_spotify_id"] in settings.EVENTS_ENABLED_FOR_SPOTIFY_USER_IDS

    if not events_enabled:
        return trigger_client_event(
            HttpResponseBadRequest("Events are not available yet. Please check back later"),
            "HXToast",
            {"type": "error", "body": "Events are not available yet. Please check back later"},
        )

    user = await sync_to_async(lambda: spotify_user.user)()  # pyright: ignore

    events = await user.upcoming_events()  # pyright: ignore

    artist_ids = [(await sync_to_async(lambda: a.artist)()).spotify_id for a in events.keys()]
    artists = await request.spotify_client.get_artists(artist_ids)
    artists = [ArtistData.from_tekore_model(a) for a in artists]

    events_data = dict(zip(artists, events.values()))
    return render(request, "web/user_events.html", context={"upcoming_events": dict(sorted(events_data.items()))})


@catch_errors
@require_GET
async def event_details(request: MottleHttpRequest, event_id: str) -> HttpResponse:
    artist = request.headers.get("M-Artist-Name")
    if artist is not None:
        artist = unquote(artist)

    event = await Event.objects.aget(id=event_id)
    return render(request, "web/modals/event_details.html", context={"artist": artist, "event": event})


@catch_errors
@require_GET
async def saved_tracks(request: MottleHttpRequest) -> HttpResponse:
    tracks = await request.spotify_client.get_current_user_saved_tracks()
    tracks = [TrackData.from_tekore_model(t.track, added_at=t.added_at) for t in tracks]

    return render(request, "web/saved_tracks.html", context={"tracks": tracks})


@catch_errors
@require_POST
async def remove_saved_tracks(request: MottleHttpRequest) -> HttpResponse:
    track_ids = request.POST.getlist("track-id")
    if not track_ids:
        return HttpResponseBadRequest("No tracks selected")

    await request.spotify_client.remove_user_saved_tracks(track_ids)

    return trigger_client_event(
        HttpResponse(),
        "HXToast",
        {"type": "success", "body": "Saved track(s) removed"},
    )
