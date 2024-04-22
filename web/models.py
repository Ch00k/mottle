import datetime
import hashlib
import logging
import uuid
from typing import Any, Optional

from django.conf import settings
from django.db import models
from tekore import Token
from tekore.model import PrivateUser

from .spotify import authenticate
from .utils import MottleException

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


class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SpotifyAuth(BaseModel):
    state = models.CharField(max_length=50, null=True, unique=True)
    redirect_uri = models.CharField(max_length=100, null=True)
    access_token = EncryptedCharField(max_length=450, null=True)
    refresh_token = EncryptedCharField(max_length=300, null=True)
    expires_at = models.DateTimeField(null=True)

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
            logger.info(f"{self} has expired")
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

    async def request(self, code: str) -> None:
        if self.state is None:
            raise MottleException(f"{self}: state is None")

        try:
            token = authenticate(code, self.state)
        except Exception as e:
            raise MottleException(f"Failed to authenticate: {e}")

        await self.update_from_tekore_token(token)

    async def refresh(self) -> None:
        if not self.is_expiring:
            logger.info(f"{self} is expiring in {self.expires_in} seconds, no need for a refresh yet")
            return

        logger.debug(f"Refreshing {self}")

        try:
            tekore_token = settings.SPOTIFY_CREDEINTIALS.refresh(self.as_tekore_token)
        except Exception as e:
            raise MottleException(f"Failed to refresh token: {e}")

        await self.update_from_tekore_token(tekore_token)
        logger.debug(f"{self} refreshed")

    async def update_from_tekore_token(self, token: Token) -> None:
        self.access_token = token.access_token
        self.refresh_token = token.refresh_token
        self.expires_at = datetime.datetime.fromtimestamp(token.expires_at)
        if self.state is not None:
            self.state = None
        await self.asave()


class SpotifyEntityModel(BaseModel):
    spotify_id = models.CharField(max_length=32, unique=True)

    class Meta:
        abstract = True


class SpotifyUser(SpotifyEntityModel):
    display_name = models.CharField(max_length=48, null=True)
    email = models.EmailField(null=True)

    def __str__(self) -> str:
        return f"<SpotifyUser {self.id} spotify_id={self.spotify_id}>"

    @property
    def has_playlists(self) -> bool:
        return self.playlists.exists()  # pyright: ignore


class Playlist(SpotifyEntityModel):
    spotify_user = models.ForeignKey(SpotifyUser, null=True, on_delete=models.CASCADE, related_name="playlists")
    watched_playlists = models.ManyToManyField("self", symmetrical=False)

    def __str__(self) -> str:
        return f"<Playlist {self.id} spotify_id={self.spotify_id}>"

    @property
    def has_owner(self) -> bool:
        return self.spotify_user is not None

    @property
    def watches_others(self) -> bool:
        return self.watched_playlists.exists()

    @property
    def is_watched_by_others(self) -> bool:
        return Playlist.objects.filter(watched_playlists=self).exists()

    @property
    def is_standalone(self) -> bool:
        return not self.watches_others and not self.is_watched_by_others

    @property
    def watchers(self) -> models.QuerySet["Playlist"]:
        return Playlist.objects.filter(watched_playlists=self)

    async def is_watched_by_others_than(self, playlist: "Playlist") -> bool:
        return await Playlist.objects.filter(watched_playlists=self).exclude(spotify_id=playlist.spotify_id).aexists()

    async def unfollow(self) -> None:
        await self.watched_playlists.aclear()
        self.spotify_user = None
        await self.asave()

    async def watch(self, playlist: "Playlist") -> None:
        await self.watched_playlists.aadd(playlist)

    async def unwatch(self, playlist: "Playlist") -> None:
        await self.watched_playlists.aremove(playlist)


class PlaylistUpdate(BaseModel):
    target_playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name="updates")
    source_playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, null=True, related_name="provided_updates")
    source_artist = models.ForeignKey(Playlist, on_delete=models.CASCADE, null=True)

    albums_added = models.JSONField(null=True)
    albums_removed = models.JSONField(null=True)
    tracks_added = models.JSONField(null=True)
    tracks_removed = models.JSONField(null=True)

    update_hash = models.CharField(max_length=64, unique=True)
    is_notified_of = models.BooleanField(default=False)
    is_accepted = models.BooleanField(null=True, default=None)
    is_overridden_by = models.ForeignKey("self", on_delete=models.CASCADE, null=True, related_name="overrides")

    def __str__(self) -> str:
        return f"<PlaylistUpdate {self.id} hash={self.update_hash}>"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.update_hash = generate_playlist_update_hash(
            self.albums_added,
            self.albums_removed,
            self.tracks_added,
            self.tracks_removed,
        )
        super().save(*args, **kwargs)


def generate_playlist_update_hash(
    albums_added: Optional[list[str]] = None,
    albums_removed: Optional[list[str]] = None,
    tracks_added: Optional[list[str]] = None,
    tracks_removed: Optional[list[str]] = None,
) -> str:
    ids = (
        ["+album"]
        + sorted(albums_added or [])
        + ["-album"]
        + sorted(albums_removed or [])
        + ["+track"]
        + sorted(tracks_added or [])
        + ["-track"]
        + sorted(tracks_removed or [])
    )
    return hashlib.sha256("".join(ids).encode("utf-8")).hexdigest()


async def watch_playlist(
    watching_playlist_spotify_id: str, watched_playlist_spotify_id: str, spotify_user: PrivateUser
) -> None:
    user, created = await SpotifyUser.objects.aupdate_or_create(
        spotify_id=spotify_user.id, defaults={"display_name": spotify_user.display_name, "email": spotify_user.email}
    )
    if created:
        logger.debug(f"Created user: {user}")
    else:
        logger.debug(f"User already existed: {user}")

    watching_playlist, created = await Playlist.objects.aget_or_create(
        spotify_id=watching_playlist_spotify_id, defaults={"spotify_user": user}
    )
    if created:
        logger.debug(f"Created watching playlist: {watching_playlist}")
    else:
        logger.debug(f"Watching playlist already existed: {watching_playlist}")

    watched_playlist, created = await Playlist.objects.aget_or_create(spotify_id=watched_playlist_spotify_id)
    if created:
        logger.debug(f"Created watched playlist: {watched_playlist}")
    else:
        logger.debug(f"Watched playlist already existed: {watched_playlist}")

    await watching_playlist.watch(watched_playlist)


# TODO: This logic is way too complexm and most likey YAGNI
# async def unfollow_playlist(playlist: Playlist) -> None:
#     playlist_owner = playlist.spotify_user

#     if playlist.is_standalone:
#         logger.debug(f"{playlist} has no relations to other playlists. Deleting")
#         await playlist.adelete()
#     else:
#         if playlist.watches_others:
#             async for watched_playlist in playlist.watched_playlists.all():
#                 if watched_playlist.is_watched_by_others_than(playlist):
#                     await playlist.watched_playlists.aremove(watched_playlist)
#                 else:
#                     await watched_playlist.adelete()

#         playlist.spotify_user = None
#         await playlist.asave()

#     if playlist_owner is None:
#         return

#     # If the user has no playlists, it has no use in the database
#     if not playlist_owner.has_playlists:
#         logger.debug(f"Deleting {playlist_owner}")
#         await playlist_owner.adelete()


# async def unwatch_playlist(watching_playlist: Playlist, watched_playlist: Playlist) -> None:
#     watching_playlist_owner = watching_playlist.spotify_user

#     should_delete_watched_playlist = (
#         not watched_playlist.has_owner and not await watched_playlist.is_watched_by_others_than(watching_playlist)
#     )

#     if should_delete_watched_playlist:
#         logger.debug(f"Deleting watched playlist {watched_playlist}")
#         await watched_playlist.adelete()
#     else:
#         # Otherwise, remove it from the watched playlists of the target playlist
#         logger.debug(f"Removing {watched_playlist} from those watched by {watching_playlist}")
#         await watching_playlist.watched_playlists.aremove(watched_playlist)

#     if await sync_to_async(lambda: watching_playlist.is_standalone)():
#         logger.debug(f"Deleting watching playlist {watching_playlist}")
#         await watching_playlist.adelete()
#     else:
#         if not await sync_to_async(lambda: watching_playlist.watches_others)():
#             logger.debug(f"{watching_playlist} watches no other playlists. Removing owner")
#             watching_playlist.spotify_user = None
#             await watching_playlist.asave()

#     if watching_playlist_owner is None:
#         # This should never be the case
#         return

#     # If the user has no playlists, it has no use in the database
#     if not await sync_to_async(lambda: watching_playlist_owner.has_playlists)():
#         logger.debug(f"Deleting {watching_playlist_owner}")
#         await watching_playlist_owner.adelete()
