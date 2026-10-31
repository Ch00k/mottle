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
        ("Isle Of Man", "Isle of Man"),
    ],
)
def test_songkick_country_names_are_shortened(sent: str, shown: str) -> None:
    """The abbreviations and ISO 3166 names Songkick sends become the short names Bandsintown sends."""
    assert get_normalized_country_name(sent) == shown


@pytest.mark.parametrize("name", ["Germany", "United Kingdom", "Czechia", "United Arab Emirates", "Viet Nam"])
def test_other_country_names_are_kept(name: str) -> None:
    """A short name is kept as it is, and so is a name that has no known short form."""
    assert get_normalized_country_name(name) == name


def test_missing_country_stays_missing() -> None:
    """A venue without a country gets none."""
    assert get_normalized_country_name(None) is None
