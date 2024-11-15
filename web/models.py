import datetime
import hashlib
import logging
import uuid
from typing import Any

from asgiref.sync import sync_to_async
from dirtyfields import DirtyFieldsMixin
from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.contrib.gis.geos import Point
from django.db import models
from tekore import Token
from tekore.model import PlaylistTrack

from web.events.data import EventSourceArtist
from web.events.enums import EventDataSource, EventType

from .events.data import Event as FetchedEvent
from .events.data import Venue as FetchedVenue
from .spotify import authenticate
from .utils import MottleException, MottleSpotifyClient

TOKEN_EXPIRATION_THRESHOLD = 60

logger = logging.getLogger(__name__)


def encrypt_value(value: str) -> str:
    return settings.SPOTIFY_TOKEN_CRYPTER.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_value(value: str) -> str:
    return settings.SPOTIFY_TOKEN_CRYPTER.decrypt(value).decode("utf-8")


class EncryptedCharField(models.CharField):
    def to_python(self, value: str | None) -> str | None:
        if value is None:
            return value
        ret: str = super().to_python(decrypt_value(value))
        return ret

    def from_db_value(self, value: str | None, _: Any, __: Any) -> str | None:
        return self.to_python(value)

    def get_prep_value(self, value: str | None) -> str | None:
        return value

    def get_db_prep_value(self, value: str | None, connection: Any, prepared: bool = False) -> str | None:
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


class SpotifyEntityModel(BaseModel):
    spotify_id = models.CharField(max_length=32, unique=True)

    class Meta:
        abstract = True


class SpotifyUser(SpotifyEntityModel):
    display_name = models.CharField(max_length=48, null=True)
    email = models.EmailField(null=True)
    location = gis_models.PointField(null=True, srid=settings.GEODJANGO_SRID)

    def __str__(self) -> str:
        return f"<SpotifyUser {self.id} spotify_id={self.spotify_id}>"


class SpotifyAuthRequest(BaseModel):
    state = models.CharField(max_length=50, unique=True)
    redirect_uri = models.CharField(max_length=100, null=True)

    def __str__(self) -> str:
        return f"<SpotifyAuthRequest {self.id} state={self.state}>"

    async def request_token(self, code: str) -> Token:
        if self.state is None:
            raise MottleException(f"{self}: state is None")

        try:
            token = authenticate(code, self.state)
        except Exception as e:
            raise MottleException(f"Failed to authenticate: {e}")

        return token


class SpotifyAuth(BaseModel):
    spotify_user = models.OneToOneField(SpotifyUser, on_delete=models.CASCADE, related_name="spotify_auth")
    access_token = EncryptedCharField(max_length=450, null=True)
    refresh_token = EncryptedCharField(max_length=300, null=True)
    expires_at = models.DateTimeField(null=True)
    token_scope = models.JSONField()

    def __str__(self) -> str:
        return f"<SpotifyAuth {self.id} expires_at={self.expires_at}>"

    @property
    def expires_in(self) -> float:
        if self.expires_at is None:
            return 0

        delta = self.expires_at - datetime.datetime.now(tz=datetime.UTC)
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

    async def maybe_refresh(self) -> None:
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
        await self.asave()


class Artist(SpotifyEntityModel):
    def __str__(self) -> str:
        return f"<Artist {self.id} spotify_id={self.spotify_id}>"

    async def get_album_ids(self, spotify_client: MottleSpotifyClient) -> list[str]:
        albums = await spotify_client.get_artist_albums(self.spotify_id)
        return [album.id for album in albums]


class EventArtist(BaseModel):
    artist = models.OneToOneField(Artist, on_delete=models.CASCADE, related_name="event_artist")  # TODO: Cascade?
    musicbrainz_id = models.UUIDField(null=True)
    songkick_url = models.CharField(max_length=2000, null=True)
    bandsintown_url = models.CharField(max_length=2000, null=True)
    songkick_name_match_accuracy = models.IntegerField()
    bandsintown_name_match_accuracy = models.IntegerField()
    watching_users = models.ManyToManyField(SpotifyUser, related_name="watched_event_artists")

    def __str__(self) -> str:
        return f"<EventArtist {self.id}>"

    async def as_fetched_artist(self) -> EventSourceArtist:
        return EventSourceArtist(
            name="dummy",
            songkick_url=self.songkick_url,
            bandsintown_url=self.bandsintown_url,
            events=[
                e.as_fetched_event()
                async for e in self.events.all()  # pyright: ignore
            ],  # TODO: This is ineficient
        )

    @staticmethod
    async def create_from_fetched_artist(
        artist: Artist,
        musicbrainz_id: str | None,
        fetched_artist: EventSourceArtist,
        watching_spotify_user_ids: list[str],
    ) -> "EventArtist":
        event_artist = await EventArtist.objects.acreate(
            artist=artist,
            musicbrainz_id=musicbrainz_id,
            songkick_url=fetched_artist.songkick_url,
            bandsintown_url=fetched_artist.bandsintown_url,
            songkick_name_match_accuracy=fetched_artist.songkick_match_accuracy,
            bandsintown_name_match_accuracy=fetched_artist.bandsintown_match_accuracy,
        )
        # https://github.com/typeddjango/django-stubs/issues/997
        await event_artist.watching_users.aset(watching_spotify_user_ids)  # type: ignore # pyright: ignore
        return event_artist

    async def update_events(self) -> None:
        fetched_artist = await self.as_fetched_artist()
        await fetched_artist.fetch_events()

        for event in fetched_artist.events:
            await Event.update_or_create_from_fetched_event(event, self)


class Event(DirtyFieldsMixin, BaseModel):
    artist = models.ForeignKey(EventArtist, on_delete=models.CASCADE, related_name="events")
    source = models.CharField(max_length=50, choices=[(e.name, e.value) for e in EventDataSource])
    source_url = models.CharField(max_length=2000)
    type = models.CharField(max_length=50, choices=[(e.name, e.value) for e in EventType])
    date = models.DateField()
    venue = models.CharField(max_length=200, null=True)
    postcode = models.CharField(max_length=50, null=True)
    address = models.CharField(max_length=1000, null=True)
    city = models.CharField(max_length=100, null=True)
    country = models.CharField(max_length=100, null=True)
    geolocation = gis_models.PointField(null=True, srid=settings.GEODJANGO_SRID)
    stream_urls = models.JSONField(null=True)
    tickets_urls = models.JSONField(null=True)

    @staticmethod
    async def update_or_create_from_fetched_event(
        fetched_event: FetchedEvent, event_artist: EventArtist
    ) -> tuple["Event", bool]:
        venue = fetched_event.venue
        if venue is None:
            venue_name = None
            postcode = None
            address = None
            city = None
            country = None
            geolocation = None
        else:
            venue_name = venue.name
            postcode = venue.postcode
            address = venue.address
            city = venue.city
            country = venue.country
            geolocation = Point(venue.geo_lon, venue.geo_lat, srid=settings.GEODJANGO_SRID)

        return await Event.objects.aupdate_or_create(
            artist=event_artist,
            source=fetched_event.source,
            source_url=fetched_event.url,
            defaults={
                "type": fetched_event.type,
                "date": fetched_event.date,
                "venue": venue_name,
                "postcode": postcode,
                "address": address,
                "city": city,
                "country": country,
                "geolocation": geolocation,
                "stream_urls": fetched_event.stream_urls,
                "tickets_urls": fetched_event.tickets_urls,
            },
        )

    def as_fetched_event(self) -> FetchedEvent:
        if self.venue is None or self.geolocation is None:
            venue = None
        else:
            venue = FetchedVenue(
                name=self.venue,
                postcode=self.postcode,
                address=self.address,
                city=self.city,
                country=self.country,
                geo_lat=self.geolocation.y,
                geo_lon=self.geolocation.x,
            )

        return FetchedEvent(
            source=EventDataSource(self.source),
            url=self.source_url,
            type=EventType(self.type),
            date=self.date,
            venue=venue,
            stream_urls=self.stream_urls,
            tickets_urls=self.tickets_urls,
        )


class EventUpdate(BaseModel):
    FULL = "full"
    PARTIAL = "partial"
    EVENT_UPDATE_TYPES = [(FULL, "full"), (PARTIAL, "partial")]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="updates")
    type = models.CharField(max_length=50, choices=EVENT_UPDATE_TYPES)
    changes = models.JSONField(null=True)
    is_notified_of = models.BooleanField(default=False)


class Playlist(SpotifyEntityModel):
    spotify_user = models.ForeignKey(SpotifyUser, null=True, on_delete=models.CASCADE, related_name="playlists")

    def __str__(self) -> str:
        return f"<Playlist {self.id} spotify_id={self.spotify_id}>"

    async def unfollow(self) -> None:
        await self.updates.all().adelete()  # pyright: ignore
        await self.configs_as_watching.all().adelete()  # pyright: ignore
        self.spotify_user = None
        await self.asave()
        # TODO: Delete the playlist itself

    @classmethod
    async def watch_playlist(
        cls,
        spotify_client: MottleSpotifyClient,
        watching_playlist_spotify_id: str,
        watched_playlist_spotify_id: str,
        auto_accept_updates: bool = False,
    ) -> None:
        # TODO: Keep email and display name in session?
        spotify_user = await spotify_client.get_current_user()

        user, created = await SpotifyUser.objects.aupdate_or_create(
            spotify_id=spotify_user.id,
            defaults={"display_name": spotify_user.display_name, "email": spotify_user.email},
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

        await PlaylistWatchConfig.objects.acreate(
            watching_playlist=watching_playlist,
            watched_playlist=watched_playlist,
            auto_accept_updates=auto_accept_updates,
        )

    # TODO: A lot of duplicated code with watch_playlist
    @classmethod
    async def watch_artist(
        cls,
        spotify_client: MottleSpotifyClient,
        watching_playlist_spotify_id: str,
        watched_artist_spotify_id: str,
        albums_ignored: list[str] | None = None,
        auto_accept_updates: bool = False,
    ) -> None:
        # TODO: Keep email and display name in session?
        spotify_user = await spotify_client.get_current_user()

        user, created = await SpotifyUser.objects.aupdate_or_create(
            spotify_id=spotify_user.id,
            defaults={"display_name": spotify_user.display_name, "email": spotify_user.email},
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

        watched_artist, created = await Artist.objects.aget_or_create(spotify_id=watched_artist_spotify_id)
        if created:
            logger.debug(f"Created watched artist: {watched_artist}")
        else:
            logger.debug(f"Watched artist already existed: {watched_artist}")

        await PlaylistWatchConfig.objects.acreate(
            watching_playlist=watching_playlist,
            watched_artist=watched_artist,
            albums_ignored=albums_ignored,
            auto_accept_updates=auto_accept_updates,
        )

    async def unwatch(self, playlist: "Playlist") -> None:
        await self.updates.filter(source_playlist=playlist).adelete()  # pyright: ignore
        await self.configs_as_watching.filter(watched_playlist=playlist).adelete()  # pyright: ignore

    async def get_tracks(self, spotify_client: MottleSpotifyClient) -> list[PlaylistTrack]:
        return await spotify_client.get_playlist_tracks(self.spotify_id)

    async def get_track_ids(self, spotify_client: MottleSpotifyClient) -> list[str]:
        return [
            track.track.id
            for track in await self.get_tracks(spotify_client)
            if track.track is not None and track.track.id is not None
        ]

    @property
    def pending_updates(self) -> models.QuerySet["PlaylistUpdate"]:
        return self.updates.filter(is_overridden_by=None, is_accepted=None)  # pyright: ignore


class PlaylistWatchConfig(BaseModel):
    watching_playlist = models.ForeignKey(Playlist, on_delete=models.RESTRICT, related_name="configs_as_watching")
    watched_playlist = models.ForeignKey(
        Playlist, null=True, on_delete=models.RESTRICT, related_name="configs_as_watched_playlist"
    )
    watched_artist = models.ForeignKey(
        Artist, null=True, on_delete=models.RESTRICT, related_name="configs_as_watched_artist"
    )
    auto_accept_updates = models.BooleanField(default=False)
    albums_ignored = models.JSONField(null=True)
    tracks_ignored = models.JSONField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(models.Q(watched_playlist__isnull=False) | models.Q(watched_artist__isnull=False))
                    & ~models.Q(watched_playlist__isnull=False, watched_artist__isnull=False)
                ),
                name="watched_playlist_or_artist",
            )
        ]

    def __str__(self) -> str:
        return f"<PlaylistWatchConfig {self.id}>"


class PlaylistUpdate(BaseModel):
    target_playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name="updates")
    source_playlist = models.ForeignKey(
        Playlist, on_delete=models.CASCADE, null=True, related_name="provided_updates_playlist"
    )
    source_artist = models.ForeignKey(
        Artist, on_delete=models.CASCADE, null=True, related_name="provided_updates_artist"
    )

    albums_added = models.JSONField(null=True)
    albums_removed = models.JSONField(null=True)
    tracks_added = models.JSONField(null=True)
    tracks_removed = models.JSONField(null=True)

    update_hash = models.CharField(max_length=64)
    is_notified_of = models.BooleanField(default=False)
    is_accepted = models.BooleanField(null=True, default=None)
    is_overridden_by = models.ForeignKey("self", on_delete=models.CASCADE, null=True, related_name="overrides")

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(models.Q(source_playlist__isnull=False) | models.Q(source_artist__isnull=False))
                    & ~models.Q(source_playlist__isnull=False, source_artist__isnull=False)
                ),
                name="source_playlist_or_artist",
            ),
            models.UniqueConstraint(
                fields=["target_playlist", "update_hash"], name="unique_target_playlist_update_hash"
            ),
        ]

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

    @property
    def is_auto_acceptable(self) -> bool:
        try:
            config = PlaylistWatchConfig.objects.get(
                watched_playlist=self.source_playlist, watching_playlist=self.target_playlist
            )
        except PlaylistWatchConfig.DoesNotExist:
            return False
        else:
            return config.auto_accept_updates

    async def accept(self, spotify_client: MottleSpotifyClient) -> None:
        target_playlist = await sync_to_async(lambda: self.target_playlist)()

        if self.tracks_added is not None:
            await spotify_client.add_tracks_to_playlist(
                target_playlist.spotify_id, [f"spotify:track:{t}" for t in self.tracks_added]
            )

        if self.albums_added is not None:
            tracks = await spotify_client.get_tracks_in_albums(self.albums_added)
            await spotify_client.add_tracks_to_playlist(
                target_playlist.spotify_id, [t.uri for t in tracks if t is not None]
            )

        self.is_accepted = True
        await self.asave()

    async def reject(self) -> None:
        self.is_accepted = False
        await self.asave()

    @classmethod
    async def find_or_create_for_playlist(
        cls, target_playlist: Playlist, source_playlist: Playlist, new_track_ids: list[str]
    ) -> tuple["PlaylistUpdate", bool]:
        update_hash = generate_playlist_update_hash(tracks_added=new_track_ids)
        logger.debug(f"PlaylistUpdate hash: {update_hash}")

        logger.info(
            f"Checking if PlaylistUpdate for target playlist {target_playlist} with hash {update_hash} already exists"
        )
        try:
            update = await PlaylistUpdate.objects.aget(target_playlist=target_playlist, update_hash=update_hash)
        except PlaylistUpdate.DoesNotExist:
            logger.info(f"PlaylistUpdate for target playlist {target_playlist} with hash {update_hash} does not exist")
            logger.info("Checking if PlaylistUpdate with the same target_playlist and source_playlist already exists")
            try:
                outdated_update = await PlaylistUpdate.objects.aget(
                    target_playlist=target_playlist, source_playlist=source_playlist, is_overridden_by=None
                )
            except PlaylistUpdate.DoesNotExist:
                logger.info("PlaylistUpdate with the same target_playlist and source_playlist does not exist")
                outdated_update = None
            else:
                logger.info(
                    f"PlaylistUpdate with the same target_playlist and source_playlist exists: {outdated_update}"
                )

            update = await PlaylistUpdate.objects.acreate(
                target_playlist=target_playlist,
                source_playlist=source_playlist,
                is_notified_of=False,
                is_accepted=None,
                tracks_added=list(new_track_ids),
            )
            logger.info(f"Created new PlaylistUpdate: {update}")

            if outdated_update is not None:
                logger.info(f"Setting is_overridden_by to {update} for outdated {outdated_update}")
                outdated_update.is_overridden_by = update
                await outdated_update.asave()
            return update, True
        else:
            logger.info(f"PlaylistUpdate with hash {update_hash} already exists: {update}")
            return update, False

    # TODO: Mostly duplicates find_or_create_for_playlist
    @classmethod
    async def find_or_create_for_artist(
        cls, target_playlist: Playlist, source_artist: Artist, new_album_ids: list[str]
    ) -> tuple["PlaylistUpdate", bool]:
        update_hash = generate_playlist_update_hash(albums_added=new_album_ids)
        logger.debug(f"PlaylistUpdate hash: {update_hash}")

        logger.info(
            f"Checking if PlaylistUpdate for target playlist {target_playlist} with hash {update_hash} already exists"
        )
        try:
            update = await PlaylistUpdate.objects.aget(target_playlist=target_playlist, update_hash=update_hash)
        except PlaylistUpdate.DoesNotExist:
            logger.info(f"PlaylistUpdate for target playlist {target_playlist} with hash {update_hash} does not exist")
            logger.info("Checking if PlaylistUpdate with the same target_playlist and source_artist already exists")
            try:
                outdated_update = await PlaylistUpdate.objects.aget(
                    target_playlist=target_playlist, source_artist=source_artist, is_overridden_by=None
                )
            except PlaylistUpdate.DoesNotExist:
                logger.info("PlaylistUpdate with the same target_playlist and source_artist does not exist")
                outdated_update = None
            else:
                logger.info(f"PlaylistUpdate with the same target_playlist and source_artist exists: {outdated_update}")

            update = await PlaylistUpdate.objects.acreate(
                target_playlist=target_playlist,
                source_artist=source_artist,
                is_notified_of=False,
                is_accepted=None,
                albums_added=list(new_album_ids),
            )
            logger.info(f"Created new PlaylistUpdate: {update}")

            if outdated_update is not None:
                logger.info(f"Setting is_overridden_by to {update} for outdated {outdated_update}")
                outdated_update.is_overridden_by = update
                await outdated_update.asave()
            return update, True
        else:
            logger.info(f"PlaylistUpdate with hash {update_hash} already exists: {update}")
            return update, False


def generate_playlist_update_hash(
    albums_added: list[str] | None = None,
    albums_removed: list[str] | None = None,
    tracks_added: list[str] | None = None,
    tracks_removed: list[str] | None = None,
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
