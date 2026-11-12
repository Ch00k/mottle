import re
from typing import Any

from django.db import migrations

# The ID of an event in its URL's last segment, which the source keeps when it renames the event's URL
EVENT_SOURCE_ID_REGEX = re.compile(r"/(\d+)(?:-[^/]*)?$")


def event_source_id(event_url: str) -> str:
    match = EVENT_SOURCE_ID_REGEX.search(event_url)

    if match is None:
        raise ValueError(f"Event URL has no event ID: {event_url}")

    return match.group(1)


def remove_duplicate_events(apps: Any, _: Any) -> None:
    """Delete the events stored twice, keeping one of each.

    An artist's events on a date that more than one source has an event on are the same concert: the source that had
    one stored first keeps its events of that date. Of the events a source stored under more than one URL of the same
    event, the one stored last keeps its current URL and date.
    """
    Event = apps.get_model("web", "Event")
    events = list(Event.objects.order_by("created_at").only("id", "artist_id", "source", "source_url", "date"))

    first_sources = {}
    for event in events:
        first_sources.setdefault((event.artist_id, event.date), event.source)

    duplicate_ids = set()
    last_stored = {}

    for event in events:
        if event.source != first_sources[(event.artist_id, event.date)]:
            duplicate_ids.add(event.id)
            continue

        key = (event.artist_id, event.source, event_source_id(event.source_url))

        if key in last_stored:
            duplicate_ids.add(last_stored[key].id)

        last_stored[key] = event

    Event.objects.filter(id__in=duplicate_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("web", "0012_remove_playlistupdate_removed_items"),
    ]

    operations = [
        migrations.RunPython(remove_duplicate_events, migrations.RunPython.noop),
    ]
