import datetime
import json
import logging
from pathlib import Path

import httpx2
import pytest
from django.conf import settings

from urlshortener.models import ShortURL
from web.events import data
from web.events.constants import BANDSINTOWN_BASE_URL, SONGKICK_BASE_URL
from web.events.data import EventSourceArtist, Venue
from web.events.enums import ArtistNameMatchAccuracy, EventDataSource, EventType
from web.events.exceptions import BandsintownException, EventDataSourceException
from web.events.http import AsyncRetryingClient
from web.models import Artist, EventArtist

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
