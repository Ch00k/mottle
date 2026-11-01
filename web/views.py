import logging
from datetime import UTC, datetime
from itertools import groupby
from urllib.parse import unquote

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.gis.geos import Point
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseServerError,
)
from django.shortcuts import aget_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
    require_safe,
)
from django_htmx.http import push_url
from tekore.model import AlbumType, FullPlaylistTrack

from web.spotify_items import get_smallest_image

from .background import run_in_background
from .data import AlbumData, ArtistData, PlaylistData, TrackData
from .middleware import MottleHttpRequest, get_signed_in_auth
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
from .tasks import atrack_artists_events, check_playlist_for_updates, track_artist_events, upload_cover_image
from .utils import MottleException, MottleSpotifyClient
from .views_utils import bad_request_toast, get_playlist_modal_response, toast, wants_fragment

ALBUM_SORT_ORDER = {AlbumType.album: 0, AlbumType.single: 1, AlbumType.compilation: 2}

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
async def login(request: MottleHttpRequest) -> HttpResponse:
    redirect_uri: str | None

    if request.method == "GET":
        if await get_signed_in_auth(request.session) is not None:
            return redirect("index")

        # Django template does not undestand None (it will treat it as a literal 'None'),
        # so we need to pass an empty string
        redirect_uri = request.GET.get("next", "")
        return render(request, "web/login.html", {"redirect_uri": redirect_uri})

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


@require_GET
async def logout(request: MottleHttpRequest) -> HttpResponse:
    await sync_to_async(request.session.flush)()
    return redirect("login")


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
    except SpotifyAuthRequest.DoesNotExist:
        logger.debug(f"SpotifyAuthRequest with state {state} does not exist")
        return HttpResponseBadRequest("Invalid request")

    token = await spotify_auth_request.request_token(code)
    logger.debug(f"Received token, expires at {token.expires_at}")
    spotify_client = MottleSpotifyClient(access_token=token.access_token)

    try:
        user = await spotify_client.get_current_user()
    except MottleException as e:
        logger.error(e)
        return HttpResponseServerError("Failed to get user")

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
    request.session["spotify_user_image_url"] = get_smallest_image(user.images)
    request.session["feature_flags"] = {"events_enabled": settings.EVENTS_ENABLED}

    await spotify_auth_request.adelete()

    return redirect(spotify_auth_request.redirect_uri or "index")


@require_safe  # Accept HEAD requests from UptimeRobot
def index(request: MottleHttpRequest) -> HttpResponse:
    if request.session.get("spotify_user_id"):
        return redirect("playlists")

    return render(request, "web/index.html")


def changelog(request: MottleHttpRequest) -> HttpResponse:
    return render(request, "web/changelog.html")


@require_GET
async def search(request: MottleHttpRequest) -> HttpResponse:
    query = request.GET.get("query", "")

    if not query:
        # Emptying the search box gets the results' empty state, at the search page's own URL
        if wants_fragment(request):
            return push_url(render(request, "web/parts/search_results.html", {"query": ""}), reverse("search"))
        return render(request, "web/search.html", context={"results": [], "query": ""})

    artists, playlists = await request.spotify_client.find_artists_and_playlists(query)
    artists = [ArtistData.from_tekore_model(artist) for artist in artists]

    # https://community.spotify.com/t5/Spotify-for-Developers/null-values-when-using-quot-Get-Current-User-s-Playlists-quot/td-p/6549968
    playlists = [PlaylistData.from_tekore_model(playlist) for playlist in playlists if playlist is not None]

    # Skip own playlists
    playlists = [p for p in playlists if p.owner_id != request.session["spotify_user_spotify_id"]]

    # Skip followed playlists
    # TODO: There should be another way to check if a playlist belongs to the current user
    user_playlists = await request.spotify_client.get_current_user_playlists()
    user_playlist_ids = [playlist.id for playlist in user_playlists]
    playlists = [p for p in playlists if p.id not in user_playlist_ids]

    if wants_fragment(request):
        return push_url(
            render(
                request,
                "web/parts/search_results.html",
                context={
                    "artists": artists,
                    "playlists": playlists,
                    "user_playlist_ids": user_playlist_ids,
                    "query": query,
                },
            ),
            "?query=" + unquote(query),
        )

    return render(
        request,
        "web/search.html",
        context={
            "artists": artists,
            "playlists": playlists,
            "user_playlist_ids": user_playlist_ids,
            "query": query,
        },
    )


@require_http_methods(["GET", "POST"])
async def albums(request: MottleHttpRequest, artist_id: str) -> HttpResponse:
    artist = ArtistData.from_tekore_model(await request.spotify_client.get_artist(artist_id))

    all_releases = await request.spotify_client.get_artist_albums_separately_by_type(artist_id)
    albums_sorted = sorted(
        sorted(all_releases, key=lambda x: x.release_date, reverse=True),
        key=lambda x: ALBUM_SORT_ORDER[x.album_type],
    )
    releases = [AlbumData.from_tekore_model(album) for album in albums_sorted]
    album_ids = [r.id for r in releases]
    releases_grouped = groupby(releases, key=lambda x: x.type)
    releases_dict = {t: list(r) for t, r in releases_grouped}

    albums = releases_dict.get(AlbumType.album, [])
    singles = releases_dict.get(AlbumType.single, [])
    compilations = releases_dict.get(AlbumType.compilation, [])

    if request.method == "GET":
        # A link leads to the artist's page, a button opens the dialog creating the discography
        template = "web/modals/playlist_discography.html" if wants_fragment(request) else "web/artist.html"
        return render(
            request,
            template,
            context={
                "artist": artist,
                "albums": albums,
                "singles": singles,
                "compilations": compilations,
            },
        )

    requested_album_ids = request.POST.getlist("album")
    if not requested_album_ids:
        return bad_request_toast("No albums selected")

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
        run_in_background(
            upload_cover_image(
                playlist_title=name,
                playlist_spotify_id=playlist.id,
                spotify_user_id=request.session["spotify_user_id"],
            )
        )

    if settings.EVENTS_ENABLED and bool(request.POST.get("track-events", False)):
        run_in_background(
            atrack_artists_events(
                artists_data={artist_id: artist.name}, spotify_user_id=request.session["spotify_user_id"]
            )
        )

    # TODO: Toast
    return redirect("playlist", playlist_id=playlist.id)


@require_GET
async def album(request: MottleHttpRequest, album_id: str) -> HttpResponse:
    album = AlbumData.from_tekore_model(await request.spotify_client.get_album(album_id))

    tracks = await request.spotify_client.get_album_tracks(album_id)
    tracks = [TrackData.from_tekore_model(track, album=album) for track in tracks]

    return render(
        request,
        "web/album.html",
        context={"album": album, "tracks": tracks},
    )


@require_GET
async def playlists(request: MottleHttpRequest) -> HttpResponse:
    playlists = await request.spotify_client.get_current_user_playlists()

    # https://community.spotify.com/t5/Spotify-for-Developers/null-values-when-using-quot-Get-Current-User-s-Playlists-quot/td-p/6549968
    playlists = [PlaylistData.from_tekore_model(playlist) for playlist in playlists if playlist is not None]
    playlists = sorted(playlists, key=lambda x: x.owner_id == request.session["spotify_user_spotify_id"], reverse=True)

    db_watching_playlists = PlaylistWatchConfig.objects.filter(
        watching_playlist__spotify_user__id=request.session["spotify_user_id"],
    ).values("watching_playlist__spotify_id")

    watching_playlists = [item["watching_playlist__spotify_id"] async for item in db_watching_playlists]

    return render(
        request,
        "web/playlists.html",
        {"playlists": playlists, "watching_playlists": watching_playlists},
    )


@require_GET
async def playlist_updates(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    db_playlist = await aget_object_or_404(
        Playlist,
        spotify_id=playlist_id,
        spotify_user__id=request.session["spotify_user_id"],
    )

    await check_playlist_for_updates(db_playlist, request.spotify_client)

    updates = []

    async for update in db_playlist.pending_updates.select_related("source_playlist", "source_artist"):
        watched_playlist = update.source_playlist
        watched_artist = update.source_artist

        if watched_playlist is not None:
            watched_playlist_data = await request.spotify_client.get_playlist(watched_playlist.spotify_id)
            watched_playlist_tracks = await request.spotify_client.get_tracks(update.tracks_added)
            tracks = [TrackData.from_tekore_model(track) for track in watched_playlist_tracks]
            updates.append((update.id, watched_playlist_data, tracks))
        elif watched_artist is not None:
            watched_artist_data = await request.spotify_client.get_artist(watched_artist.spotify_id)
            tracks = await request.spotify_client.get_tracks_in_albums(update.albums_added)
            track_ids = [track.id for track in tracks]
            watched_artist_tracks = await request.spotify_client.get_tracks(track_ids)
            tracks = [TrackData.from_tekore_model(track) for track in watched_artist_tracks]
            updates.append((update.id, watched_artist_data, tracks))
        else:
            # XXX: This should never happen
            logger.error(f"PlaylistUpdate {update.id} has no source_playlist or source_artist")
            continue

    return render(
        request,
        "web/modals/playlist_updates.html",
        {
            "playlist_id": playlist_id,
            "updates": updates,
        },
    )


@require_POST
async def accept_playlist_update(request: MottleHttpRequest, playlist_id: str, update_id: str) -> HttpResponse:
    update = await aget_object_or_404(
        PlaylistUpdate,
        id=update_id,
        target_playlist__spotify_id=playlist_id,
        target_playlist__spotify_user__id=request.session["spotify_user_id"],
    )

    await update.accept(request.spotify_client)
    return toast(HttpResponse(), "success", "Update accepted")


@require_POST
async def accept_playlist_updates(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    # TODO: This is inefficient
    updates = PlaylistUpdate.objects.filter(
        target_playlist__spotify_id=playlist_id,
        target_playlist__spotify_user__id=request.session["spotify_user_id"],
    )

    async for update in updates:
        await update.accept(request.spotify_client)

    return toast(HttpResponse(), "success", "Update accepted")


@require_GET
async def playlist_items(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist = PlaylistData.from_tekore_model(await request.spotify_client.get_playlist(playlist_id))
    playlist_tracks = await request.spotify_client.get_playlist_tracks(playlist_id)

    # TODO: There should be another way to check if a playlist belongs to the current user
    user_playlists = await request.spotify_client.get_current_user_playlists()
    user_playlist_ids = [playlist.id for playlist in user_playlists]

    is_watching = await PlaylistWatchConfig.objects.filter(
        watching_playlist__spotify_user__id=request.session["spotify_user_id"],
        watching_playlist__spotify_id=playlist_id,
    ).aexists()
    watching_playlists = [playlist_id] if is_watching else []

    tracks = [
        TrackData.from_tekore_model(track.track, added_at=track.added_at.date())
        for track in playlist_tracks
        if isinstance(track.track, FullPlaylistTrack)
    ]
    if settings.EVENTS_ENABLED and request.GET.get("track-artists", False):
        artists = {}
        for track in tracks:
            for artist in track.artists:
                artists[artist.id] = artist.name

        run_in_background(
            atrack_artists_events(
                artists_data=artists,
                spotify_user_id=request.session["spotify_user_id"],
            )
        )

    context = {
        "playlist": playlist,
        "tracks": tracks,
        "watching_playlists": watching_playlists,
        "user_playlist_ids": user_playlist_ids,
    }
    return render(request, "web/playlist.html", context)


@require_POST
async def follow_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    await request.spotify_client.follow_playlist(playlist_id)

    return toast(HttpResponse(), "success", "Successfully added playlist")


@require_POST
async def unfollow_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    await request.spotify_client.unfollow_playlist(playlist_id)

    try:
        db_playlist = await Playlist.objects.aget(spotify_id=playlist_id)
    except Playlist.DoesNotExist:
        logger.info(f"Playlist {playlist_id} not found in database. Skipping")
    else:
        await db_playlist.unfollow()

    return toast(HttpResponse(), "success", "Successfully removed playlist")


@require_http_methods(["GET", "POST"])
async def deduplicate_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist = PlaylistData.from_tekore_model(await request.spotify_client.get_playlist(playlist_id))

    if request.method == "POST":
        if playlist.owner_id != request.session["spotify_user_spotify_id"]:
            return HttpResponseBadRequest("You can only deduplicate your own playlists")

        # Spotify ignores the positions in a request to remove tracks at given positions, which its API does not
        # document, and removes every occurrence. So every copy of a duplicated track is removed and one is added back.

        tracks_to_remove = request.POST.getlist("track-ids")

        logger.debug(f"Tracks: {tracks_to_remove}")
        logger.debug(f"Removing {len(tracks_to_remove)} tracks from playlist {playlist_id}")

        tracks = [f"spotify:track:{track_id}" for track_id in tracks_to_remove]

        await request.spotify_client.remove_tracks_from_playlist(playlist_id, tracks)
        await request.spotify_client.add_tracks_to_playlist(playlist_id, tracks)

        return toast(HttpResponse(), "success", "Duplicates removed")

    duplicates = await request.spotify_client.find_duplicate_tracks_in_playlist(playlist_id)
    duplicates = [(TrackData.from_tekore_model(track), _) for track, _ in duplicates]

    # TODO: Toast
    return render(
        request,
        "web/modals/playlist_duplicates.html",
        context={
            "playlist": playlist,
            "duplicates": duplicates,
        },
    )


@require_POST
async def remove_tracks_from_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    track_ids = request.POST.getlist("track-id")
    if not track_ids:
        return HttpResponseBadRequest("No tracks selected")

    track_uris = [f"spotify:track:{track_id}" for track_id in track_ids]

    await request.spotify_client.remove_tracks_from_playlist(playlist_id, track_uris)

    return toast(HttpResponse(), "success", "Track(s) removed from playlist")


@require_POST
async def copy_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist_name = (await request.spotify_client.get_playlist(playlist_id)).name
    playlist_items = await request.spotify_client.get_playlist_tracks(playlist_id)

    # TODO: Uploading playlist cover image is weird. Right after playlist is created, the upload returns a 404 for
    # some time, then it returns a 502 for some time, and only then it returns a 202.
    # Disabling it for now because the whole thing is very flaky.

    playlist = await request.spotify_client.create_playlist_with_tracks(
        request.session["spotify_user_spotify_id"],
        name=f"Copy of {playlist_name}",
        track_uris=[track.track.uri for track in playlist_items if isinstance(track.track, FullPlaylistTrack)],
        is_public=True,  # TODO: How do we decide on the value?
    )

    # When the playlist is initially created, it is empty, and its attributes (e.g. owner) are still not defined.
    # Therefore we fetch it again.
    playlist = await request.spotify_client.get_playlist(playlist.id)

    # TODO: Toast
    return render(request, "web/parts/playlist_card.html", context={"playlist": playlist})


@require_http_methods(["GET", "POST"])
async def merge_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    source_playlist_id = playlist_id

    if request.method == "GET":
        return await get_playlist_modal_response(request, playlist_id, "web/modals/playlist_merge.html")

    merge_type = request.POST.get("merge-type")
    target_playlist_id = request.POST.get("merge-target")
    target_playlist_name_new = request.POST.get("new-playlist-name")

    if merge_type == "new":
        if target_playlist_name_new:
            target_playlist = await request.spotify_client.create_playlist(
                request.session["spotify_user_spotify_id"],
                target_playlist_name_new,
                is_public=True,
            )
            target_playlist_id = target_playlist.id
        else:
            return bad_request_toast("No new playlist name provided")
    elif not target_playlist_id:
        return bad_request_toast("No merge target provided")

    source_playlist_items = await request.spotify_client.get_playlist_tracks(source_playlist_id)

    await request.spotify_client.add_tracks_to_playlist(
        target_playlist_id,  # TODO: WTF!?
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
            target_playlist_id,  # TODO: WTF!?
            source_playlist_id,
            auto_accept_updates=auto_accept,
        )

    return toast(HttpResponse(), "success", "Successfully merged playlists")


@require_GET
async def configure_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist = PlaylistData.from_tekore_model(await request.spotify_client.get_playlist(playlist_id))

    try:
        db_playlist = await Playlist.objects.aget(
            spotify_id=playlist_id,
            spotify_user__id=request.session["spotify_user_id"],
        )
    except Playlist.DoesNotExist:
        watched_playlist_settings: list[tuple[PlaylistData, bool]] = []
        watched_artist_settings: list[tuple[ArtistData, bool]] = []
    else:
        configs = PlaylistWatchConfig.objects.filter(watching_playlist=db_playlist).select_related(
            "watched_playlist", "watched_artist"
        )
        watched_playlist_settings = []
        watched_artist_settings = []

        async for config in configs:
            db_watched_playlist = config.watched_playlist
            db_watched_artist = config.watched_artist

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


@require_http_methods(["GET", "POST", "DELETE"])
async def rename_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist = PlaylistData.from_tekore_model(await request.spotify_client.get_playlist(playlist_id))

    if request.method == "GET":
        return render(request, "web/parts/playlist_rename.html", context={"playlist": playlist})

    if request.method == "POST":
        name = request.POST.get("name")
        if not name:
            return bad_request_toast("No name provided")

        await request.spotify_client.change_playlist_details(playlist_id, name)

        playlist.name = name
        return toast(
            render(request, "web/parts/playlist_name_editable.html", {"playlist": playlist}),
            "success",
            "Playlist renamed",
        )

    return render(request, "web/parts/playlist_name_editable.html", {"playlist": playlist})


@require_POST
async def playlist_cover_image(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    playlist = PlaylistData.from_tekore_model(await request.spotify_client.get_playlist(playlist_id))

    run_in_background(
        upload_cover_image(
            playlist_title=playlist.name,
            playlist_spotify_id=playlist_id,
            spotify_user_id=request.session["spotify_user_id"],
        )
    )
    return toast(HttpResponse(), "success", "Cover image generation requested. It may take a while to update.")


@require_http_methods(["GET", "POST"])
async def watch_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    if request.method == "GET":
        return await get_playlist_modal_response(request, playlist_id, "web/modals/playlist_watch.html")

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
                return bad_request_toast("No watching playlist name provided")
    else:
        return bad_request_toast("No watching playlist provided")

    if not watching_playlist_id:
        return bad_request_toast("No watching playlist ID provided")

    auto_accept = bool(request.POST.get("auto-accept", False))
    if auto_accept:
        logger.info(f"Setting up automatic accept for playlist {watching_playlist_id} from playlist {playlist_id}")

    await Playlist.watch_playlist(
        request.spotify_client,
        watching_playlist_id,
        playlist_id,
        auto_accept_updates=auto_accept,
    )

    return toast(HttpResponse(), "success", "Successfully started watching playlist")


@require_POST
async def unwatch_playlist(request: MottleHttpRequest, playlist_id: str) -> HttpResponse:
    watching_playlist_id = request.POST.get("watching-playlist-id")

    if not watching_playlist_id:
        return bad_request_toast("No watching playlist ID provided")

    watching_playlist = await aget_object_or_404(
        Playlist,
        spotify_id=watching_playlist_id,
        spotify_user__id=request.session["spotify_user_id"],
    )
    watched_playlist = await aget_object_or_404(Playlist, spotify_id=playlist_id)

    await watching_playlist.unwatch(watched_playlist)
    return toast(HttpResponse(), "success", "Successfully unwatched playlist")


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
        return bad_request_toast("No watched playlist or artist ID provided")

    new_setting = not config.auto_accept_updates
    config.auto_accept_updates = new_setting
    await config.asave()

    return toast(
        render(request, "web/parts/accept.html", {"playlist_id": playlist_id, "enabled": new_setting}),
        "success",
        f"Auto-accept {'enabled' if new_setting else 'disabled'}",
    )


@require_GET
async def artist_events(request: MottleHttpRequest, artist_id: str) -> HttpResponse:
    artist = ArtistData.from_tekore_model(await request.spotify_client.get_artist(artist_id))

    event_artist = await track_artist_events(artist_id, artist.name, request.session["spotify_user_id"])
    await event_artist.update_events()

    events = [e async for e in event_artist.events.filter(date__gte=datetime.now(tz=UTC)).order_by("date")]
    return render(request, "web/events.html", context={"artist": artist, "events": events})


@require_http_methods(["GET", "POST"])
async def user_settings(request: MottleHttpRequest) -> HttpResponse:
    spotify_user = await SpotifyUser.objects.select_related("user").aget(
        spotify_id=request.session["spotify_user_spotify_id"]
    )
    user = spotify_user.user

    if request.method == "GET":
        template = "web/settings.html"

        if user.location:
            longitude, latitude = user.location
            radius = user.event_distance_threshold
        else:
            longitude, latitude, radius = None, None, None
    else:
        template = "web/parts/settings.html"

        latitude = float(lat) if (lat := request.POST.get("latitude")) else None
        longitude = float(lon) if (lon := request.POST.get("longitude")) else None
        radius = float(rad) if (rad := request.POST.get("radius")) else None

        user.playlist_notifications = bool(request.POST.get("playlist-notifications", False))
        user.release_notifications = bool(request.POST.get("release-notifications", False))
        user.event_notifications = bool(request.POST.get("event-notifications", False))

        if user.event_notifications:
            if all((latitude, longitude, radius)):
                user.location = Point(longitude, latitude, srid=settings.GEODJANGO_SRID)
                user.event_distance_threshold = radius
            else:
                return bad_request_toast("Location and radius must be provided for event notifications")

        await user.asave()

    response = render(
        request,
        template,
        context={
            "playlist_notifications": user.playlist_notifications,
            "release_notifications": user.release_notifications,
            "event_notifications": user.event_notifications,
            "latitude": latitude,
            "longitude": longitude,
            "radius": radius,
        },
    )

    if request.method == "GET":
        return response

    return toast(response, "success", "Settings updated successfully")


@require_GET
async def user_events(request: MottleHttpRequest) -> HttpResponse:
    spotify_user = await SpotifyUser.objects.select_related("user").aget(
        spotify_id=request.session["spotify_user_spotify_id"]
    )
    events_enabled = settings.EVENTS_ENABLED

    if not events_enabled:
        return bad_request_toast("Events are not available yet. Please check back later")

    user = spotify_user.user

    events = await user.upcoming_events()

    artist_ids = [a.artist.spotify_id for a in events]
    artists = await request.spotify_client.get_artists(artist_ids)
    artists = [ArtistData.from_tekore_model(a) for a in artists]

    events_data = dict(zip(artists, events.values(), strict=True))
    return render(request, "web/user_events.html", context={"upcoming_events": dict(sorted(events_data.items()))})


@require_GET
async def event_details(request: MottleHttpRequest, event_id: str) -> HttpResponse:
    event = await Event.objects.select_related("artist__artist").aget(id=event_id)
    artist = (await request.spotify_client.get_artist(event.artist.artist.spotify_id)).name

    return render(request, "web/modals/event_details.html", context={"artist": artist, "event": event})


@require_GET
async def saved_tracks(request: MottleHttpRequest) -> HttpResponse:
    tracks = await request.spotify_client.get_current_user_saved_tracks()
    tracks = [TrackData.from_tekore_model(t.track, added_at=t.added_at) for t in tracks]

    return render(request, "web/saved_tracks.html", context={"tracks": tracks})


@require_POST
async def remove_saved_tracks(request: MottleHttpRequest) -> HttpResponse:
    track_ids = request.POST.getlist("track-id")
    if not track_ids:
        return HttpResponseBadRequest("No tracks selected")

    await request.spotify_client.remove_user_saved_tracks(track_ids)

    return toast(HttpResponse(), "success", "Saved track(s) removed")
