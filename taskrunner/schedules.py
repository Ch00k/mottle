from datetime import datetime

from croniter.croniter import croniter
from django_q.models import Schedule
from django_q.tasks import schedule
from django_q.utils import localtime

from featureflags.data import FeatureFlag

PLAYLIST_UPDATES_JOB = "web.tasks.check_playlists_for_updates"
EVENT_UPDATES_JOB = "web.tasks.check_artists_for_event_updates"

PLAYLIST_UPDATES_SCHEDULE = FeatureFlag.playlist_updates_schedule()
if PLAYLIST_UPDATES_SCHEDULE is None:
    raise RuntimeError("Playlist updates schedule is not set")

EVENT_UPDATES_SCHEDULE = FeatureFlag.event_updates_schedule()
if EVENT_UPDATES_SCHEDULE is None:
    raise RuntimeError("Event updates schedule is not set")


def playlist_updates() -> None:
    schedule(
        PLAYLIST_UPDATES_JOB,
        send_notifications=True,
        schedule_type=Schedule.CRON,
        cron=PLAYLIST_UPDATES_SCHEDULE,
        next_run=croniter(PLAYLIST_UPDATES_SCHEDULE, localtime()).get_next(datetime),  # pyright: ignore[reportArgumentType]
        cluster="long_running",
    )


def event_updates() -> None:
    schedule(
        EVENT_UPDATES_JOB,
        send_notifications=True,
        concurrent_execution=True,
        concurrency_limit=FeatureFlag.event_fetching_concurrency_limit(),
        schedule_type=Schedule.CRON,
        cron=FeatureFlag.event_updates_schedule(),
        next_run=croniter(EVENT_UPDATES_SCHEDULE, localtime()).get_next(datetime),  # pyright: ignore[reportArgumentType]
        cluster="long_running",
    )
