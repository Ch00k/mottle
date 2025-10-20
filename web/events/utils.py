import logging
import unicodedata
from typing import TYPE_CHECKING, Literal

import country_converter as coco
from django.conf import settings
from thefuzz import fuzz
from unidecode import unidecode

from .constants import ALNUM_TABLE
from .enums import ArtistNameMatchAccuracy, EventDataSource
from .exceptions import HeuristicsException

if TYPE_CHECKING:
    from .data import Event

logger = logging.getLogger(__name__)

cc = coco.CountryConverter()


def get_proxy_url(
    country: str | None = None,
    state: str | None = None,
    city: str | None = None,
    dns_resolution_type: Literal["local", "remote"] | None = None,
) -> str | None:
    address = settings.BRIGHTDATA_PROXY_ADDRESS
    username = settings.BRIGHTDATA_PROXY_USERNAME
    password = settings.BRIGHTDATA_PROXY_PASSWORD

    if address is None or username is None or password is None:
        raise RuntimeError("Proxy settings are not configured")

    if country:
        username += f"-country-{country}"

    if state:
        username += f"-state-{state}"

    if city:
        username += f"-city-{city}"

    if dns_resolution_type:
        username += f"-dns-{dns_resolution_type}"

    return f"http://{username}:{password}@{address}"


def get_normalized_country_name(country_name: str | None) -> str | None:
    if country_name is None:
        return None

    normalized_name: str | None = cc.convert(country_name, to="name_short", not_found=None)  # pyright: ignore[reportArgumentType,reportAssignmentType]
    return normalized_name


def find_best_artist_name_match_simple(
    artist_name: str, available_artists: list[tuple[str, str]]
) -> tuple[str, ArtistNameMatchAccuracy]:
    original_artist_name = artist_name
    artist_name = artist_name.casefold()
    available_artists = [(_, name.casefold()) for _, name in available_artists]

    if matches := [(_, name) for _, name in available_artists if name and artist_name == name]:
        id, _ = matches[0]  # noqa: A001
        return id, ArtistNameMatchAccuracy.exact

    artist_name = artist_name.translate(ALNUM_TABLE)
    if not artist_name:
        raise HeuristicsException(f"Artist '{original_artist_name}' not found")

    transformed = [(_, name.translate(ALNUM_TABLE)) for _, name in available_artists]
    transformed = [(_, name) for _, name in transformed if name]
    if not transformed:
        raise HeuristicsException(f"Artist '{original_artist_name}' not found")

    if matches := [(_, name) for _, name in transformed if artist_name == name]:
        id, _ = matches[0]  # noqa: A001
        return id, ArtistNameMatchAccuracy.exact_alnum

    raise HeuristicsException(f"Artist '{original_artist_name}' not found")


def find_best_artist_name_match_advanced(
    artist_name: str, available_artists: list[tuple[str, str]]
) -> tuple[str, ArtistNameMatchAccuracy]:
    try:
        return find_best_artist_name_match_simple(artist_name, available_artists)
    except HeuristicsException:
        pass

    # TODO: This repeats what's already been done in find_best_artist_name_match_simple
    original_artist_name = artist_name
    artist_name = artist_name.casefold()
    available_artists = [(_, name.casefold()) for _, name in available_artists]

    transliterated_artist_name = unidecode(artist_name)
    transliterated = [(_, unidecode(name)) for _, name in available_artists]

    if matches := [(_, name) for _, name in transliterated if transliterated_artist_name == name]:
        id, _ = matches[0]  # noqa: A001
        return id, ArtistNameMatchAccuracy.exact_ascii

    artist_name = artist_name.encode("ascii", errors="ignore").decode("ascii")
    if not artist_name:
        raise HeuristicsException(f"Artist '{original_artist_name}' not found")

    transformed = [(_, name.encode("ascii", errors="ignore").decode("ascii")) for _, name in available_artists]
    transformed = [(_, name) for _, name in transformed if name]
    if not transformed:
        raise HeuristicsException(f"Artist '{original_artist_name}' not found")

    if matches := [(_, name) for _, name in transformed if artist_name == name]:
        id, _ = matches[0]  # noqa: A001
        return id, ArtistNameMatchAccuracy.exact_ascii

    if matches := [
        (_, name)
        for _, name in available_artists
        if fuzz.ratio(artist_name, name) >= settings.EVENT_ARTIST_NAME_MATCH_THRESHOLD
    ]:
        id, _ = matches[0]  # noqa: A001
        return id, ArtistNameMatchAccuracy.fuzzy

    raise HeuristicsException(f"Artist '{original_artist_name}' not found")

    # from cyrtranslit import to_latin
    # from transliterate import translit
    #
    # [(_, name) for _, name in filtered if CYRILLIC_COMMON & set(name)]
    # [(_, translit(name, "ru", reversed=True)) for _, name in filtered]
    # [(_, translit(name, "uk", reversed=True)) for _, name in filtered]
    # [(_, to_latin(name, "ru")) for _, name in filtered]
    # [(_, to_latin(name, "ua")) for _, name in filtered]


def normalize_string(string: str) -> str:
    return replace_unicode_characters(string.strip().casefold())


def replace_unicode_characters(string: str) -> str:
    # https://www.unicode.org/reports/tr44/
    # https://www.compart.com/en/unicode/category
    # Mark, Number, Punctuation, Symbol
    # https://www.unicode.org/reports/tr44/#General_Category_Values
    return "".join(
        [unidecode(char) if unicodedata.category(char).startswith(("M", "N", "P", "S")) else char for char in string]
    )


def should_fetch_event(event_url: str, event_source: EventDataSource, events: dict[str, "Event"]) -> bool:
    existing_event = events.get(event_url)

    if existing_event is None:
        return True

    if existing_event.source != event_source:
        logger.error("WTF!?")  # TODO: Eh?
        return True

    return not existing_event.tickets_urls and not existing_event.stream_urls
