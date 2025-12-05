import logging
from datetime import datetime
from typing import cast

from croniter.croniter import croniter
from django.conf import settings
from django_q.models import Schedule
from django_q.utils import localtime

logger = logging.getLogger(__name__)

PLAYLIST_UPDATES_NAME = "playlist_updates"
PLAYLIST_UPDATES_FUNC = "web.tasks.check_playlists_for_updates"

EVENT_UPDATES_NAME = "event_updates"
EVENT_UPDATES_FUNC = "web.tasks.check_artists_for_event_updates"


def get_next_run(cron_schedule: str) -> datetime:
    return cast("datetime", croniter(cron_schedule, localtime()).get_next(datetime))


def playlist_updates() -> None:
    schedule = settings.SCHEDULE.get("PLAYLIST_UPDATES")
    if schedule is None:
        logger.warning("PLAYLIST_UPDATES schedule is not set. Skipping task creation")
        return

    Schedule.objects.update_or_create(
        name=PLAYLIST_UPDATES_NAME,
        defaults={
            "func": PLAYLIST_UPDATES_FUNC,
            "schedule_type": Schedule.CRON,
            "cron": schedule,
            "cluster": "long_running",
            "next_run": get_next_run(schedule),
        },
    )


def event_updates() -> None:
    schedule = settings.SCHEDULE.get("EVENT_UPDATES")
    if schedule is None:
        logger.warning("EVENT_UPDATES schedule is not set. Skipping task creation")
        return

    Schedule.objects.update_or_create(
        name=EVENT_UPDATES_NAME,
        defaults={
            "func": EVENT_UPDATES_FUNC,
            "schedule_type": Schedule.CRON,
            "cron": schedule,
            "cluster": "long_running",
            "next_run": get_next_run(schedule),
        },
    )
