import datetime
import logging
import uuid
from typing import Any, Optional

from django.conf import settings
from django.db import models
from tekore import Token

TOKEN_EXPIRATION_THRESHOLD = 60

logger = logging.getLogger(__name__)


def encrypt_value(value: str) -> str:
    return settings.SPOTIFY_TOKEN_CRYPTER.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_value(value: str) -> str:
    return settings.SPOTIFY_TOKEN_CRYPTER.decrypt(value).decode("utf-8")


class EncryptedCharField(models.CharField):
    def to_python(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        ret: str = super().to_python(decrypt_value(value))
        return ret

    def from_db_value(self, value: Optional[str], _: Any, __: Any) -> Optional[str]:
        return self.to_python(value)

    def get_prep_value(self, value: Optional[str]) -> Optional[str]:
        return value

    def get_db_prep_value(self, value: Optional[str], connection: Any, prepared: bool = False) -> Optional[str]:
        value = super().get_db_prep_value(value, connection, prepared)
        if value is None:
            return value
        return encrypt_value(value)


class SpotifyAuth(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    redirect_uri = models.CharField(max_length=100, null=True)
    state = models.CharField(max_length=50, null=True, unique=True)
    access_token = EncryptedCharField(max_length=450, null=True)
    refresh_token = EncryptedCharField(max_length=300, null=True)
    expires_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"<SpotifyAuth {self.id} expires_at={self.expires_at}>"

    @property
    def expires_in(self) -> float:
        if self.expires_at is None:
            return 0

        delta = self.expires_at - datetime.datetime.now(tz=datetime.timezone.utc)
        return delta.total_seconds()

    @property
    def is_expiring(self) -> bool:
        if self.expires_in <= 0:
            logger.info(f"{self} is expired")
            return True
        elif self.expires_in < TOKEN_EXPIRATION_THRESHOLD:
            logger.info(f"{self} is expiring in {self.expires_in} seconds")
            return True
        else:
            return False

    @property
    def as_tekore_token(self) -> Token:
        return Token(
            token_info={
                "token_type": "Bearer",
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_in": self.expires_in,
            },
            uses_pkce=False,
        )

    async def unset_state(self) -> None:
        self.state = None
        await self.asave()

    async def update_from_tekore_token(self, token: Token) -> None:
        self.access_token = token.access_token
        self.refresh_token = token.refresh_token
        self.expires_at = datetime.datetime.fromtimestamp(token.expires_at)
        await self.asave()
