import datetime
import logging
from typing import TYPE_CHECKING

import pytest
from django.test import override_settings

from web.events.data import Event
from web.events.enums import ArtistNameMatchAccuracy, EventDataSource, EventType
from web.events.exceptions import HeuristicsException
from web.events.utils import (
    find_best_artist_name_match_advanced,
    find_best_artist_name_match_simple,
    get_normalized_country_name,
    should_fetch_event,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize(
    ("sent", "shown"),
    [
        ("UK", "United Kingdom"),
        ("US", "United States"),
        ("Czech Republic", "Czechia"),
        ("Korea, Republic Of", "South Korea"),
        ("Moldova, Republic Of", "Moldova"),
        ("Russian Federation", "Russia"),
        ("Macedonia", "North Macedonia"),
        ("Viet Nam", "Vietnam"),
        ("Congo, The Democratic Republic Of The", "DR Congo"),
        ("Cote D'ivoire", "Côte d'Ivoire"),
    ],
)
def test_songkick_country_names_are_shortened(sent: str, shown: str) -> None:
    """The abbreviations and ISO 3166 names Songkick sends become the short names Bandsintown sends.

    Songkick capitalises every word, so the mapping must match whatever case the joining words come in.
    """
    assert get_normalized_country_name(sent) == shown


@pytest.mark.parametrize(
    ("sent", "shown"),
    [("Isle Of Man", "Isle of Man"), ("Bosnia And Herzegovina", "Bosnia and Herzegovina")],
)
def test_capitalised_joining_words_are_lowered(sent: str, shown: str) -> None:
    """A name with no short form still loses Songkick's capitals on "And", "Of" and "The"."""
    assert get_normalized_country_name(sent) == shown


@pytest.mark.parametrize(
    "name", ["Germany", "United Kingdom", "Czechia", "United Arab Emirates", "Congo", "The Gambia"]
)
def test_other_country_names_are_kept(name: str) -> None:
    """A short name is kept as it is, including one that starts with "The"."""
    assert get_normalized_country_name(name) == name


def test_missing_country_stays_missing() -> None:
    """A venue without a country gets none."""
    assert get_normalized_country_name(None) is None


ARTISTS = [("1", "Deep Purple Tribute"), ("2", "Deep Purple"), ("3", "DEEP PURPLE")]


@pytest.mark.parametrize("find_match", [find_best_artist_name_match_simple, find_best_artist_name_match_advanced])
def test_exact_name_matches_first_artist_whatever_the_case(find_match: Callable) -> None:
    """The first artist whose name equals the searched one, ignoring case, is an exact match."""
    assert find_match("deep purple", ARTISTS) == ("2", ArtistNameMatchAccuracy.exact)


@pytest.mark.parametrize("find_match", [find_best_artist_name_match_simple, find_best_artist_name_match_advanced])
def test_name_equal_without_punctuation_and_spaces_is_alnum_match(find_match: Callable) -> None:
    """A name equal to the searched one once punctuation and whitespace are dropped is an alphanumeric match."""
    assert find_match("AC/DC", [("1", "Accept"), ("2", "AC DC")]) == ("2", ArtistNameMatchAccuracy.exact_alnum)


@pytest.mark.parametrize("find_match", [find_best_artist_name_match_simple, find_best_artist_name_match_advanced])
def test_name_of_only_punctuation_matches_nothing(find_match: Callable) -> None:
    """A searched name that is all punctuation matches no artist once the punctuation is dropped."""
    with pytest.raises(HeuristicsException, match=r"^Artist '\?\?\?' not found$"):
        find_match("???", [("1", "Accept")])


@pytest.mark.parametrize("find_match", [find_best_artist_name_match_simple, find_best_artist_name_match_advanced])
def test_artists_named_only_punctuation_match_nothing(find_match: Callable) -> None:
    """Artists whose names are all punctuation match no searched name."""
    with pytest.raises(HeuristicsException, match=r"^Artist 'Deep Purple' not found$"):
        find_match("Deep Purple", [("1", "!!!"), ("2", "...")])


def test_simple_match_does_not_transliterate() -> None:
    """The simple match finds no artist whose name differs from the searched one only in its accents."""
    with pytest.raises(HeuristicsException, match=r"^Artist 'Sigur Rós' not found$"):
        find_best_artist_name_match_simple("Sigur Rós", [("1", "Sigur Ros")])


def test_advanced_match_transliterates_to_ascii() -> None:
    """A name equal to the searched one once both are transliterated to ASCII is an ASCII match."""
    assert find_best_artist_name_match_advanced("Sigur Rós", [("1", "Sigur Ros")]) == (
        "1",
        ArtistNameMatchAccuracy.exact_ascii,
    )


def test_advanced_match_drops_characters_outside_ascii() -> None:
    """A name equal to the searched one once both lose their characters outside ASCII is an ASCII match.

    Transliterating "Björk" gives "bjork", which "Bjrk" is not; dropping the "ö" gives "bjrk".
    """
    assert find_best_artist_name_match_advanced("Björk", [("1", "Bjrk")]) == ("1", ArtistNameMatchAccuracy.exact_ascii)


def test_advanced_match_of_name_without_ascii_characters_matches_nothing() -> None:
    """A searched name with no ASCII characters, which no transliteration matched, matches no artist."""
    with pytest.raises(HeuristicsException, match=r"^Artist 'λ' not found$"):
        find_best_artist_name_match_advanced("λ", [("1", "Lambda")])


@override_settings(EVENT_ARTIST_NAME_MATCH_THRESHOLD=85)
def test_advanced_match_rounds_similarity_up_to_threshold() -> None:
    """A name whose similarity to the searched one rounds to the threshold is a fuzzy match.

    "the deep purple" is 84.6% similar to "deep purple", which rounds to the threshold of 85.
    """
    assert find_best_artist_name_match_advanced("Deep Purple", [("1", "The Deep Purple")]) == (
        "1",
        ArtistNameMatchAccuracy.fuzzy,
    )


@override_settings(EVENT_ARTIST_NAME_MATCH_THRESHOLD=85)
def test_advanced_match_rejects_similarity_below_threshold() -> None:
    """A name whose similarity to the searched one rounds below the threshold matches nothing.

    "deep purple band" is 81.5% similar to "deep purple".
    """
    with pytest.raises(HeuristicsException, match=r"^Artist 'Deep Purple' not found$"):
        find_best_artist_name_match_advanced("Deep Purple", [("1", "Deep Purple Band")])


EVENT_URL = "https://www.songkick.com/concerts/1-deep-purple-at-hamburg-sporthalle"


def songkick_concert(tickets_urls: list[str] | None = None) -> Event:
    return Event(
        source=EventDataSource.songkick,
        url=EVENT_URL,
        type=EventType.concert,
        date=datetime.date(2026, 11, 1),
        tickets_urls=tickets_urls or [],
    )


def test_event_not_known_is_fetched() -> None:
    """An event that was not fetched before is fetched."""
    assert should_fetch_event(EVENT_URL, EventDataSource.songkick, {}) is True


def test_known_event_with_links_is_not_fetched_again() -> None:
    """An event already known with its ticket links is not fetched again."""
    events = {EVENT_URL: songkick_concert(tickets_urls=["https://s.mottle.it/abc1234"])}

    assert should_fetch_event(EVENT_URL, EventDataSource.songkick, events) is False


def test_known_event_without_links_is_fetched_again() -> None:
    """An event known without ticket or stream links is fetched again, as it may have got some since."""
    assert should_fetch_event(EVENT_URL, EventDataSource.songkick, {EVENT_URL: songkick_concert()}) is True


def test_event_known_from_other_source_is_fetched(caplog: pytest.LogCaptureFixture) -> None:
    """An event whose URL is known from the other source is fetched, and the mix-up is logged as an error."""
    events = {EVENT_URL: songkick_concert(tickets_urls=["https://s.mottle.it/abc1234"])}

    assert should_fetch_event(EVENT_URL, EventDataSource.bandsintown, events) is True

    errors = [r.getMessage() for r in caplog.records if r.name == "web.events.utils" and r.levelno == logging.ERROR]
    assert errors == ["WTF!?"]
