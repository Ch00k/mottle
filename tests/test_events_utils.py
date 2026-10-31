import pytest

from web.events.utils import get_normalized_country_name


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
