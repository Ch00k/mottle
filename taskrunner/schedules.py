import logging
from datetime import datetime
from typing import cast

from croniter.croniter import croniter
from django_q.models import Schedule
from django_q.utils import localtime

from featureflags.data import FeatureFlag

logger = logging.getLogger(__name__)

PLAYLIST_UPDATES_NAME = "playlist_updates"
PLAYLIST_UPDATES_FUNC = "web.tasks.check_playlists_for_updates"
PLAYLIST_UPDATES_SCHEDULE = FeatureFlag.playlist_updates_schedule()

EVENT_UPDATES_NAME = "event_updates"
EVENT_UPDATES_FUNC = "web.tasks.check_artists_for_event_updates"
EVENT_UPDATES_SCHEDULE = FeatureFlag.event_updates_schedule()


def get_next_run(cron_schedule: str) -> datetime:
    return cast("datetime", croniter(cron_schedule, localtime()).get_next(datetime))


def playlist_updates() -> None:
    if PLAYLIST_UPDATES_SCHEDULE is None:
        raise RuntimeError("playlist_updates schedule is not set")

    Schedule.objects.update_or_create(
        name=PLAYLIST_UPDATES_NAME,
        defaults={
            "func": PLAYLIST_UPDATES_FUNC,
            "kwargs": {"send_notifications": True},
            "schedule_type": Schedule.CRON,
            "cron": PLAYLIST_UPDATES_SCHEDULE,
            "cluster": "long_running",
            "next_run": get_next_run(PLAYLIST_UPDATES_SCHEDULE),
        },
    )


def event_updates() -> None:
    if EVENT_UPDATES_SCHEDULE is None:
        raise RuntimeError("event_updates schedule is not set")

    Schedule.objects.update_or_create(
        name=EVENT_UPDATES_NAME,
        defaults={
            "func": EVENT_UPDATES_FUNC,
            "kwargs": {
                "artist_spotify_ids": None,
                "compile_notifications": True,
                "send_notifications": True,
                "force_refetch": False,
                "concurrent_execution": True,
                # TODO: This will not be updates unltil a restart
                "concurrency_limit": FeatureFlag.event_fetching_concurrency_limit(),
            },
            "schedule_type": Schedule.CRON,
            "cron": EVENT_UPDATES_SCHEDULE,
            "cluster": "long_running",
            "next_run": get_next_run(EVENT_UPDATES_SCHEDULE),
        },
    )
