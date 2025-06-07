import asyncio
import logging
from typing import Any, cast

from django.core.management.base import BaseCommand

from web.jobs import check_artists_for_event_updates

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Add artists to those that need to be checked for event updates"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--force-refetch",
            action="store_true",
            default=False,
            help="Force refetching events from event sources",
        )
        parser.add_argument(
            "--concurrent-execution",
            action="store_true",
            default=False,
            help="Process all artists concurrently (useful for large playlists)",
        )

    def handle(self, *_: Any, **options: str) -> None:
        force_refetch: bool = cast(bool, options.get("force_refetch", False))  # pyright: ignore[reportAssignmentType]
        concurrent_execution: bool = cast(bool, options.get("concurrent_execution", False))  # pyright: ignore[reportAssignmentType]

        asyncio.run(
            check_artists_for_event_updates(
                compile_notifications=False,
                force_refetch=force_refetch,
                concurrent_execution=concurrent_execution,
            )
        )
