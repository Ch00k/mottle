import logging
from typing import TYPE_CHECKING, Any

from asgiref.sync import sync_to_async
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django_htmx.http import trigger_client_event

from .models import Artist, EventArtist, EventUpdate, Playlist
from .spotify_items import get_smallest_image
from .utils import MottleException, MottleSpotifyClient

if TYPE_CHECKING:
    from .middleware import MottleHttpRequest

logger = logging.getLogger(__name__)


def toast(response: HttpResponse, toast_type: str, body: str) -> HttpResponse:
    """Return the response, with an HTMX trigger that shows the user a toast of the type ("success" or "error")."""
    return trigger_client_event(response, "HXToast", {"type": toast_type, "body": body})


# The request headers `wants_fragment` goes by, which a response it chose must vary on, so that a browser does not serve
# a cached fragment as the page at the same URL, or the page as the fragment
FRAGMENT_REQUEST_HEADERS = ("HX-Request", "HX-Boosted", "HX-History-Restore-Request")


def wants_fragment(request: MottleHttpRequest) -> bool:
    """Return whether the request is an HTMX one for part of a page, which a page's own control makes.

    A boosted link or form, and the request restoring a page htmx no longer has in its history cache, want the whole
    page.
    """
    return bool(request.htmx) and not request.htmx.boosted and not request.htmx.history_restore_request


def bad_request_toast(message: str) -> HttpResponse:
    """Return a bad request response with the message, which an error toast shows the user."""
    return toast(HttpResponseBadRequest(message), "error", message)


async def get_playlist_modal_response(request: MottleHttpRequest, playlist_id: str, template_path: str) -> HttpResponse:
    playlist = await request.spotify_client.get_playlist(playlist_id)

    try:
        playlists = await request.spotify_client.get_current_user_playlists()
    except MottleException as e:
        logger.exception(e)
        raise

    # TODO: This could probably be made more efficient by filtering playlist inside get_current_user_playlists
    playlists = [
        p for p in playlists if p.owner.id == request.session["spotify_user_spotify_id"] and p.id != playlist_id
    ]
    return render(
        request,
        template_path,
        context={
            "playlists": playlists,
            "playlist_id": playlist_id,
            "playlist_name": playlist.name,
            "playlist_image_url": get_smallest_image(playlist.images),
        },
    )


async def compile_event_updates_email(
    updates: dict[EventArtist, list[EventUpdate]], spotify_client: MottleSpotifyClient
) -> str:
    template_data = []

    for event_artist, event_updates in updates.items():
        artist_spotify_id = event_artist.artist.spotify_id
        spotify_artist = await spotify_client.get_artist(artist_spotify_id)

        updates_data = []
        for update in event_updates:
            event = update.event
            event_type_display = " ".join(event.type.split("_"))

            updates_data.append(
                {
                    "is_full": update.type == EventUpdate.FULL,
                    "event_type": event.type,
                    "event_type_display": event_type_display,
                    "event": event,
                    "changes": update.changes,
                    "has_stream_urls_change": update.changes and "stream_urls" in update.changes,
                    "has_tickets_urls_change": update.changes and "tickets_urls" in update.changes,
                }
            )

        template_data.append((spotify_artist.name, updates_data))

    html = await sync_to_async(render_to_string)("web/email/event_updates.html", {"updates": template_data})

    return html.strip()


async def compile_playlist_updates_email(
    updates: dict[str, list[dict[str, Any]]],
    spotify_client: MottleSpotifyClient,
    num_to_show: int = 10,
) -> str:
    template_data = []

    for playlist_spotify_id, playlist_updates in updates.items():
        playlist_data = await spotify_client.get_playlist(playlist_spotify_id)

        updates_data = []
        for update in playlist_updates:
            watched_playlist: Playlist | None = await sync_to_async(lambda: update["update"].source_playlist)()
            watched_artist: Artist | None = await sync_to_async(lambda: update["update"].source_artist)()

            update_info = {
                "auto_acceptable": update["auto_acceptable"],
                "auto_accept_successful": update["auto_accept_successful"],
                "playlist_updates_url": reverse("playlist_updates", args=[playlist_spotify_id]),
                "watched_playlist": None,
                "watched_artist": None,
                "tracks": [],
                "tracks_truncated": False,
                "tracks_remaining": 0,
                "albums": [],
                "albums_truncated": False,
                "albums_remaining": 0,
            }

            if watched_playlist is not None:
                watched_playlist_data = await spotify_client.get_playlist(watched_playlist.spotify_id)
                update_info["watched_playlist"] = {
                    "name": watched_playlist_data.name,
                    "owner_name": watched_playlist_data.owner.display_name,
                }

                tracks_data = await spotify_client.get_tracks(update["update"].tracks_added)
                for track_data in tracks_data[:num_to_show]:
                    artists_str = ", ".join([a.name for a in track_data.artists])
                    update_info["tracks"].append(
                        {
                            "name": track_data.name,
                            "artists": artists_str,
                        }
                    )

                if len(tracks_data) > num_to_show:
                    update_info["tracks_truncated"] = True
                    update_info["tracks_remaining"] = len(tracks_data) - num_to_show

            elif watched_artist is not None:
                watched_artist_data = await spotify_client.get_artist(watched_artist.spotify_id)
                update_info["watched_artist"] = {
                    "name": watched_artist_data.name,
                }

                albums_data = await spotify_client.get_albums(update["update"].albums_added)
                for album_data in albums_data[:num_to_show]:
                    update_info["albums"].append(
                        {
                            "name": album_data.name,
                            "release_date": album_data.release_date,
                        }
                    )

                if len(albums_data) > num_to_show:
                    update_info["albums_truncated"] = True
                    update_info["albums_remaining"] = len(albums_data) - num_to_show

            else:
                # XXX: This should never happen
                logger.error(f"PlaylistUpdate {update} has no source playlist or artist")
                continue

            updates_data.append(update_info)

        template_data.append((playlist_data.name, updates_data))

    html = await sync_to_async(render_to_string)("web/email/playlist_updates.html", {"updates": template_data})

    return html.strip()
