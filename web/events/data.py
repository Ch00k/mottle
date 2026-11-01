import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from django.conf import settings
from sentry_sdk import capture_exception, capture_message

from urlshortener.models import ShortURL

from .constants import (
    BANDSINTOWN_BASE_URL,
    BANDSINTOWN_JSON_LD_XPATH,
    BANDSINTOWN_WINDOW_DATA_XPATH,
    SONGKICK_ARTIST_CALENDAR_URL_REGEX,
    SONGKICK_ARTIST_URL_REGEX,
    SONGKICK_BASE_URL,
    SONGKICK_EVENT_URL_REGEX,
    SONGKICK_EVENTS_XPATH,
    SONGKICK_LIVE_STREAM_XPATH,
    SONGKICK_TICKETS_XPATH,
)
from .enums import SONGKICK_EVENT_TYPE_MAP, ArtistNameMatchAccuracy, EventDataSource, EventType
from .exceptions import (
    BandsintownException,
    EventDataSourceException,
    HTTPClientException,
    MusicBrainzException,
    SongkickException,
)
from .http import (
    async_bandsintown_client,
    async_musicbrainz_client,
    async_songkick_client,
    get_json,
    get_redirect_location,
    get_xpath,
)
from .utils import (
    get_normalized_country_name,
    match_artist_name,
    normalize_string,
    replace_unicode_characters,
    should_fetch_event,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class MusicBrainzArtist:
    id: str
    names: list[str]
    songkick_url: str | None = None
    bandsintown_url: str | None = None

    @staticmethod
    async def find(artist_spotify_id: str, artist_name: str) -> MusicBrainzArtist | None:
        try:
            return await MusicBrainzArtist.find_by_spotify_url(f"https://open.spotify.com/artist/{artist_spotify_id}")
        except MusicBrainzException as e:
            logger.warning(f"Failed to find artist by Spotify ID '{artist_spotify_id}': {e}. Trying search by name")
            return await MusicBrainzArtist.find_by_name(artist_name)

    @staticmethod
    async def find_by_spotify_url(url: str) -> MusicBrainzArtist | None:
        try:
            data = await get_json(async_musicbrainz_client, "url", params={"resource": url, "inc": "artist-rels"})
        except HTTPClientException as e:
            raise MusicBrainzException(f"Failed to fetch artist by Spotify URL '{url}': {e}") from e

        relations = data.get("relations", [])
        if not relations:
            raise MusicBrainzException(f"No artist relations found for URL '{url}' in MusicBrainz")

        artist_id = relations[0].get("artist", {}).get("id")
        if not artist_id:
            raise MusicBrainzException(f"No artist ID found for URL '{url}' in MusicBrainz")

        logger.info(f"Found artist ID '{artist_id}' for Spotify URL '{url}' in MusicBrainz")

        try:
            artist = await MusicBrainzArtist.from_artist_id(artist_id)
        except Exception as e:
            raise MusicBrainzException(f"Failed to get artist info for {artist_id}: {e}") from e
        else:
            return artist

    @staticmethod
    async def find_by_name(artist_name: str) -> MusicBrainzArtist | None:
        if not artist_name:
            raise MusicBrainzException("Artist name is empty")

        logger.info(f"Searching for artist '{artist_name}' in MusicBrainz")

        try:
            data = await get_json(async_musicbrainz_client, "artist", params={"query": artist_name, "limit": 100})
        except HTTPClientException as e:
            raise MusicBrainzException(f"Failed to fetch artist by name '{artist_name}': {e}") from e

        artists = data.get("artists", [])

        if not artists:
            logger.warning(f"No artists found for query '{artist_name}'")
            return None

        normalized_artist_name = normalize_string(artist_name)

        for artist_data in artists:
            # TODO: https://musicbrainz.org/doc/Style/Artist/Sort_Name
            artist_names = [normalize_string(artist_data["name"])] + [
                normalize_string(a["name"]) for a in artist_data.get("aliases", [])
            ]

            if normalized_artist_name in {a for a in artist_names if a}:
                try:
                    artist = await MusicBrainzArtist.from_artist_id(artist_data["id"])
                except Exception as e:
                    raise MusicBrainzException(f"Failed to get artist info for '{artist_name}': {e}") from e
                else:
                    logger.info(f"Found MusicBrainzArtist {artist_data['id']} for '{artist_name}'")
                    return artist

        logger.warning(f"No artists found in MusicBrainz for query '{artist_name}'")
        return None

    @staticmethod
    async def from_artist_id(artist_id: str) -> MusicBrainzArtist:
        try:
            data = await get_json(async_musicbrainz_client, f"artist/{artist_id}", params={"inc": "aliases+url-rels"})
        except HTTPClientException as e:
            raise MusicBrainzException(f"Failed to fetch artist by ID '{artist_id}': {e}") from e

        songkick_url = None
        bandsintown_url = None

        for url in data.get("relations", []):
            if url["type"] == "songkick":
                songkick_url = url["url"]["resource"]
                logger.debug(f"Found Songkick URL: {songkick_url}")
            if url["type"] == "bandsintown":
                bandsintown_url = url["url"]["resource"]
                logger.debug(f"Found Bandsintown URL: {bandsintown_url}")

        # A link that does not lead to the artist's events is dropped, which leaves that source to the name search
        if songkick_url:
            # MusicBrainz links to the artist's page, and the events are read from the artist's calendar
            match = SONGKICK_ARTIST_URL_REGEX.match(songkick_url)
            if match is None:
                logger.warning(f"MusicBrainz artist {artist_id} links to a Songkick page of no artist: {songkick_url}")
                songkick_url = None
            else:
                try:
                    songkick_url = await get_songkick_calendar_url(match.group(1))
                except HTTPClientException as e:
                    logger.warning(f"Failed to find the Songkick calendar of MusicBrainz artist {artist_id}: {e}")
                    songkick_url = None

        if bandsintown_url:
            # Sometimes the URL we find in MusicBrainz is not the final URL, e.g. https://www.bandsintown.com/a/738
            try:
                bandsintown_url = (
                    await get_redirect_location(async_bandsintown_client, bandsintown_url) or bandsintown_url
                )
            except HTTPClientException as e:
                logger.warning(f"Failed to follow the Bandsintown URL of MusicBrainz artist {artist_id}: {e}")
                bandsintown_url = None

        if all((songkick_url, bandsintown_url)):
            artist_names = [data["name"]]
        else:
            # TODO: https://musicbrainz.org/doc/Style/Artist/Sort_Name
            # TODO: Artist 563ace2c-6e94-4b64-b544-40099a96b86d has a legitimate alias that is not primary
            # c463f1c4-d19f-420f-ac88-a7b5afabeeb9 has no primary alises at all
            aliases = [data["name"]] + [a["name"] for a in data.get("aliases", []) if a["primary"]]
            artist_names = [replace_unicode_characters(a) for a in aliases] + aliases
            artist_names = list(dict.fromkeys([a for a in artist_names if a]))

        return MusicBrainzArtist(
            id=artist_id, names=artist_names, songkick_url=songkick_url, bandsintown_url=bandsintown_url
        )


@dataclass
class Venue:
    name: str
    postcode: str | None = None
    address: str | None = None
    city: str | None = None
    country: str | None = None
    geo_lat: float | None = None
    geo_lon: float | None = None


@dataclass
class Event:
    source: EventDataSource
    url: str
    type: EventType
    date: date
    venue: Venue | None = None
    stream_urls: list[str] = field(default_factory=list)
    tickets_urls: list[str] = field(default_factory=list)


@dataclass
class EventSourceArtist:
    name: str
    alternative_names: list[str] = field(default_factory=list)
    songkick_url: str | None = None
    bandsintown_url: str | None = None
    songkick_match_accuracy: ArtistNameMatchAccuracy = ArtistNameMatchAccuracy.no_match
    bandsintown_match_accuracy: ArtistNameMatchAccuracy = ArtistNameMatchAccuracy.no_match
    events: list[Event] = field(default_factory=list)
    failed_sources: list[EventDataSource] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name:
            raise EventDataSourceException("Artist name is empty")

    @staticmethod
    async def find(
        artist_name: str, alternative_names: list[str], use_advanced_heuristics: bool = False
    ) -> EventSourceArtist:
        if artist_name in alternative_names:
            alternative_names.remove(artist_name)

        alt_names_str = ", ".join(alternative_names) if alternative_names else "none"
        logger.info(
            f"Searching for artist '{artist_name}' (alternative names: {alt_names_str}) in Songkick and Bandsintown"
        )

        artist = EventSourceArtist(name=artist_name, alternative_names=alternative_names)

        songkick_result, bandsintown_result = await asyncio.gather(
            artist.find_in_songkick(use_advanced_heuristics),
            artist.find_in_bandsintown(use_advanced_heuristics),
            return_exceptions=True,
        )

        if isinstance(songkick_result, BaseException):
            logger.warning(songkick_result)
        else:
            logger.debug(
                f"Artist found in Songkick ({artist.songkick_url}), "
                f"name match accuracy: {artist.songkick_match_accuracy}"
            )

        if isinstance(bandsintown_result, BaseException):
            logger.warning(bandsintown_result)
        else:
            logger.debug(
                f"Artist found in Bandsintown ({artist.bandsintown_url}), "
                f"name match accuracy: {artist.bandsintown_match_accuracy}"
            )

        return artist

    async def find_by_any_name(
        self,
        source_name: str,
        find_artist: Callable[[str, bool], Awaitable[tuple[str | None, ArtistNameMatchAccuracy]]],
        use_advanced_heuristics: bool,
    ) -> tuple[str, ArtistNameMatchAccuracy] | None:
        """Return the source's ID of the artist, found by the first of its names that finds it, and how well it matched.

        A name whose search fails is logged with its error, and the next name is tried.
        """
        for name in [self.name, *self.alternative_names]:
            logger.info(f"Searching for artist in {source_name} by one of the names: '{name}'")

            try:
                artist_id, accuracy = await find_artist(name, use_advanced_heuristics)
            except Exception as e:
                logger.exception(
                    f"Failed to search for artist in {source_name} by name '{name}': {e}. Trying next name"
                )
                capture_exception(e)
                continue

            if artist_id is not None:
                return artist_id, accuracy

            logger.warning(f"Could not find artist by name '{name}' in {source_name}. Trying next name")

        return None

    async def find_in_songkick(self, use_advanced_heuristics: bool = False) -> None:
        found = await self.find_by_any_name("Songkick", find_artist_in_songkick, use_advanced_heuristics)
        if found is None:
            raise SongkickException(f"Artist '{self.name}' not found in Songkick using any of the names")

        artist_id, accuracy = found

        try:
            artist_url = await get_songkick_calendar_url(artist_id)
        except HTTPClientException as e:
            raise SongkickException(f"Failed to fetch artist URL for '{self.name}': {e}") from e

        logger.info(f"Found artist '{self.name}' in Songkick: {artist_url}")

        self.songkick_url = artist_url
        self.songkick_match_accuracy = accuracy

    async def find_in_bandsintown(self, use_advanced_heuristics: bool = False) -> None:
        found = await self.find_by_any_name("Bandsintown", find_artist_in_bandsintown, use_advanced_heuristics)
        if found is None:
            raise BandsintownException(f"Artist '{self.name}' not found in Bandsintown using any of the names")

        artist_id, accuracy = found
        artist_url = f"{BANDSINTOWN_BASE_URL}/a/{artist_id}"
        logger.info(f"Found artist in Bandsintown: {artist_url}")

        self.bandsintown_url = artist_url
        self.bandsintown_match_accuracy = accuracy

    async def fetch_events(self) -> None:
        events = {e.url: e for e in self.events}

        # A source that fails, including on markup it cannot parse, leaves the events read from the other one in place
        if self.songkick_url is not None:
            try:
                await self.fetch_songkick_events(self.songkick_url, events)
            except Exception as e:
                logger.exception(e)
                self.failed_sources.append(EventDataSource.songkick)

        if self.bandsintown_url is not None:
            try:
                await self.fetch_bandsintown_events(self.bandsintown_url, events)
            except Exception as e:
                logger.exception(e)
                self.failed_sources.append(EventDataSource.bandsintown)

        self.events = list(events.values())

    async def fetch_songkick_events(self, songkick_url: str, events: dict[str, Event]) -> None:
        try:
            events_xpath, calendar_url = await get_xpath(
                async_songkick_client, songkick_url, SONGKICK_EVENTS_XPATH, follow_redirects=True
            )
        except HTTPClientException as exc:
            raise SongkickException(f"Failed to fetch events from Songkick: {exc}") from exc

        # Songkick moves an artist's calendar when it merges or renames the artist, but a redirect to any other page
        # would point the artist at a page with no events for good
        if not SONGKICK_ARTIST_CALENDAR_URL_REGEX.match(calendar_url):
            raise SongkickException(
                f"Songkick redirected {songkick_url} to {calendar_url}, which is not an artist calendar"
            )

        self.songkick_url = calendar_url

        event_jsons = [json.loads(event) for event in events_xpath]

        songkick_events_data = []
        for event_json in event_jsons:
            if len(event_json) != 1:
                logger.error(f"Event data list does not have exactly one item: {event_json}")
                capture_message(f"Event data list does not have exactly one item: {event_json}")
                continue

            event_data = event_json[0]
            event_url = event_data["url"].split("?")[0]

            if not should_fetch_event(event_url, EventDataSource.songkick, events):
                logger.info(f"Event {event_url} already exists and has tickets or stream URLs. Skipping")
                continue

            songkick_events_data.append(event_data)

        calls = [extract_songkick_event(event_data) for event_data in songkick_events_data]
        results = await asyncio.gather(*calls, return_exceptions=True)

        for r in results:
            if isinstance(r, BaseException):
                logger.error(r, exc_info=r)
            else:
                events[r.url] = r

    async def fetch_bandsintown_events(self, bandsintown_url: str, events: dict[str, Event]) -> None:
        events_data = await extract_bandsintown_events_data(bandsintown_url)

        # This assumes that there are no duplicate dates in the following `for` loop
        existing_event_dates = [e.date for e in events.values()]

        bandsintown_events_data = []
        for event_data in events_data:
            event_url = event_data["url"].split("?")[0]
            event_date = datetime.fromisoformat(event_data["startDate"]).date()

            if not should_fetch_event(event_url, EventDataSource.bandsintown, events):
                logger.info(f"Event {event_url} already exists and has tickets or stream URLs. Skipping")
                continue

            # TODO: Perhaps this check needs to be a little smarter, but good enough for now
            if event_date in existing_event_dates:
                logger.warning(f"Event on {event_date} already exists. Skipping")
                continue

            bandsintown_events_data.append(event_data)

        calls = [extract_bandsintown_event(event_data) for event_data in bandsintown_events_data]
        results = await asyncio.gather(*calls, return_exceptions=True)

        for r in results:
            if isinstance(r, BaseException):
                logger.error(r, exc_info=r)
            else:
                events[r.url] = r


async def get_songkick_calendar_url(artist_id: str) -> str:
    """Return the URL of the Songkick artist's calendar, which redirects from one that has only the artist's ID."""
    url_path = f"artists/{artist_id}/calendar"
    return await get_redirect_location(async_songkick_client, url_path) or f"{SONGKICK_BASE_URL}/{url_path}"


async def find_artist_in_songkick(
    artist_name: str, use_advanced_heuristics: bool = False
) -> tuple[str | None, ArtistNameMatchAccuracy]:
    # https://github.com/encode/httpx/discussions/3360
    query = artist_name.replace('"', "%22")
    search_results = await get_json(async_songkick_client, f"api/universal_search?query={query}")

    artists = [
        (str(a["document"]["primary_key_id"]), a["document"]["name"])
        for a in search_results["data"]["attributes"]["search_results"]["artists"]
    ]
    return match_artist_name(artist_name, artists, use_advanced_heuristics)


async def find_artist_in_bandsintown(
    artist_name: str, use_advanced_heuristics: bool = False
) -> tuple[str | None, ArtistNameMatchAccuracy]:
    try:
        search_results = await get_json(async_bandsintown_client, f"searchSuggestions?searchTerm={artist_name}")
    except HTTPClientException as e:
        raise BandsintownException(f"Failed to search Bandsintown for '{artist_name}': {e}") from e

    artists = [(str(a["id"]), a["name"]) for a in search_results["artists"]]
    return match_artist_name(artist_name, artists, use_advanced_heuristics)


def venue_from_place(place: dict[str, Any]) -> Venue | None:
    """Return the venue a schema.org Place describes, or None for a place without a name."""
    name = place.get("name")
    if name is None:
        logger.warning(f"Venue data is incomplete: {place}. Setting Venue to None")
        return None

    address = place.get("address", {})
    geo = place.get("geo", {})

    return Venue(
        name=name,
        postcode=address.get("postalCode"),
        address=address.get("streetAddress"),
        city=address.get("addressLocality"),
        country=get_normalized_country_name(address.get("addressCountry")),
        geo_lat=geo.get("latitude"),
        geo_lon=geo.get("longitude"),
    )


async def extract_songkick_event(event_data: dict[str, Any]) -> Event:
    event_url = event_data["url"].split("?")[0]
    match = SONGKICK_EVENT_URL_REGEX.match(event_url)

    if match is None:
        raise SongkickException(f"Event URL does not match pattern: {event_url}")

    event_type_orig = match.group(1)

    event_type = SONGKICK_EVENT_TYPE_MAP.get(event_type_orig)
    if event_type is None:
        raise SongkickException(f"Unknown event type: {event_type_orig}")

    venue = None if event_type == EventType.live_stream else venue_from_place(event_data["location"])

    # TODO: Can an event have both ticket and live stream URLs?
    stream_urls = []
    tickets_urls = []

    xpath = SONGKICK_LIVE_STREAM_XPATH if event_type == EventType.live_stream else SONGKICK_TICKETS_XPATH

    urls, _ = await get_xpath(async_songkick_client, event_url, xpath)

    urls = [u.split("?")[0] for u in urls]

    if settings.RESOLVE_SONGKICK_URLS:
        calls = [get_redirect_location(async_songkick_client, u) for u in urls]
        results = await asyncio.gather(*calls, return_exceptions=True)

        result_urls = []
        for r in results:
            if isinstance(r, HTTPClientException):
                logger.error(f"Failed to fetch URL: {r}", exc_info=r)
                capture_exception(r)
            elif isinstance(r, BaseException):
                logger.error(f"Unexpected error while fetching URL: {r}", exc_info=r)
                capture_exception(r)
            elif r is not None:
                result_urls.append((await ShortURL.shorten(r)).full_short_url)
    else:
        urls = [urljoin(SONGKICK_BASE_URL, u) for u in urls]
        result_urls = [(await ShortURL.shorten(u)).full_short_url for u in urls]

    if event_type == EventType.live_stream:
        stream_urls = result_urls
    else:
        tickets_urls = result_urls

    return Event(
        source=EventDataSource.songkick,
        url=event_url,
        type=event_type,
        date=datetime.fromisoformat(event_data["startDate"]).date(),
        venue=venue,
        stream_urls=stream_urls,
        tickets_urls=tickets_urls,
    )


async def extract_bandsintown_event(event_data: dict[str, Any]) -> Event:
    event_url = event_data["url"].split("?")[0]
    date = datetime.fromisoformat(event_data["startDate"]).date()

    try:
        script_tag, _ = await get_xpath(async_bandsintown_client, event_url, BANDSINTOWN_WINDOW_DATA_XPATH)
    except HTTPClientException as e:
        raise BandsintownException(f"Failed to fetch data for event {event_url}: {e}") from e
    else:
        if not script_tag:
            raise BandsintownException(f"'window.__data' element not found on {event_url}")

        if len(script_tag) > 1:
            raise BandsintownException(f"Multiple 'window.__data' elements found on {event_url}")

        try:
            window_data = script_tag[0].split("window.__data=")[1]
        except Exception as e:
            raise BandsintownException(f"Failed to extract 'window.__data' from {event_url}: {e}") from e

        try:
            data = json.loads(window_data)
        except Exception as e:
            raise BandsintownException(f"Failed to parse 'window.__data' from {event_url}: {e}") from e

    # The artist page's event data leaves out the venue's address, which only the event page has
    try:
        location = data["jsonLdContainer"]["eventJsonLd"]["location"]
    except KeyError as e:
        raise BandsintownException(f"jsonLdContainer.eventJsonLd.location not found on {event_url}") from e

    is_streaming_event = location["@type"] == "VirtualLocation"

    tickets_urls = []
    stream_urls = []

    event_view = data.get("eventView", {})
    if not event_view:
        msg = f"eventView key not found in 'window.__data' on {event_url}"
        logger.error(msg)
        capture_message(msg)

    event_view_body = event_view.get("body", {})
    if not event_view_body:
        msg = f"eventView.body key not found in 'window.__data' on {event_url}"
        logger.error(msg)
        capture_message(msg)

    if is_streaming_event:
        type = EventType.live_stream  # noqa: A001
        venue = None

        event_details = event_view_body.get("hybridEventDetails", {})
        if not event_details:
            logger.warning(f"eventView.body.hybridEventDetails key not found in 'window.__data' on {event_url}")

        stream_url = event_details.get("streamingUrl")
        if stream_url:
            stream_urls = [(await ShortURL.shorten(stream_url)).full_short_url]
        else:
            logger.warning(
                f"eventView.body.hybridEventDetails.streamingUrl key not found in 'window.__data' on {event_url}"
            )

    else:
        type = EventType.concert  # noqa: A001
        venue = venue_from_place(location)

        detailed_ticket_list = event_view_body.get("detailedTicketList", {})
        if not detailed_ticket_list:
            logger.warning(f"eventView.body.detailedTicketList key not found in 'window.__data' on {event_url}")

        ticket_list = detailed_ticket_list.get("ticketList", [])
        for ticket in ticket_list:
            ticket_url = ticket.get("directTicketUrl")
            if ticket_url:
                tickets_urls.append((await ShortURL.shorten(ticket_url)).full_short_url)
            else:
                logger.warning(
                    f"directTicketUrl not found in one of the elements in "
                    f"eventView.body.detailedTicketList.ticketList in 'window.__data' on {event_url}"
                )

    return Event(
        source=EventDataSource.bandsintown,
        url=event_url,
        type=type,
        date=date,
        venue=venue,
        stream_urls=stream_urls,
        tickets_urls=tickets_urls,
    )


async def extract_bandsintown_events_data(bandsintown_url: str) -> list[dict[str, Any]]:
    try:
        # The page of an artist with no upcoming events redirects to itself with `?noindex=1`
        scripts, _ = await get_xpath(
            async_bandsintown_client, bandsintown_url, BANDSINTOWN_JSON_LD_XPATH, follow_redirects=True
        )
    except HTTPClientException as e:
        raise BandsintownException(f"Failed to fetch data from {bandsintown_url}: {e}") from e

    try:
        json_lds = [json.loads(script) for script in scripts]
    except Exception as e:
        raise BandsintownException(f"Failed to parse JSON-LD on {bandsintown_url}: {e}") from e

    # The artist's upcoming events are the only list among the JSON-LD in the page's head, and it is empty when there
    # are none
    event_lists = [json_ld for json_ld in json_lds if isinstance(json_ld, list)]
    if len(event_lists) != 1:
        raise BandsintownException(
            f"Expected one list of events in the JSON-LD on {bandsintown_url}, found {len(event_lists)}"
        )

    events = event_lists[0]

    types = [event.get("@type") if isinstance(event, dict) else type(event).__name__ for event in events]
    if any(t != "MusicEvent" for t in types):
        raise BandsintownException(
            f"Expected only MusicEvent items in the list of events on {bandsintown_url}: {types}"
        )

    return events
