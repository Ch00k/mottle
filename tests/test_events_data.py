import datetime
import logging
from pathlib import Path

import httpx
import pytest
from django.conf import settings

from urlshortener.models import ShortURL
from web.events import data
from web.events.constants import BANDSINTOWN_BASE_URL, SONGKICK_BASE_URL
from web.events.data import EventSourceArtist, Venue
from web.events.enums import ArtistNameMatchAccuracy, EventDataSource, EventType
from web.events.http import AsyncRetryingClient
from web.models import Artist, EventArtist

# ShortURL.shorten opens a transaction from asgiref's worker thread, which the in-memory test database refuses
# while the test's own transaction holds it on the main thread.
pytestmark = pytest.mark.django_db(transaction=True)

DATA_DIR = Path(__file__).parent / "data"
CALENDAR_PATH = "/artists/99952-deep-purple/calendar"
CONCERT_PATH = "/concerts/42898019-deep-purple-at-hamburg-sporthalle"


def songkick_site(request: httpx.Request) -> httpx.Response:
    """Serve the saved Songkick pages, and redirect an artist's former calendar URL to its current one."""
    pages = {
        CALENDAR_PATH: "songkick_artist_calendar.html",
        CONCERT_PATH: "songkick_concert.html",
    }

    if request.url.path == "/artists/1-deep-purple-old/calendar":
        return httpx.Response(301, headers={"Location": f"{SONGKICK_BASE_URL}{CALENDAR_PATH}"})

    page = pages.get(request.url.path)
    if page is None:
        return httpx.Response(404)

    return httpx.Response(200, text=(DATA_DIR / page).read_text())


@pytest.fixture
def songkick(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AsyncRetryingClient(
        name="songkick",
        base_url=SONGKICK_BASE_URL,
        transport=httpx.MockTransport(songkick_site),
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
        f"{SONGKICK_BASE_URL}//tickets/33601226",
        f"{SONGKICK_BASE_URL}//tickets/33602496",
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
        name="bandsintown",
        retries=2,
        base_url=BANDSINTOWN_BASE_URL,
        transport=httpx.MockTransport(lambda _: httpx.Response(403, text="Just a moment...")),
    )
    monkeypatch.setattr(data, "async_bandsintown_client", bandsintown)
    artist = EventSourceArtist(
        name="Deep Purple",
        songkick_url=f"{SONGKICK_BASE_URL}{CALENDAR_PATH}",
        bandsintown_url=f"{BANDSINTOWN_BASE_URL}/a/564",
    )

    await artist.fetch_events()

    assert [e.url for e in artist.events] == [f"{SONGKICK_BASE_URL}{CONCERT_PATH}"]
    errors = [r for r in caplog.records if r.name == "web.events.data" and r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].getMessage().startswith(f"Failed to fetch data from {BANDSINTOWN_BASE_URL}/a/564: ")
