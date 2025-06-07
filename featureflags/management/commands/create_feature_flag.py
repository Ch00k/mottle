import json
import logging
from typing import Any

from django.core.management import CommandError
from django.core.management.base import BaseCommand

from featureflags.models import FeatureFlag

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Create a new feature flag"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--name", type=str, help="The name of the feature flag")
        parser.add_argument("--value", type=str, help="The value of the feature flag")

    def handle(self, *_: Any, **options: str) -> None:
        name = options.get("name")
        value = options.get("value")

        if not name:
            raise CommandError("You must provide a feature flag name")

        if not value:
            raise CommandError("You must provide a feature flag value")

        FeatureFlag.objects.create(name=name, value=json.loads(value))
