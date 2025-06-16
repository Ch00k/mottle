from django_q.models import Schedule
from django_q.tasks import schedule

from featureflags.data import FeatureFlag

PLAYLIST_UPDATES_JOB = "web.tasks.check_playlists_for_updates"
EVENT_UPDATES_JOB = "web.tasks.check_artists_for_event_updates"


def playlist_updates() -> None:
    schedule(
        PLAYLIST_UPDATES_JOB,
        send_notifications=True,
        schedule_type=Schedule.CRON,
        cron=FeatureFlag.playlist_updates_schedule(),
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
        cluster="long_running",
    )
