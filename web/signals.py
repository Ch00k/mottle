import logging
from typing import Any

from django.db.models import signals
from django.dispatch import receiver

from .models import Event, EventArtist, EventUpdate

logger = logging.getLogger(__name__)


@receiver(signals.pre_save, sender=EventArtist)
def handle_pre_save_event_artist(instance: EventArtist, **__: Any) -> None:
    old_values = instance.get_dirty_fields()
    if not old_values:
        return

    changes = {}
    for field, old_value in old_values.items():
        new_value = getattr(instance, field)
        changes[field] = {"old": old_value, "new": new_value}

    logger.debug(f"EventArtist {instance} has changes: {changes}")


@receiver(signals.post_save, sender=Event)
def handle_post_save_event(instance: Event, created: bool, **__: Any) -> None:
    logger.debug(f"Received post_save signal for {instance}")

    if created:
        EventUpdate.objects.create(event=instance, type=EventUpdate.FULL)
        return

    old_values = instance.get_dirty_fields()

    if "stream_urls" not in old_values and "tickets_urls" not in old_values and "geolocation" not in old_values:
        logger.debug(f"No changes detected for {instance}")
        return

    changes = {}

    if "stream_urls" in old_values:
        changes["stream_urls"] = {"old": old_values["stream_urls"], "new": instance.stream_urls}

    if "tickets_urls" in old_values:
        changes["tickets_urls"] = {"old": old_values["tickets_urls"], "new": instance.tickets_urls}

    if "geolocation" in old_values:
        changes["geolocation"] = {"old": old_values["geolocation"], "new": instance.geolocation}

    EventUpdate.objects.create(event=instance, type=EventUpdate.PARTIAL, changes=changes)
