import datetime
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
import pytest
from django.conf import settings

from urlshortener.models import ShortURL
from web.events import data
from web.events.constants import BANDSINTOWN_BASE_URL, MUSICBRAINZ_API_BASE_URL, SONGKICK_BASE_URL
from web.events.data import Event, EventSourceArtist, MusicBrainzArtist, Venue
from web.events.enums import ArtistNameMatchAccuracy, EventDataSource, EventType
from web.events.exceptions import (
    BandsintownException,
    EventDataSourceException,
    HTTPClientException,
    SongkickException,
)
from web.events.http import AsyncRetryingClient
from web.models import Artist, EventArtist

if TYPE_CHECKING:
    from collections.abc import Callable

# ShortURL.shorten opens a transaction from asgiref's worker thread, which the in-memory test database refuses
# while the test's own transaction holds it on the main thread.
pytestmark = pytest.mark.django_db(transaction=True)

DATA_DIR = Path(__file__).parent / "data"
CALENDAR_PATH = "/artists/99952-deep-purple/calendar"
CONCERT_PATH = "/concerts/42898019-deep-purple-at-hamburg-sporthalle"
BANDSINTOWN_ARTIST_PATH = "/a/31005"
BANDSINTOWN_EVENT_PATH = "/e/1037189005-deep-purple-at-quarterback-immobilien-arena"


def songkick_site(request: httpx2.Request) -> httpx2.Response:
    """Serve the saved Songkick pages, and redirect an artist's former calendar URL to its current one."""
    pages = {
        CALENDAR_PATH: "songkick_artist_calendar.html",
        CONCERT_PATH: "songkick_concert.html",
    }

    if request.url.path == "/artists/1-deep-purple-old/calendar":
        return httpx2.Response(301, headers={"Location": f"{SONGKICK_BASE_URL}{CALENDAR_PATH}"})

    if request.url.path == "/artists/2-deep-purple-removed/calendar":
        return httpx2.Response(301, headers={"Location": f"{SONGKICK_BASE_URL}/search?query=deep+purple"})

    if request.url.path == "/search":
        return httpx2.Response(200, text="<html><body>Search results</body></html>")

    if request.url.path == "/artists/3-deep-purple-broken/calendar":
        calendar = (DATA_DIR / "songkick_artist_calendar.html").read_text()
        return httpx2.Response(200, text=calendar.replace('[{"@context"', '[{"@context', 1))

    page = pages.get(request.url.path)
    if page is None:
        return httpx2.Response(404)

    return httpx2.Response(200, text=(DATA_DIR / page).read_text())


def bandsintown_site(request: httpx2.Request) -> httpx2.Response:
    """Serve the saved Bandsintown artist and event pages."""
    pages = {
        BANDSINTOWN_ARTIST_PATH: "bandsintown_artist.html",
        BANDSINTOWN_EVENT_PATH: "bandsintown_event.html",
    }

    if request.url.path == "/a/2-deep-purple-no-events":
        artist = (DATA_DIR / "bandsintown_artist.html").read_text()
        events_start = artist.index('[{"@context"')
        return httpx2.Response(200, text=artist[:events_start] + "[]" + artist[artist.index("}]</script>") + 2 :])

    if request.url.path == "/a/3-deep-purple-no-event-list":
        artist = (DATA_DIR / "bandsintown_artist.html").read_text()
        return httpx2.Response(200, text=artist.replace('<script type="application/ld+json">[', "<script>[", 1))

    if request.url.path == "/a/4-deep-purple-festival":
        artist = (DATA_DIR / "bandsintown_artist.html").read_text()
        return httpx2.Response(200, text=artist.replace('"@type": "MusicEvent"', '"@type": "Festival"', 1))

    page = pages.get(request.url.path)
    if page is None:
        return httpx2.Response(404)

    return httpx2.Response(200, text=(DATA_DIR / page).read_text())


@pytest.fixture
def bandsintown(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AsyncRetryingClient(
        base_url=BANDSINTOWN_BASE_URL,
        transport=httpx2.MockTransport(bandsintown_site),
    )
    monkeypatch.setattr(data, "async_bandsintown_client", client)


@pytest.fixture
def songkick(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AsyncRetryingClient(
        base_url=SONGKICK_BASE_URL,
        transport=httpx2.MockTransport(songkick_site),
    )
    monkeypatch.setattr(data, "async_songkick_client", client)


@pytest.mark.asyncio
@pytest.mark.usefixtures("songkick")
async def test_fetch_events_reads_upcoming_songkick_events() -> None:
    """The artist's upcoming concerts are read from the calendar page, with the venue and ticket links of each.

    The calendar page also lists past concerts, which must not be picked up.
    """
    artist = EventSourceArtist(name="Deep Purple", songkick_url=f"{SONGKICK_BASE_URL}{CALENDAR_PATH}")

    await artist.fetch_events()

    assert len(artist.events) == 1
    event = artist.events[0]
    assert event.source == EventDataSource.songkick
    assert event.url == f"{SONGKICK_BASE_URL}{CONCERT_PATH}"
    assert event.type == EventType.concert
    assert event.date == datetime.date(2026, 11, 1)
    assert event.venue == Venue(
        name="Hamburg Sporthalle",
        postcode="22297",
        address="Krochmannstraße 55",
        city="Hamburg",
        country="Germany",
        geo_lat=53.60254,
        geo_lon=9.99859,
    )
    assert event.stream_urls == []

    short_urls = [s async for s in ShortURL.objects.order_by("url")]
    assert [s.url for s in short_urls] == [
        f"{SONGKICK_BASE_URL}/tickets/33601226",
        f"{SONGKICK_BASE_URL}/tickets/33602496",
    ]
    assert sorted(event.tickets_urls) == sorted(f"{settings.URLSHORTENER_BASE_URL}/{s.hash}" for s in short_urls)


@pytest.mark.asyncio
@pytest.mark.usefixtures("songkick")
async def test_fetch_events_follows_moved_songkick_calendar() -> None:
    """A calendar URL Songkick has moved is followed to the current one, which replaces the artist's URL."""
    artist = EventSourceArtist(
        name="Deep Purple", songkick_url=f"{SONGKICK_BASE_URL}/artists/1-deep-purple-old/calendar"
    )

    await artist.fetch_events()

    assert [e.url for e in artist.events] == [f"{SONGKICK_BASE_URL}{CONCERT_PATH}"]
    assert artist.songkick_url == f"{SONGKICK_BASE_URL}{CALENDAR_PATH}"


@pytest.mark.asyncio
@pytest.mark.usefixtures("songkick")
async def test_update_events_stores_events_and_moved_songkick_calendar() -> None:
    """Updating an artist's events saves the fetched events and the calendar URL Songkick has moved it to."""
    event_artist = await EventArtist.objects.acreate(
        artist=await Artist.objects.acreate(spotify_id="deep_purple"),
        songkick_url=f"{SONGKICK_BASE_URL}/artists/1-deep-purple-old/calendar",
        songkick_name_match_accuracy=ArtistNameMatchAccuracy.exact,
        bandsintown_name_match_accuracy=ArtistNameMatchAccuracy.no_match,
    )

    await event_artist.update_events()

    await event_artist.arefresh_from_db()
    assert event_artist.songkick_url == f"{SONGKICK_BASE_URL}{CALENDAR_PATH}"
    assert [e.source_url async for e in event_artist.events.all()] == [f"{SONGKICK_BASE_URL}{CONCERT_PATH}"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("songkick")
async def test_fetch_events_keeps_songkick_events_when_bandsintown_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Bandsintown refusing every request does not lose the events already read from Songkick.

    The failure is logged as an error naming the Bandsintown page that could not be read.
    """
    bandsintown = AsyncRetryingClient(
        attempts=2,
        wait_initial=0,
        wait_max=0,
        retry_statuses=frozenset({403}),
        base_url=BANDSINTOWN_BASE_URL,
        transport=httpx2.MockTransport(lambda _: httpx2.Response(403, text="Just a moment...")),
    )
    monkeypatch.setattr(data, "async_bandsintown_client", bandsintown)
    artist = EventSourceArtist(
        name="Deep Purple",
        songkick_url=f"{SONGKICK_BASE_URL}{CALENDAR_PATH}",
        bandsintown_url=f"{BANDSINTOWN_BASE_URL}{BANDSINTOWN_ARTIST_PATH}",
    )

    await artist.fetch_events()

    assert [e.url for e in artist.events] == [f"{SONGKICK_BASE_URL}{CONCERT_PATH}"]
    errors = [r for r in caplog.records if r.name == "web.events.data" and r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert (
        errors[0]
        .getMessage()
        .startswith(f"Failed to fetch data from {BANDSINTOWN_BASE_URL}{BANDSINTOWN_ARTIST_PATH}: ")
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("bandsintown")
async def test_fetch_events_reads_upcoming_bandsintown_events() -> None:
    """The artist's upcoming concerts are read from the artist page, with the venue and ticket links of each.

    The artist page leaves out the venue's address, so the venue is read from the event page, along with the tickets.
    """
    artist = EventSourceArtist(name="Deep Purple", bandsintown_url=f"{BANDSINTOWN_BASE_URL}{BANDSINTOWN_ARTIST_PATH}")

    await artist.fetch_events()

    assert len(artist.events) == 1
    event = artist.events[0]
    assert event.source == EventDataSource.bandsintown
    assert event.url == f"{BANDSINTOWN_BASE_URL}{BANDSINTOWN_EVENT_PATH}"
    assert event.type == EventType.concert
    assert event.date == datetime.date(2026, 10, 31)
    assert event.venue == Venue(
        name="QUARTERBACK Immobilien ARENA",
        postcode="04105",
        address="Am Sportforum 2",
        city="Leipzig",
        country="Germany",
        geo_lat=51.33962,
        geo_lon=12.37129,
    )
    assert event.stream_urls == []
    assert artist.failed_sources == []

    short_urls = [s async for s in ShortURL.objects.all()]
    assert sorted(s.url for s in short_urls) == sorted(
        [
            (
                "https://www.awin1.com/cread.php?awinmid=11388&awinaffid=97249&p=https%3A%2F%2Fwww.eventim.de%2Fnoapp"
                "%2Fevent%2F20959741%2F%3Faffiliate%3DBNO&clickref=%24%7Bcame_from%7D~%24%7Bevent_id%7D~~%24%7Bapp_id"
                "%7D~%24%7Bclick_id%7D&direct=true"
            ),
            (
                "https://www.awin1.com/cread.php?awinmid=18490&awinaffid=97249&p=https%3A%2F%2Fwww.adticket.de%2Ftickets"
                "--%2Fe2485976%3FaffiliateId%3Df5c587f89cca20b5fb59da79e27e1cc49fab748d3a2d896d93835399ddf5567a&clickref"
                "=%24%7Bcame_from%7D~%24%7Bevent_id%7D~~%24%7Bapp_id%7D~%24%7Bclick_id%7D&direct=true"
            ),
            (
                "https://myticmyticketag.sjv.io/c/379560/3068482/25111?u=https%3A%2F%2Fshop.myticket.de%2Fselection%2Fevent"
                "%2Fseat%3FperfId%3D10229545411455%26table%3Dfalse%26choiceSelectionDone%3Dfalse%26productId"
                "%3D10229545411347%26tourId%3D10229541373053&direct=true"
            ),
        ]
    )
    assert sorted(event.tickets_urls) == sorted(f"{settings.URLSHORTENER_BASE_URL}/{s.hash}" for s in short_urls)


@pytest.mark.asyncio
@pytest.mark.usefixtures("bandsintown")
async def test_fetch_events_reads_no_bandsintown_events_from_an_empty_list() -> None:
    """An artist with no upcoming concerts has an empty list of events in the page, which is not a failure."""
    artist = EventSourceArtist(name="Deep Purple", bandsintown_url=f"{BANDSINTOWN_BASE_URL}/a/2-deep-purple-no-events")

    await artist.fetch_events()

    assert artist.events == []
    assert artist.failed_sources == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("bandsintown")
async def test_fetch_events_fails_bandsintown_without_a_list_of_events(caplog: pytest.LogCaptureFixture) -> None:
    """An artist page whose head has no list of events fails the source, so a markup change does not go unnoticed.

    The failure is logged as an error naming the page and what was missing from it.
    """
    bandsintown_url = f"{BANDSINTOWN_BASE_URL}/a/3-deep-purple-no-event-list"
    artist = EventSourceArtist(name="Deep Purple", bandsintown_url=bandsintown_url)

    await artist.fetch_events()

    assert artist.events == []
    assert artist.failed_sources == [EventDataSource.bandsintown]
    errors = [r for r in caplog.records if r.name == "web.events.data" and r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == [
        f"Expected one list of events in the JSON-LD on {bandsintown_url}, found 0"
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("bandsintown")
async def test_fetch_events_fails_bandsintown_on_items_other_than_events(caplog: pytest.LogCaptureFixture) -> None:
    """A list of events holding anything but MusicEvent items fails the source, so a change of type is noticed.

    Dropping such items would make the artist look as if it had no upcoming events. The failure is logged as an error
    naming the page and the types found.
    """
    bandsintown_url = f"{BANDSINTOWN_BASE_URL}/a/4-deep-purple-festival"
    artist = EventSourceArtist(name="Deep Purple", bandsintown_url=bandsintown_url)

    await artist.fetch_events()

    assert artist.events == []
    assert artist.failed_sources == [EventDataSource.bandsintown]
    errors = [r for r in caplog.records if r.name == "web.events.data" and r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == [
        f"Expected only MusicEvent items in the list of events on {bandsintown_url}: ['Festival']"
    ]


@pytest.mark.asyncio
async def test_find_in_bandsintown_tries_every_name_when_search_is_refused(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A search Bandsintown refuses moves on to the artist's next name, and fails once every name has been tried.

    Each refused search is logged as an error naming the name searched for.
    """
    monkeypatch.setattr(
        data,
        "async_bandsintown_client",
        AsyncRetryingClient(
            attempts=1,
            base_url=BANDSINTOWN_BASE_URL,
            transport=httpx2.MockTransport(lambda _: httpx2.Response(403, text="Just a moment...")),
        ),
    )
    artist = EventSourceArtist(name="Deep Purple", alternative_names=["Deep Purple Mk II"])

    with pytest.raises(
        BandsintownException, match=r"^Artist 'Deep Purple' not found in Bandsintown using any of the names$"
    ):
        await artist.find_in_bandsintown()

    assert artist.bandsintown_url is None
    errors = [r.getMessage() for r in caplog.records if r.name == "web.events.data" and r.levelno == logging.ERROR]
    assert len(errors) == 2
    assert errors[0].startswith(
        "Failed to search for artist in Bandsintown by name 'Deep Purple': "
        "Failed to search Bandsintown for 'Deep Purple': "
    )
    assert errors[1].startswith(
        "Failed to search for artist in Bandsintown by name 'Deep Purple Mk II': "
        "Failed to search Bandsintown for 'Deep Purple Mk II': "
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("songkick")
async def test_fetch_events_rejects_songkick_redirect_away_from_a_calendar(caplog: pytest.LogCaptureFixture) -> None:
    """A calendar URL Songkick redirects to a page that is not an artist calendar is kept, and the failure logged.

    Storing the new URL would point the artist at a page with no events for good.
    """
    calendar_url = f"{SONGKICK_BASE_URL}/artists/2-deep-purple-removed/calendar"
    artist = EventSourceArtist(name="Deep Purple", songkick_url=calendar_url)

    await artist.fetch_events()

    assert artist.events == []
    assert artist.songkick_url == calendar_url
    assert artist.failed_sources == [EventDataSource.songkick]
    errors = [r for r in caplog.records if r.name == "web.events.data" and r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == [
        (
            f"Songkick redirected {calendar_url} to {SONGKICK_BASE_URL}/search?query=deep+purple, which is not an "
            "artist calendar"
        )
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("songkick", "bandsintown")
async def test_fetch_events_keeps_bandsintown_events_when_songkick_markup_breaks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Songkick page that cannot be parsed does not lose the events read from Bandsintown.

    The calendar's event data is broken JSON, which fails outside the HTTP client's own error handling.
    """
    artist = EventSourceArtist(
        name="Deep Purple",
        songkick_url=f"{SONGKICK_BASE_URL}/artists/3-deep-purple-broken/calendar",
        bandsintown_url=f"{BANDSINTOWN_BASE_URL}{BANDSINTOWN_ARTIST_PATH}",
    )

    await artist.fetch_events()

    assert len(artist.events) == 1
    event = artist.events[0]
    assert event.source == EventDataSource.bandsintown
    assert event.url == f"{BANDSINTOWN_BASE_URL}{BANDSINTOWN_EVENT_PATH}"
    assert event.date == datetime.date(2026, 10, 31)
    assert event.venue is not None
    assert event.venue.name == "QUARTERBACK Immobilien ARENA"
    assert len(event.tickets_urls) == 3
    assert artist.failed_sources == [EventDataSource.songkick]
    errors = [r for r in caplog.records if r.name == "web.events.data" and r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert isinstance(errors[0].exc_info, tuple)
    assert isinstance(errors[0].exc_info[1], json.JSONDecodeError)


@pytest.mark.asyncio
@pytest.mark.usefixtures("songkick")
async def test_update_events_saves_events_then_reports_failed_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed source still lets the other source's events be saved, then fails the update so the task counts it."""
    monkeypatch.setattr(
        data,
        "async_bandsintown_client",
        AsyncRetryingClient(
            attempts=1,
            base_url=BANDSINTOWN_BASE_URL,
            transport=httpx2.MockTransport(lambda _: httpx2.Response(403, text="Just a moment...")),
        ),
    )
    event_artist = await EventArtist.objects.acreate(
        artist=await Artist.objects.acreate(spotify_id="deep_purple"),
        songkick_url=f"{SONGKICK_BASE_URL}{CALENDAR_PATH}",
        bandsintown_url=f"{BANDSINTOWN_BASE_URL}{BANDSINTOWN_ARTIST_PATH}",
        songkick_name_match_accuracy=ArtistNameMatchAccuracy.exact,
        bandsintown_name_match_accuracy=ArtistNameMatchAccuracy.exact,
    )

    with pytest.raises(EventDataSourceException, match=r"^Failed to fetch events from bandsintown$"):
        await event_artist.update_events()

    assert [e.source_url async for e in event_artist.events.all()] == [f"{SONGKICK_BASE_URL}{CONCERT_PATH}"]


MUSICBRAINZ_ARTIST_ID = "79239441-bfd5-4981-a70c-55c3f15c1287"
MUSICBRAINZ_STALE_ARTIST_ID = "b2f1b5a4-6a0e-4c1c-9b8e-2f8d7c3e4a51"
BANDSINTOWN_REDIRECTING_PATH = "/a/738"
BANDSINTOWN_REMOVED_PATH = "/a/404"


def musicbrainz_artist(name: str, bandsintown_path: str) -> dict:
    return {
        "name": name,
        "aliases": [{"name": "DP", "primary": True}],
        "relations": [
            {"type": "songkick", "url": {"resource": "https://www.songkick.com/artists/99952-deep-purple"}},
            {"type": "bandsintown", "url": {"resource": f"{BANDSINTOWN_BASE_URL}{bandsintown_path}"}},
        ],
    }


def musicbrainz_site(request: httpx2.Request) -> httpx2.Response:
    """Answers MusicBrainz's API: a Spotify URL lookup that finds nothing, a name search and two artists.

    Both artists link to Deep Purple's Songkick page. One links to a Bandsintown URL that redirects to the artist, the
    other to one Bandsintown has removed.
    """
    path = request.url.path.removeprefix("/ws/2/")
    if path == "url":
        return httpx2.Response(200, json={"relations": []})
    if path == "artist":
        found = request.url.params["query"] == "Deep Purple"
        artists = [{"id": MUSICBRAINZ_ARTIST_ID, "name": "Deep Purple"}] if found else []
        return httpx2.Response(200, json={"artists": artists})
    if path == f"artist/{MUSICBRAINZ_ARTIST_ID}":
        return httpx2.Response(200, json=musicbrainz_artist("Deep Purple", BANDSINTOWN_REDIRECTING_PATH))
    if path == f"artist/{MUSICBRAINZ_STALE_ARTIST_ID}":
        return httpx2.Response(200, json=musicbrainz_artist("Deep Purple", BANDSINTOWN_REMOVED_PATH))
    return httpx2.Response(404)


def songkick_site_with_artist_ids(request: httpx2.Request) -> httpx2.Response:
    """The saved Songkick pages, where a calendar URL with only the artist's ID redirects to the artist's calendar."""
    if request.url.path == "/artists/99952/calendar":
        return httpx2.Response(301, headers={"Location": f"{SONGKICK_BASE_URL}{CALENDAR_PATH}"})
    return songkick_site(request)


def bandsintown_site_with_redirect(request: httpx2.Request) -> httpx2.Response:
    if request.url.path == BANDSINTOWN_REDIRECTING_PATH:
        return httpx2.Response(301, headers={"Location": f"{BANDSINTOWN_BASE_URL}{BANDSINTOWN_ARTIST_PATH}"})
    return bandsintown_site(request)


@pytest.fixture
def musicbrainz(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, transport, base_url in (
        ("async_musicbrainz_client", httpx2.MockTransport(musicbrainz_site), MUSICBRAINZ_API_BASE_URL),
        ("async_songkick_client", httpx2.MockTransport(songkick_site_with_artist_ids), SONGKICK_BASE_URL),
        ("async_bandsintown_client", httpx2.MockTransport(bandsintown_site_with_redirect), BANDSINTOWN_BASE_URL),
    ):
        monkeypatch.setattr(data, name, AsyncRetryingClient(attempts=1, base_url=base_url, transport=transport))


@pytest.mark.asyncio
@pytest.mark.usefixtures("musicbrainz")
async def test_musicbrainz_artist_found_by_name_leads_to_its_events() -> None:
    """An artist MusicBrainz does not link to its Spotify page is found by name, and its links lead to its events.

    MusicBrainz links to the artist's Songkick page, which becomes the artist's calendar, and to a Bandsintown URL
    that redirects, which becomes where it redirects to. Events are then read from both.
    """
    musicbrainz_artist = await MusicBrainzArtist.find("spotify_artist_id", "Deep Purple")

    assert musicbrainz_artist == MusicBrainzArtist(
        id=MUSICBRAINZ_ARTIST_ID,
        names=["Deep Purple"],
        songkick_url=f"{SONGKICK_BASE_URL}{CALENDAR_PATH}",
        bandsintown_url=f"{BANDSINTOWN_BASE_URL}{BANDSINTOWN_ARTIST_PATH}",
    )

    artist = EventSourceArtist(
        name="Deep Purple",
        songkick_url=musicbrainz_artist.songkick_url,
        bandsintown_url=musicbrainz_artist.bandsintown_url,
    )
    await artist.fetch_events()

    assert artist.failed_sources == []
    assert {event.source for event in artist.events} == {EventDataSource.songkick, EventDataSource.bandsintown}


@pytest.mark.asyncio
@pytest.mark.usefixtures("musicbrainz")
async def test_musicbrainz_artist_keeps_match_without_stale_link(caplog: pytest.LogCaptureFixture) -> None:
    """A link MusicBrainz has to a removed Bandsintown page is dropped, and the rest of the match is kept.

    With one source missing, the artist's names and aliases come along for the name search to find it.
    """
    with caplog.at_level(logging.WARNING, logger="web.events.data"):
        musicbrainz_artist = await MusicBrainzArtist.from_artist_id(MUSICBRAINZ_STALE_ARTIST_ID)

    assert musicbrainz_artist == MusicBrainzArtist(
        id=MUSICBRAINZ_STALE_ARTIST_ID,
        names=["Deep Purple", "DP"],
        songkick_url=f"{SONGKICK_BASE_URL}{CALENDAR_PATH}",
        bandsintown_url=None,
    )
    assert [r.getMessage().split(":")[0] for r in caplog.records] == [
        f"Failed to follow the Bandsintown URL of MusicBrainz artist {MUSICBRAINZ_STALE_ARTIST_ID}"
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("musicbrainz")
async def test_musicbrainz_finds_no_artist_for_unknown_name() -> None:
    """A name MusicBrainz has no artist for finds nothing."""
    assert await MusicBrainzArtist.find_by_name("Deep Purple Mk II") is None


REMOVED_SONGKICK_ARTIST_ID = 404


def install_event_source_search(
    monkeypatch: pytest.MonkeyPatch, artists: list[tuple[int, str]], refused_names: tuple[str, ...] = ()
) -> None:
    """Point both event sources at searches that find the given artists for any name but the refused ones.

    A Songkick calendar URL with only the artist's ID redirects to the artist's calendar, but for an artist Songkick
    has removed.
    """

    def songkick_site(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/universal_search":
            if request.url.params["query"] in refused_names:
                return httpx2.Response(403)

            documents = [{"document": {"primary_key_id": id_, "name": name}} for id_, name in artists]
            return httpx2.Response(200, json={"data": {"attributes": {"search_results": {"artists": documents}}}})

        if match := re.fullmatch(r"/artists/(\d+)/calendar", request.url.path):
            if int(match[1]) == REMOVED_SONGKICK_ARTIST_ID:
                return httpx2.Response(404)

            return httpx2.Response(301, headers={"Location": f"{SONGKICK_BASE_URL}/artists/{match[1]}-found/calendar"})

        return httpx2.Response(404)

    def bandsintown_site(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/searchSuggestions":
            if request.url.params["searchTerm"] in refused_names:
                return httpx2.Response(403)

            return httpx2.Response(200, json={"artists": [{"id": id_, "name": name} for id_, name in artists]})

        return httpx2.Response(404)

    for name, site, base_url in (
        ("async_songkick_client", songkick_site, SONGKICK_BASE_URL),
        ("async_bandsintown_client", bandsintown_site, BANDSINTOWN_BASE_URL),
    ):
        client = AsyncRetryingClient(attempts=1, base_url=base_url, transport=httpx2.MockTransport(site))
        monkeypatch.setattr(data, name, client)


FIND_ARTIST_IN_SOURCES = [data.find_artist_in_songkick, data.find_artist_in_bandsintown]


@pytest.mark.asyncio
@pytest.mark.parametrize("find_artist", FIND_ARTIST_IN_SOURCES)
async def test_find_artist_picks_artist_with_searched_name(
    monkeypatch: pytest.MonkeyPatch, find_artist: Callable
) -> None:
    """Of the artists a source's search finds, the one with the searched name is picked, with how well it matched."""
    install_event_source_search(monkeypatch, [(1, "Deep Purple Tribute"), (99952, "deep purple")])

    assert await find_artist("Deep Purple") == ("99952", ArtistNameMatchAccuracy.exact)


@pytest.mark.asyncio
@pytest.mark.parametrize("find_artist", FIND_ARTIST_IN_SOURCES)
async def test_find_artist_finds_nothing_when_search_finds_nothing(
    monkeypatch: pytest.MonkeyPatch, find_artist: Callable
) -> None:
    """A search that finds no artists finds no match."""
    install_event_source_search(monkeypatch, [])

    assert await find_artist("Deep Purple") == (None, ArtistNameMatchAccuracy.no_match)


@pytest.mark.asyncio
@pytest.mark.parametrize("find_artist", FIND_ARTIST_IN_SOURCES)
async def test_find_artist_matches_similar_name_only_with_advanced_heuristics(
    monkeypatch: pytest.MonkeyPatch, find_artist: Callable
) -> None:
    """An artist whose name is only similar to the searched one is matched by the advanced heuristics alone."""
    install_event_source_search(monkeypatch, [(7, "The Deep Purple")])

    assert await find_artist("Deep Purple") == (None, ArtistNameMatchAccuracy.no_match)
    assert await find_artist("Deep Purple", use_advanced_heuristics=True) == ("7", ArtistNameMatchAccuracy.fuzzy)


@pytest.mark.asyncio
async def test_find_artist_in_songkick_fails_on_refused_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """A search Songkick refuses fails with the request that was refused."""
    install_event_source_search(monkeypatch, [], refused_names=("Deep Purple",))

    with pytest.raises(
        HTTPClientException, match=r"^Failed to send GET request to api/universal_search\?query=Deep Purple: "
    ):
        await data.find_artist_in_songkick("Deep Purple")


@pytest.mark.asyncio
async def test_find_artist_in_bandsintown_fails_on_refused_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """A search Bandsintown refuses fails with the name searched for."""
    install_event_source_search(monkeypatch, [], refused_names=("Deep Purple",))

    with pytest.raises(BandsintownException, match=r"^Failed to search Bandsintown for 'Deep Purple': "):
        await data.find_artist_in_bandsintown("Deep Purple")


@pytest.mark.asyncio
async def test_find_in_songkick_tries_names_until_one_is_found(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The artist's names are searched for in turn until one is found, whose calendar and match the artist gets.

    A name that finds nothing is logged as a warning.
    """
    install_event_source_search(monkeypatch, [(99952, "Deep Purple Mk II")])
    artist = EventSourceArtist(name="Deep Purple", alternative_names=["Deep Purple Mk II"])

    with caplog.at_level(logging.WARNING, logger="web.events.data"):
        await artist.find_in_songkick()

    assert artist.songkick_url == f"{SONGKICK_BASE_URL}/artists/99952-found/calendar"
    assert artist.songkick_match_accuracy == ArtistNameMatchAccuracy.exact
    assert [r.getMessage() for r in caplog.records] == [
        "Could not find artist by name 'Deep Purple' in Songkick. Trying next name"
    ]


@pytest.mark.asyncio
async def test_find_in_songkick_tries_every_name_when_search_is_refused(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A search Songkick refuses moves on to the artist's next name, and fails once every name has been tried.

    Each refused search is logged as an error naming the name searched for.
    """
    install_event_source_search(monkeypatch, [], refused_names=("Deep Purple", "Deep Purple Mk II"))
    artist = EventSourceArtist(name="Deep Purple", alternative_names=["Deep Purple Mk II"])

    with pytest.raises(SongkickException, match=r"^Artist 'Deep Purple' not found in Songkick using any of the names$"):
        await artist.find_in_songkick()

    assert artist.songkick_url is None
    errors = [r.getMessage() for r in caplog.records if r.name == "web.events.data" and r.levelno == logging.ERROR]
    assert len(errors) == 2
    assert errors[0].startswith(
        "Failed to search for artist in Songkick by name 'Deep Purple': "
        "Failed to send GET request to api/universal_search?query=Deep Purple: "
    )
    assert errors[1].startswith(
        "Failed to search for artist in Songkick by name 'Deep Purple Mk II': "
        "Failed to send GET request to api/universal_search?query=Deep Purple Mk II: "
    )


@pytest.mark.asyncio
async def test_find_in_songkick_fails_without_artist_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """An artist found whose calendar Songkick does not have is not found."""
    install_event_source_search(monkeypatch, [(REMOVED_SONGKICK_ARTIST_ID, "Deep Purple")])
    artist = EventSourceArtist(name="Deep Purple")

    with pytest.raises(SongkickException, match=r"^Failed to fetch artist URL for 'Deep Purple': "):
        await artist.find_in_songkick()

    assert artist.songkick_url is None


@pytest.mark.asyncio
async def test_find_in_bandsintown_gives_artist_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """The artist found in Bandsintown gets the artist's page and how well the name matched."""
    install_event_source_search(monkeypatch, [(31005, "Deep Purple")])
    artist = EventSourceArtist(name="Deep Purple")

    await artist.find_in_bandsintown()

    assert artist.bandsintown_url == f"{BANDSINTOWN_BASE_URL}{BANDSINTOWN_ARTIST_PATH}"
    assert artist.bandsintown_match_accuracy == ArtistNameMatchAccuracy.exact


@pytest.mark.asyncio
async def test_find_searches_both_sources_by_every_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """The artist is searched for in both sources, by its name and the alternative names other than its name."""
    install_event_source_search(monkeypatch, [(31005, "DP")])

    artist = await EventSourceArtist.find("Deep Purple", ["Deep Purple", "DP"])

    assert artist.alternative_names == ["DP"]
    assert artist.songkick_url == f"{SONGKICK_BASE_URL}/artists/31005-found/calendar"
    assert artist.songkick_match_accuracy == ArtistNameMatchAccuracy.exact
    assert artist.bandsintown_url == f"{BANDSINTOWN_BASE_URL}{BANDSINTOWN_ARTIST_PATH}"
    assert artist.bandsintown_match_accuracy == ArtistNameMatchAccuracy.exact


@pytest.mark.asyncio
async def test_find_uses_advanced_heuristics_in_both_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """The advanced heuristics, when asked for, match a similar name in both sources."""
    install_event_source_search(monkeypatch, [(31005, "The Deep Purple")])

    artist = await EventSourceArtist.find("Deep Purple", [], use_advanced_heuristics=True)

    assert artist.songkick_match_accuracy == ArtistNameMatchAccuracy.fuzzy
    assert artist.bandsintown_match_accuracy == ArtistNameMatchAccuracy.fuzzy


@pytest.mark.asyncio
async def test_find_leaves_artist_without_sources_when_not_found(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An artist found in neither source has no URLs and no match, and each source's failure is logged as a warning."""
    install_event_source_search(monkeypatch, [])

    with caplog.at_level(logging.WARNING, logger="web.events.data"):
        artist = await EventSourceArtist.find("Deep Purple", [])

    assert artist.songkick_url is None
    assert artist.songkick_match_accuracy == ArtistNameMatchAccuracy.no_match
    assert artist.bandsintown_url is None
    assert artist.bandsintown_match_accuracy == ArtistNameMatchAccuracy.no_match
    assert sorted(r.getMessage() for r in caplog.records) == [
        "Artist 'Deep Purple' not found in Bandsintown using any of the names",
        "Artist 'Deep Purple' not found in Songkick using any of the names",
        "Could not find artist by name 'Deep Purple' in Bandsintown. Trying next name",
        "Could not find artist by name 'Deep Purple' in Songkick. Trying next name",
    ]


def install_page(monkeypatch: pytest.MonkeyPatch, client_name: str, base_url: str, page: str) -> None:
    """Point the event source's client at a site that serves the page at every URL."""
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, text=page))
    monkeypatch.setattr(data, client_name, AsyncRetryingClient(attempts=1, base_url=base_url, transport=transport))


SONGKICK_LIVE_STREAM_URL = f"{SONGKICK_BASE_URL}/live-stream-concerts/1-deep-purple-online"


@pytest.mark.asyncio
async def test_extract_songkick_live_stream_reads_stream_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Songkick live stream has no venue, and its stream links are read from its page and shortened."""
    install_page(
        monkeypatch,
        "async_songkick_client",
        SONGKICK_BASE_URL,
        '<html><body><div class="live-stream-link"><a href="https://stream.example/deep-purple?ref=sk">Watch</a></div>'
        "</body></html>",
    )

    event = await data.extract_songkick_event(
        {
            "url": f"{SONGKICK_LIVE_STREAM_URL}?utm_source=microformat",
            "startDate": "2026-12-01T20:00:00",
            "location": {"@type": "VirtualLocation", "name": "Online"},
        }
    )

    short_url = await ShortURL.objects.aget(url="https://stream.example/deep-purple")
    assert event == Event(
        source=EventDataSource.songkick,
        url=SONGKICK_LIVE_STREAM_URL,
        type=EventType.live_stream,
        date=datetime.date(2026, 12, 1),
        venue=None,
        stream_urls=[short_url.full_short_url],
        tickets_urls=[],
    )


@pytest.mark.asyncio
async def test_extract_songkick_concert_reads_partial_venue(monkeypatch: pytest.MonkeyPatch) -> None:
    """A venue's missing details are left out, and its country gets the short name."""
    install_page(monkeypatch, "async_songkick_client", SONGKICK_BASE_URL, "<html><body></body></html>")

    event = await data.extract_songkick_event(
        {
            "url": f"{SONGKICK_BASE_URL}{CONCERT_PATH}",
            "startDate": "2026-11-01T19:00:00",
            "location": {"name": "Royal Albert Hall", "address": {"addressCountry": "UK", "addressLocality": "London"}},
        }
    )

    assert event.venue == Venue(name="Royal Albert Hall", city="London", country="United Kingdom")
    assert event.tickets_urls == []


@pytest.mark.asyncio
async def test_extract_songkick_concert_without_venue_name_has_no_venue(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A concert whose venue has no name gets no venue, which is logged as a warning, and keeps its tickets."""
    install_page(
        monkeypatch, "async_songkick_client", SONGKICK_BASE_URL, (DATA_DIR / "songkick_concert.html").read_text()
    )
    location = {"address": {"addressCountry": "Germany"}}

    with caplog.at_level(logging.WARNING, logger="web.events.data"):
        event = await data.extract_songkick_event(
            {"url": f"{SONGKICK_BASE_URL}{CONCERT_PATH}", "startDate": "2026-11-01T19:00:00", "location": location}
        )

    assert event.venue is None
    assert len(event.tickets_urls) == 2
    assert [r.getMessage() for r in caplog.records] == [f"Venue data is incomplete: {location}. Setting Venue to None"]


@pytest.mark.asyncio
async def test_extract_songkick_event_rejects_url_of_no_event() -> None:
    """A URL that is not one of a concert, a festival or a live stream fails the event."""
    url = f"{SONGKICK_BASE_URL}/venues/1-hamburg-sporthalle"

    with pytest.raises(SongkickException, match=rf"^Event URL does not match pattern: {url}$"):
        await data.extract_songkick_event({"url": url, "startDate": "2026-11-01T19:00:00", "location": {}})


BANDSINTOWN_ONLINE_EVENT_URL = f"{BANDSINTOWN_BASE_URL}/e/1-deep-purple-online"
BANDSINTOWN_ONLINE_EVENT_DATA = {
    "url": f"{BANDSINTOWN_ONLINE_EVENT_URL}?came_from=257",
    "startDate": "2026-12-01T20:00:00",
}
BANDSINTOWN_PLACE = {
    "@type": "Place",
    "name": "Royal Albert Hall",
    "address": {"addressCountry": "UK", "addressLocality": "London"},
}


def bandsintown_event_page(window_data: dict) -> str:
    return f"<html><head><script>window.__data={json.dumps(window_data)}</script></head><body></body></html>"


def bandsintown_window_data(location: dict, event_view_body: dict) -> dict:
    return {
        "eventView": {"body": event_view_body},
        "jsonLdContainer": {"eventJsonLd": {"location": location}},
    }


def install_bandsintown_event_page(monkeypatch: pytest.MonkeyPatch, page: str) -> None:
    install_page(monkeypatch, "async_bandsintown_client", BANDSINTOWN_BASE_URL, page)


@pytest.mark.asyncio
async def test_extract_bandsintown_live_stream_reads_stream_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """An event at a virtual location is a live stream with no venue, whose stream link is shortened."""
    window_data = bandsintown_window_data(
        {"@type": "VirtualLocation"}, {"hybridEventDetails": {"streamingUrl": "https://stream.example/deep-purple"}}
    )
    install_bandsintown_event_page(monkeypatch, bandsintown_event_page(window_data))

    event = await data.extract_bandsintown_event(BANDSINTOWN_ONLINE_EVENT_DATA)

    short_url = await ShortURL.objects.aget(url="https://stream.example/deep-purple")
    assert event == Event(
        source=EventDataSource.bandsintown,
        url=BANDSINTOWN_ONLINE_EVENT_URL,
        type=EventType.live_stream,
        date=datetime.date(2026, 12, 1),
        venue=None,
        stream_urls=[short_url.full_short_url],
        tickets_urls=[],
    )


@pytest.mark.asyncio
async def test_extract_bandsintown_live_stream_without_stream_link(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A live stream without a stream link gets none, which is logged as a warning."""
    window_data = bandsintown_window_data({"@type": "VirtualLocation"}, {"hybridEventDetails": {"streamingUrl": None}})
    install_bandsintown_event_page(monkeypatch, bandsintown_event_page(window_data))

    with caplog.at_level(logging.WARNING, logger="web.events.data"):
        event = await data.extract_bandsintown_event(BANDSINTOWN_ONLINE_EVENT_DATA)

    assert event.stream_urls == []
    assert [r.getMessage() for r in caplog.records] == [
        (
            "eventView.body.hybridEventDetails.streamingUrl key not found in 'window.__data' on "
            f"{BANDSINTOWN_ONLINE_EVENT_URL}"
        )
    ]


@pytest.mark.asyncio
async def test_extract_bandsintown_concert_reads_partial_venue_and_tickets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A venue's missing details are left out, and its country gets the short name.

    A ticket without a link is skipped, which is logged as a warning.
    """
    window_data = bandsintown_window_data(
        BANDSINTOWN_PLACE,
        {
            "detailedTicketList": {
                "ticketList": [{"directTicketUrl": "https://tickets.example/1"}, {"ticketType": None}]
            }
        },
    )
    install_bandsintown_event_page(monkeypatch, bandsintown_event_page(window_data))

    with caplog.at_level(logging.WARNING, logger="web.events.data"):
        event = await data.extract_bandsintown_event(BANDSINTOWN_ONLINE_EVENT_DATA)

    short_url = await ShortURL.objects.aget(url="https://tickets.example/1")
    assert event.type == EventType.concert
    assert event.venue == Venue(name="Royal Albert Hall", city="London", country="United Kingdom")
    assert event.tickets_urls == [short_url.full_short_url]
    assert [r.getMessage() for r in caplog.records] == [
        (
            "directTicketUrl not found in one of the elements in eventView.body.detailedTicketList.ticketList in "
            f"'window.__data' on {BANDSINTOWN_ONLINE_EVENT_URL}"
        )
    ]


@pytest.mark.asyncio
async def test_extract_bandsintown_concert_without_venue_name_has_no_venue(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A concert whose venue has no name gets no venue, which is logged as a warning, and no tickets."""
    location = {"@type": "Place", "address": {"addressCountry": "UK"}}
    window_data = bandsintown_window_data(
        location, {"detailedTicketList": {"ticketList": [{"directTicketUrl": "https://tickets.example/1"}]}}
    )
    install_bandsintown_event_page(monkeypatch, bandsintown_event_page(window_data))

    with caplog.at_level(logging.WARNING, logger="web.events.data"):
        event = await data.extract_bandsintown_event(BANDSINTOWN_ONLINE_EVENT_DATA)

    assert event.venue is None
    assert event.tickets_urls == []
    assert [r.getMessage() for r in caplog.records] == [f"Venue data is incomplete: {location}. Setting Venue to None"]


@pytest.mark.asyncio
async def test_extract_bandsintown_event_without_event_view_reports_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An event page without the event's view still gives the event, with no tickets, and logs what is missing."""
    window_data = {"jsonLdContainer": {"eventJsonLd": {"location": BANDSINTOWN_PLACE}}}
    install_bandsintown_event_page(monkeypatch, bandsintown_event_page(window_data))

    with caplog.at_level(logging.WARNING, logger="web.events.data"):
        event = await data.extract_bandsintown_event(BANDSINTOWN_ONLINE_EVENT_DATA)

    assert event.venue == Venue(name="Royal Albert Hall", city="London", country="United Kingdom")
    assert event.tickets_urls == []
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.ERROR, f"eventView key not found in 'window.__data' on {BANDSINTOWN_ONLINE_EVENT_URL}"),
        (logging.ERROR, f"eventView.body key not found in 'window.__data' on {BANDSINTOWN_ONLINE_EVENT_URL}"),
        (
            logging.WARNING,
            f"eventView.body.detailedTicketList key not found in 'window.__data' on {BANDSINTOWN_ONLINE_EVENT_URL}",
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("page", "error"),
    [
        ("<html><head></head><body></body></html>", "'window.__data' element not found on {url}"),
        (
            "<html><head><script>window.__data={}</script><script>window.__data={}</script></head></html>",
            "Multiple 'window.__data' elements found on {url}",
        ),
        ("<html><head><script>window.__data={</script></head></html>", "Failed to parse 'window.__data' from {url}: "),
        (bandsintown_event_page({}), "jsonLdContainer.eventJsonLd.location not found on {url}"),
    ],
)
async def test_extract_bandsintown_event_fails_without_event_data(
    monkeypatch: pytest.MonkeyPatch, page: str, error: str
) -> None:
    """An event page without the event's data, or with more than one set of it, fails the event."""
    install_bandsintown_event_page(monkeypatch, page)

    with pytest.raises(BandsintownException) as exc_info:
        await data.extract_bandsintown_event(BANDSINTOWN_ONLINE_EVENT_DATA)

    assert str(exc_info.value).startswith(error.format(url=BANDSINTOWN_ONLINE_EVENT_URL))
