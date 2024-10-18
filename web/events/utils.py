import logging
import unicodedata
from typing import TYPE_CHECKING

from django.conf import settings
from thefuzz import fuzz
from unidecode import unidecode

from .constants import ALNUM_TABLE
from .enums import ArtistNameMatchAccuracy, EventDataSource
from .exceptions import HeuristicsException

if TYPE_CHECKING:
    from .data import Event

logger = logging.getLogger(__name__)


def find_best_artist_name_match_simple(
    artist_name: str, available_artists: list[tuple[str, str]]
) -> tuple[str, ArtistNameMatchAccuracy]:
    original_artist_name = artist_name
    artist_name = artist_name.lower()
    available_artists = [(_, name.lower()) for _, name in available_artists]

    if matches := [(_, name) for _, name in available_artists if name and artist_name == name]:
        id, _ = matches[0]
        return id, ArtistNameMatchAccuracy.exact

    artist_name = artist_name.translate(ALNUM_TABLE)
    if not artist_name:
        raise HeuristicsException(f"Artist '{original_artist_name}' not found")

    transformed = [(_, name.translate(ALNUM_TABLE)) for _, name in available_artists]
    transformed = [(_, name) for _, name in transformed if name]
    if not transformed:
        raise HeuristicsException(f"Artist '{original_artist_name}' not found")

    if matches := [(_, name) for _, name in transformed if artist_name == name]:
        id, _ = matches[0]
        return id, ArtistNameMatchAccuracy.exact_alnum

    raise HeuristicsException(f"Artist '{original_artist_name}' not found")


def find_best_artist_name_match_advanced(
    artist_name: str, available_artists: list[tuple[str, str]]
) -> tuple[str, ArtistNameMatchAccuracy]:
    try:
        return find_best_artist_name_match_simple(artist_name, available_artists)
    except HeuristicsException:
        pass

    original_artist_name = artist_name
    artist_name = artist_name.lower()
    available_artists = [(_, name.lower()) for _, name in available_artists]

    artist_name = artist_name.encode("ascii", errors="ignore").decode("ascii")
    if not artist_name:
        raise HeuristicsException(f"Artist '{original_artist_name}' not found")

    transformed = [(_, name.encode("ascii", errors="ignore").decode("ascii")) for _, name in available_artists]
    transformed = [(_, name) for _, name in transformed if name]
    if not transformed:
        raise HeuristicsException(f"Artist '{original_artist_name}' not found")

    if matches := [(_, name) for _, name in transformed if artist_name == name]:
        id, _ = matches[0]
        return id, ArtistNameMatchAccuracy.exact_ascii

    if matches := [
        (_, name)
        for _, name in available_artists
        if fuzz.ratio(artist_name, name) >= settings.EVENT_ARTIST_NAME_MATCH_THRESHOLD
    ]:
        id, _ = matches[0]
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
    string = string.casefold()
    string = replace_unicode_characters(string)
    return string


def replace_unicode_characters(string: str) -> str:
    # https://www.unicode.org/reports/tr44/
    # https://www.compart.com/en/unicode/category
    result = ""

    for char in string:
        if unicodedata.category(char).startswith(("M", "N", "P", "S")):
            char = unidecode(char)
        result += char

    return result


def should_fetch_event(event_url: str, event_source: EventDataSource, events: dict[str, "Event"]) -> bool:
    existing_event = events.get(event_url)

    if existing_event is None:
        return True

    if existing_event.source != event_source:
        logger.error("WTF!?")  # TODO: Eh?
        return True

    if not existing_event.tickets_urls and not existing_event.stream_urls:
        return True

    return False
