import logging
import uuid
from typing import Any

from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"
HASH_LENGTH = 7


class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ShortURL(BaseModel):
    url = models.URLField(unique=True)
    hash = models.CharField(max_length=HASH_LENGTH, unique=True)

    def __str__(self) -> str:
        return f"{self.hash} -> {self.url}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.hash:
            self.hash = uuid_to_hash(self.id)
        super().save(*args, **kwargs)

    @staticmethod
    async def shorten(url: str) -> "ShortURL":
        short_url, _ = await ShortURL.objects.aget_or_create(url=url)
        return short_url

    @property
    def full_short_url(self) -> str:
        return f"{settings.URLSHORTENER_BASE_URL}/{self.hash}"


def uuid_to_hash(uuid_obj: uuid.UUID, length: int = HASH_LENGTH) -> str:
    """Convert UUID to a robust alphanumeric short hash."""

    logger.debug(f"Converting UUID {uuid_obj} to hash")

    # This is why str() is used:
    # hash(uuid.UUID('00000000-0000-0000-0000-000000000001')) -> 1
    # hash(str(uuid.UUID('00000000-0000-0000-0000-000000000001'))) -> -8140488190291822392

    hash_value = abs(hash(str(uuid_obj)))
    result: list[str] = []

    while hash_value > 0 and len(result) < length:
        hash_value, remainder = divmod(hash_value, 36)
        result.append(BASE36[remainder])

    final = "".join(reversed(result))
    logger.debug(f"Converted UUID {uuid_obj} to hash {final}")
    return final
