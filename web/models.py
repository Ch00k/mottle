import datetime
import uuid

from django.db import models
from tekore import Token

TOKEN_EXPIRATION_THRESHOLD = 60


class SpotifyAuth(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    redirect_uri = models.CharField(max_length=500, null=True)
    state = models.CharField(max_length=100, unique=True)
    access_token = models.CharField(max_length=500, null=True)
    refresh_token = models.CharField(max_length=300, null=True)
    expires_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"SpotifyAuth {self.id} state={self.state} expires_at={self.expires_at}"

    @property
    def expires_in(self) -> float:
        if self.expires_at is None:
            return 0

        delta = self.expires_at - datetime.datetime.now(tz=datetime.timezone.utc)
        return delta.total_seconds()

    @property
    def is_expiring(self) -> bool:
        return self.expires_in < TOKEN_EXPIRATION_THRESHOLD

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

    async def update_from_tekore_token(self, token: Token) -> None:
        self.access_token = token.access_token
        self.refresh_token = token.refresh_token
        self.expires_at = datetime.datetime.fromtimestamp(token.expires_at)
        await self.asave()
