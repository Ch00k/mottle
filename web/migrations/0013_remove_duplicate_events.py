import re
from typing import TYPE_CHECKING, Any

from django.db import migrations

if TYPE_CHECKING:
    from datetime import datetime

# The ID of an event in its URL's last segment, which the source keeps when it renames the event's URL
EVENT_SOURCE_ID_REGEX = re.compile(r"/(\d+)(?:-[^/]*)?$")


def event_source_id(event_url: str) -> str:
    match = EVENT_SOURCE_ID_REGEX.search(event_url)

    if match is None:
        raise ValueError(f"Event URL has no event ID: {event_url}")

    return match.group(1)


def remove_duplicate_events(apps: Any, _: Any) -> None:
    """Delete the events stored twice, keeping one of each.

    Of the events a source stored under more than one URL of the same event, the one stored last keeps its current URL
    and date. Then an artist's events on a date that more than one source has an event on are the same concert: the
    source whose event was first stored, under any of its URLs, keeps its events of that date.
    """
    Event = apps.get_model("web", "Event")
    events = Event.objects.order_by("created_at").only("id", "artist_id", "source", "source_url", "date", "created_at")

    duplicate_ids = set()
    current_versions: dict[tuple[Any, str, str], Any] = {}
    first_stored_at: dict[tuple[Any, str, str], datetime] = {}

    for event in events:
        key = (event.artist_id, event.source, event_source_id(event.source_url))

        if key in current_versions:
            duplicate_ids.add(current_versions[key].id)

        current_versions[key] = event
        first_stored_at.setdefault(key, event.created_at)

    first_sources = {}
    for key in sorted(current_versions, key=lambda key: first_stored_at[key]):
        event = current_versions[key]
        first_sources.setdefault((event.artist_id, event.date), event.source)

    for event in current_versions.values():
        if event.source != first_sources[(event.artist_id, event.date)]:
            duplicate_ids.add(event.id)

    Event.objects.filter(id__in=duplicate_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("web", "0012_remove_playlistupdate_removed_items"),
    ]

    operations = [
        migrations.RunPython(remove_duplicate_events, migrations.RunPython.noop),
    ]
