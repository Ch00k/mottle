import uuid
from typing import Any

from django.db import models


class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class FeatureFlagManager(models.Manager["FeatureFlag"]):
    async def get_value(self, name: str) -> Any | None:
        try:
            f = await self.aget(name=name)
        except FeatureFlag.DoesNotExist:
            return None
        else:
            return f.value


class FeatureFlag(BaseModel):
    name = models.CharField(max_length=250, unique=True)
    value = models.JSONField(null=True)

    objects = FeatureFlagManager()

    def __str__(self) -> str:
        return f"<FeatureFlag {self.id} {self.name}>"
