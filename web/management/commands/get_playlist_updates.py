import logging

from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand

from web.jobs import check_playlists_for_updates

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Runs all periodic jobs (async)"

    def handle(self, *_: tuple, **__: dict) -> None:
        async_to_sync(check_playlists_for_updates)()
