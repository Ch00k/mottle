import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from sentry_sdk import capture_exception, capture_message

from urlshortener.models import ShortURL

from .constants import (
    BANDSINTOWN_BASE_URL,
    BANDSINTOWN_WINDOW_DATA_XPATH,
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
    HeuristicsException,
    HTTPClientException,
    MusicBrainzException,
    SongkickException,
)
from .http import (
    asend_get_request,
    async_bandsintown_client,
    async_musicbrainz_client,
    async_songkick_client,
)
from .utils import (
    find_best_artist_name_match_advanced,
    find_best_artist_name_match_simple,
    get_normalized_country_name,
    normalize_string,
    replace_unicode_characters,
    should_fetch_event,
)

logger = logging.getLogger(__name__)


@dataclass
class MusicBrainzArtist:
    id: str
    names: list[str]
    songkick_url: str | None = None
    bandsintown_url: str | None = None

    @staticmethod
    async def find(artist_spotify_id: str, artist_name: str) -> Optional["MusicBrainzArtist"]:
        try:
            return await MusicBrainzArtist.find_by_spotify_url(f"https://open.spotify.com/artist/{artist_spotify_id}")
        except MusicBrainzException as e:
            logger.warning(f"Failed to find artist by Spotify ID '{artist_spotify_id}': {e}. Trying search by name")
            return await MusicBrainzArtist.find_by_name(artist_name)

    @staticmethod
    async def find_by_spotify_url(url: str) -> Optional["MusicBrainzArtist"]:
        try:
            resp = await async_musicbrainz_client.get("url", params={"resource": url, "inc": "artist-rels"})
        except HTTPClientException as e:
            raise MusicBrainzException(f"Failed to fetch artist by Spotify URL '{url}': {e}")

        try:
            data = resp.json()
        except Exception as e:
            raise MusicBrainzException(f"Failed to parse JSON '{resp.text}': {e}")

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
            raise MusicBrainzException(f"Failed to get artist info for {artist_id}: {e}")
        else:
            return artist

    @staticmethod
    async def find_by_name(artist_name: str) -> Optional["MusicBrainzArtist"]:
        if not artist_name:
            raise MusicBrainzException("Artist name is empty")

        logger.info(f"Searching for artist '{artist_name}' in MusicBrainz")

        try:
            resp = await async_musicbrainz_client.get("artist", params={"query": artist_name, "limit": 100})
        except HTTPClientException as e:
            raise MusicBrainzException(f"Failed to fetch artist by name '{artist_name}': {e}")

        try:
            data = resp.json()
        except Exception as e:
            raise MusicBrainzException(f"Failed to parse JSON '{resp.text}': {e}")

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
                    raise MusicBrainzException(f"Failed to get artist info for '{artist_name}': {e}")
                else:
                    logger.info(f"Found MusicBrainzArtist {artist_data['id']} for '{artist_name}'")
                    return artist
        else:
            logger.warning(f"No artists found in MusicBrainz for query '{artist_name}'")
            return None

    @staticmethod
    async def from_artist_id(artist_id: str) -> "MusicBrainzArtist":
        try:
            resp = await async_musicbrainz_client.get(f"artist/{artist_id}", params={"inc": "aliases+url-rels"})
        except HTTPClientException as e:
            raise MusicBrainzException(f"Failed to fetch artist by ID '{artist_id}': {e}")

        try:
            data = resp.json()
        except Exception as e:
            raise HTTPClientException(f"Failed to parse JSON '{resp.text}': {e}")

        # TODO: https://musicbrainz.org/doc/Style/Artist/Sort_Name
        aliases = [data["name"]] + [a["name"] for a in data.get("aliases", []) if a["primary"]]
        artist_names = [replace_unicode_characters(a) for a in aliases] + aliases
        artist_names = list(dict.fromkeys([a for a in artist_names if a]))

        songkick_url = None
        bandsintown_url = None

        for url in data.get("relations", []):
            if url["type"] == "songkick":
                songkick_url = url["url"]["resource"]
                logger.debug(f"Found Songkick URL: {songkick_url}")
            if url["type"] == "bandsintown":
                bandsintown_url = url["url"]["resource"]
                logger.debug(f"Found Bandsintown URL: {bandsintown_url}")

        if songkick_url:
            # Get the final URL
            try:
                _, _, songkick_url, _ = await asend_get_request(
                    async_songkick_client, songkick_url, redirect_url=True, raise_for_lte_300=False
                )
            except HTTPClientException as e:
                raise SongkickException(f"Failed to get final Songkick URL: {e}")

        if bandsintown_url:
            # Sometimes the URL we find in MusicBrainz is not the final URL, e.g. https://www.bandsintown.com/a/738
            try:
                _, _, bandsintown_url, _ = await asend_get_request(
                    async_bandsintown_client, bandsintown_url, redirect_url=True, raise_for_lte_300=False
                )
            except HTTPClientException as e:
                raise BandsintownException(f"Failed to get final Bandsintown URL: {e}")

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

    def __post_init__(self) -> None:
        if not self.name:
            raise EventDataSourceException("Artist name is empty")

    @staticmethod
    async def find(
        artist_name: str, alternative_names: list[str], use_advanced_heuristics: bool = False
    ) -> "EventSourceArtist":
        if artist_name in alternative_names:
            alternative_names.remove(artist_name)

        alt_names_str = ", ".join([n for n in alternative_names]) if alternative_names else "none"
        logger.info(
            f"Searching for artist '{artist_name}' (alternative names: {alt_names_str}) in Songkick and Bandsintown"
        )

        artist = EventSourceArtist(name=artist_name, alternative_names=alternative_names)

        calls = [artist.find_in_songkick(use_advanced_heuristics), artist.find_in_bandsintown(use_advanced_heuristics)]
        results = await asyncio.gather(*calls, return_exceptions=True)

        songkick_result, bandsintown_result = results

        if isinstance(songkick_result, SongkickException):
            logger.warning(songkick_result)
        else:
            logger.debug(
                f"Artist found in Songkick ({artist.songkick_url}), "
                f"name match accuracy: {artist.songkick_match_accuracy}"
            )

        if isinstance(bandsintown_result, BandsintownException):
            logger.warning(bandsintown_result)
        else:
            logger.debug(
                f"Artist found in Bandsintown ({artist.bandsintown_url}), "
                f"name match accuracy: {artist.bandsintown_match_accuracy}"
            )

        return artist

    async def find_in_songkick(self, use_advanced_heuristics: bool = False) -> None:
        if self.songkick_url is not None:
            logger.warning("Songkick URL already exists")
            return

        for name in [self.name] + self.alternative_names:
            logger.info(f"Searching for artist in Songkick by one of the names: '{name}'")

            try:
                artist_id, accuracy = await find_artist_in_songkick(name, use_advanced_heuristics)
            except Exception as e:
                logger.exception(f"Failed to search for artist in Songkick by name '{name}': {e}. Trying next name")
                capture_exception(e)
            else:
                if artist_id is None:
                    logger.warning(f"Could not find artist by name '{name}' in Songkick. Trying next name")
                else:
                    break
        else:
            raise SongkickException(f"Artist '{self.name}' not found in Songkick using any of the names")

        url_path = f"artists/{artist_id}/calendar"
        try:
            _, _, artist_url, _ = await asend_get_request(
                async_songkick_client, url_path, redirect_url=True, raise_for_lte_300=False
            )
        except HTTPClientException as e:
            raise SongkickException(f"Failed to fetch artist URL for '{self.name}': {e}")

        # In case there was no redirect (unlikely though)
        artist_url = artist_url or f"{SONGKICK_BASE_URL}/{url_path}"

        logger.info(f"Found artist '{self.name}' in Songkick: {artist_url}")

        self.songkick_url = artist_url
        self.songkick_match_accuracy = accuracy

    async def find_in_bandsintown(self, use_advanced_heuristics: bool = False) -> None:
        if self.bandsintown_url is not None:
            logger.warning("Bandsintown URL already exists")
            return

        for name in [self.name] + self.alternative_names:
            logger.info(f"Searching for artist in Bandsintown by one of the names: '{name}'")

            try:
                artist_id, accuracy = await find_artist_in_bandsintown(name, use_advanced_heuristics)
            except BandsintownException as e:
                logger.exception(f"Failed to search for artist in Bandsintown by name '{name}': {e}. Trying next name")
                capture_exception(e)
            else:
                if artist_id is None:
                    logger.warning(f"Could not find artist by name '{name}' in Bandsintown. Trying next name")
                else:
                    break
        else:
            raise BandsintownException(f"Artist '{self.name}' not found in Bandsintown using any of the names")

        artist_url = f"{BANDSINTOWN_BASE_URL}/a/{artist_id}"
        logger.info(f"Found artist in Bandsintown: {artist_url}")

        self.bandsintown_url = artist_url
        self.bandsintown_match_accuracy = accuracy

    async def fetch_events(self) -> None:
        events = {e.url: e for e in self.events}

        if self.songkick_url is not None:
            try:
                _, events_xpath, __, __ = await asend_get_request(
                    async_songkick_client, self.songkick_url, xpath=SONGKICK_EVENTS_XPATH
                )
            except HTTPClientException as exc:
                raise SongkickException(f"Failed to fetch events from Songkick: {exc}")

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
                    logger.exception(r)
                else:
                    events[r.url] = r

        if self.bandsintown_url is not None:
            try:
                events_data = await extract_bandsintown_events_data(self.bandsintown_url)
            except HTTPClientException as exc:
                raise BandsintownException(f"Failed to fetch events from Bandsintown: {exc}")

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
                    logger.exception(r)
                else:
                    events[r.url] = r

        self.events = list(events.values())


async def find_artist_in_songkick(
    artist_name: str, use_advanced_heuristics: bool = False
) -> tuple[str | None, ArtistNameMatchAccuracy]:
    if '"' in artist_name:
        # https://github.com/encode/httpx/discussions/3360
        artist_name = artist_name.replace('"', "%22")

    search_results, _, __, __ = await asend_get_request(
        async_songkick_client, f"api/universal_search?query={artist_name}", parse_json=True
    )

    artists = [
        (str(a["document"]["primary_key_id"]), a["document"]["name"])
        for a in search_results["data"]["attributes"]["search_results"]["artists"]
    ]

    if not artists:
        return None, ArtistNameMatchAccuracy.no_match

    if use_advanced_heuristics:
        pick_artist = find_best_artist_name_match_advanced
    else:
        pick_artist = find_best_artist_name_match_simple

    try:
        return pick_artist(artist_name, artists)
    except HeuristicsException:
        return None, ArtistNameMatchAccuracy.no_match


async def find_artist_in_bandsintown(
    artist_name: str, use_advanced_heuristics: bool = False
) -> tuple[str | None, ArtistNameMatchAccuracy]:
    search_results, _, __, __ = await asend_get_request(
        async_bandsintown_client, f"searchSuggestions?searchTerm={artist_name}", parse_json=True
    )

    artists = [(str(a["id"]), a["name"]) for a in search_results["artists"]]

    if not artists:
        return None, ArtistNameMatchAccuracy.no_match

    if use_advanced_heuristics:
        pick_artist = find_best_artist_name_match_advanced
    else:
        pick_artist = find_best_artist_name_match_simple

    try:
        return pick_artist(artist_name, artists)
    except HeuristicsException:
        return None, ArtistNameMatchAccuracy.no_match


async def extract_songkick_event(event_data: dict[str, Any]) -> Event:
    event_url = event_data["url"].split("?")[0]
    match = SONGKICK_EVENT_URL_REGEX.match(event_url)

    if match is None:
        raise SongkickException(f"Event URL does not match pattern: {event_url}")

    event_type_orig = match.group(1)

    event_type = SONGKICK_EVENT_TYPE_MAP.get(event_type_orig)
    if event_type is None:
        raise SongkickException(f"Unknown event type: {event_type_orig}")

    location = event_data["location"]
    venue_name = location.get("name")

    if event_type == EventType.live_stream:
        venue = None
    else:
        if venue_name is None:
            logger.warning(f"Venue data is incomplete: {location}. Setting Venue to None")
            venue = None
        else:
            venue_address = location.get("address", {})
            venue_geolocation = location.get("geo", {})

            venue = Venue(
                name=venue_name,
                postcode=venue_address.get("postalCode"),
                address=venue_address.get("streetAddress"),
                city=venue_address.get("addressLocality"),
                country=get_normalized_country_name(venue_address.get("addressCountry")),
                geo_lat=venue_geolocation.get("latitude"),
                geo_lon=venue_geolocation.get("longitude"),
            )

    # TODO: Can an event have both ticket and live stream URLs?
    stream_urls = []
    tickets_urls = []

    if event_type == EventType.live_stream:
        xpath = SONGKICK_LIVE_STREAM_XPATH
    else:
        xpath = SONGKICK_TICKETS_XPATH

    _, urls, __, __ = await asend_get_request(async_songkick_client, event_url, xpath=xpath)

    urls = [u for u in urls]
    calls = [asend_get_request(async_songkick_client, u, redirect_url=True, raise_for_lte_300=False) for u in urls]
    results = await asyncio.gather(*calls, return_exceptions=True)

    result_urls = []
    for r in results:
        if isinstance(r, HTTPClientException):
            logger.exception(f"Failed to fetch URL: {r}")
            capture_exception(r)
        elif isinstance(r, BaseException):
            logger.exception(f"Unexpected error while fetching URL: {r}")
            capture_exception(r)
        else:
            if r[2] is not None:
                result_urls.append((await ShortURL.shorten(r[2])).full_short_url)

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
    location = event_data["location"]
    is_streaming_event = event_data["location"]["@type"] == "VirtualLocation"
    date = datetime.fromisoformat(event_data["startDate"]).date()

    try:
        _, script_tag, _, _ = await asend_get_request(
            async_bandsintown_client, event_url, xpath=BANDSINTOWN_WINDOW_DATA_XPATH
        )
    except HTTPClientException as e:
        raise BandsintownException(f"Failed to fetch data for event {event_url}: {e}")
    else:
        if not script_tag:
            raise BandsintownException(f"'window.__data' element not found on {event_url}")

        if len(script_tag) > 1:
            raise BandsintownException(f"Multiple 'window.__data' elements found on {event_url}")

        try:
            window_data = script_tag[0].split("window.__data=")[1]
        except Exception as e:
            raise BandsintownException(f"Failed to extract 'window.__data' from {event_url}: {e}")

        try:
            data = json.loads(window_data)
        except Exception as e:
            raise BandsintownException(f"Failed to parse 'window.__data' from {event_url}: {e}")

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
        type = EventType.live_stream
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
        type = EventType.concert
        venue_name = location.get("name")

        if venue_name is None:
            logger.warning(f"Venue data is incomplete: {location}. Setting Venue to None")
            venue = None
        else:
            venue_address = location.get("address", {})
            venue_geolocation = location.get("geo", {})

            venue = Venue(
                name=venue_name,
                postcode=venue_address.get("postalCode"),
                address=venue_address.get("streetAddress"),
                city=venue_address.get("addressLocality"),
                country=get_normalized_country_name(venue_address.get("addressCountry")),
                geo_lat=venue_geolocation.get("latitude"),
                geo_lon=venue_geolocation.get("longitude"),
            )

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
        _, script_tag, __, __ = await asend_get_request(
            async_bandsintown_client, bandsintown_url, xpath=BANDSINTOWN_WINDOW_DATA_XPATH
        )
    except HTTPClientException as e:
        raise BandsintownException(f"Failed to fetch data from {bandsintown_url}: {e}")

    if not script_tag:
        raise BandsintownException(f"'{bandsintown_url}' page has no 'window.__data=' element")

    if len(script_tag) > 1:
        raise BandsintownException(f"Multiple 'window.__data=' elements found on {bandsintown_url}")

    try:
        window_data = script_tag[0].split("window.__data=")[1]
    except Exception as e:
        raise BandsintownException(f"Failed to extract 'window.__data' from {bandsintown_url}: {e}")

    try:
        data = json.loads(window_data)
    except Exception as e:
        raise BandsintownException(f"Failed to parse 'window.__data' on {bandsintown_url}: {e}")

    events_data: list[dict[str, Any]] = data["jsonLdContainer"]["eventsJsonLd"]

    return events_data
