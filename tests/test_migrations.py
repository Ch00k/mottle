import datetime
import importlib

import pytest
from django.apps import apps

from web.events.enums import EventDataSource, EventType
from web.models import Artist, Event, EventArtist

remove_duplicate_events = importlib.import_module("web.migrations.0013_remove_duplicate_events").remove_duplicate_events


@pytest.mark.django_db
def test_remove_duplicate_events_keeps_one_of_each_event() -> None:
    """Of the same concert on a date known from both sources, the source that had it first keeps its event; of an event
    a source stored under two URLs, the one stored last is kept.

    Two events of one source on the same date, with different IDs, are different events, and both are kept.
    """
    event_artist = EventArtist.objects.create(
        artist=Artist.objects.create(spotify_id="placebo"),
        songkick_name_match_accuracy=100,
        bandsintown_name_match_accuracy=100,
    )

    def store(source: EventDataSource, url: str, date: datetime.date) -> Event:
        return Event.objects.create(
            artist=event_artist, source=source, source_url=url, type=EventType.concert, date=date
        )

    bandsintown_first = store(
        EventDataSource.bandsintown,
        "https://www.bandsintown.com/e/108105230-placebo-at-atlas-arena",
        datetime.date(2026, 11, 18),
    )
    store(
        EventDataSource.songkick,
        "https://www.songkick.com/concerts/43108780-placebo-at-atlas-arena",
        datetime.date(2026, 11, 18),
    )
    store(
        EventDataSource.songkick,
        "https://www.songkick.com/concerts/42947072-placebo-at-gasometer",
        datetime.date(2026, 11, 24),
    )
    renamed = store(
        EventDataSource.songkick,
        "https://www.songkick.com/concerts/42947072-placebo-at-the-gasometer",
        datetime.date(2026, 11, 25),
    )
    matinee = store(
        EventDataSource.songkick,
        "https://www.songkick.com/concerts/42732021-placebo-at-folies-bergere",
        datetime.date(2026, 12, 13),
    )
    evening = store(
        EventDataSource.songkick,
        "https://www.songkick.com/concerts/42732022-placebo-at-folies-bergere",
        datetime.date(2026, 12, 13),
    )

    remove_duplicate_events(apps, None)

    assert set(Event.objects.values_list("id", flat=True)) == {bandsintown_first.id, renamed.id, matinee.id, evening.id}
