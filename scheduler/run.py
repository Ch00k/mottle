import asyncio
import logging
import signal
import sys
from functools import partial
from typing import Any

import django
from django.conf import settings

django.setup()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from prometheus_client import start_http_server

from scheduler.jobs import get_event_updates, get_playlist_updates

logger = logging.getLogger(__name__)


async def run() -> None:
    metrics_server, t = start_http_server(port=int(sys.argv[2]), addr=sys.argv[1])

    loop = asyncio.get_running_loop()
    loop_shutdown_event = asyncio.Event()

    scheduler = AsyncIOScheduler(event_loop=loop)

    if schedule_playlist_updates := settings.SCHEDULE["PLAYLIST_UPDATES"]:
        logger.info(f"Setting up playlist updates job with schedule {schedule_playlist_updates}")
        scheduler.add_job(get_playlist_updates, CronTrigger.from_crontab(schedule_playlist_updates))
    else:
        logger.warning("No schedule for playlist updates, skipping...")

    if schedule_event_updates := settings.SCHEDULE["EVENT_UPDATES"]:
        logger.info(f"Setting up event updates job with schedule {schedule_event_updates}")
        scheduler.add_job(get_event_updates, CronTrigger.from_crontab(schedule_event_updates))
    else:
        logger.warning("No schedule for event updates, skipping...")

    scheduler.start()

    def signal_handler_metrics_server(signum: int, _: Any) -> None:
        logger.info(f"Received signal {signal.Signals(signum).name}, shutting down metrics server...")
        metrics_server.shutdown()
        t.join()

    def signal_handler_scheduler(signum: int) -> None:
        logger.info(f"Received signal {signal.Signals(signum).name}, shutting down scheduler and its event loop...")
        scheduler.shutdown()
        loop_shutdown_event.set()

    for s in [signal.SIGHUP, signal.SIGINT, signal.SIGTERM]:
        loop.add_signal_handler(s, partial(signal_handler_scheduler, s))
        signal.signal(s, signal_handler_metrics_server)

    await loop_shutdown_event.wait()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    logger.debug("Starting scheduler")
    main()
