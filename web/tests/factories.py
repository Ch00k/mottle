from typing import Any

import factory
from django.conf import settings
from django.contrib.gis.geos import Point
from faker import Faker

from web.events.enums import EventDataSource, EventType
from web.models import (
    Artist,
    Event,
    EventArtist,
    EventUpdate,
    Playlist,
    PlaylistUpdate,
    PlaylistWatchConfig,
    SpotifyUser,
)

fake = Faker()


class SpotifyUserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SpotifyUser

    spotify_id = factory.Faker("pystr")
    display_name = factory.Faker("user_name")
    email = factory.Faker("email")


class ArtistFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Artist

    spotify_id = factory.Faker("pystr")


class EventArtistFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EventArtist
        # skip_postgeneration_save = True

    artist = factory.SubFactory(ArtistFactory)
    songkick_id = factory.Faker("pystr")
    songkick_url = factory.Faker("url")
    bandsintown_id = factory.Faker("pystr")
    bandsintown_url = factory.Faker("url")
    # watching_users = factory.RelatedFactoryList(SpotifyUserFactory)

    @factory.post_generation
    def watching_users(self, create: Any, extracted: Any, **kwargs: Any) -> None:
        if not create or not extracted:
            # Simple build, or nothing to add, do nothing.
            return

        # Add the iterable of groups using bulk addition
        self.watching_users.add(*extracted)


class EventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Event

    artist = factory.SubFactory(EventArtistFactory)
    source = factory.Faker("random_choices", elements=[e.name for e in EventDataSource])
    source_id = factory.Faker("pystr")
    type = factory.Faker("random_choices", elements=[e.name for e in EventType])
    date = factory.Faker("date")
    venue = factory.Faker("pystr")
    postcode = factory.Faker("postcode")
    address = factory.Faker("street_address")
    city = factory.Faker("city")
    country = factory.Faker("country")
    geolocation = Point(
        float(fake.longitude()),
        float(fake.latitude()),
        srid=settings.GEODJANGO_SRID,
    )
    stream_urls = factory.Faker("pylist", variable_nb_elements=False, value_types=["url"], allowed_types=["url"])
    tickets_urls = factory.Faker("pylist", variable_nb_elements=False, value_types=["url"], allowed_types=["url"])


class EventUpdateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EventUpdate

    event = factory.SubFactory(EventFactory)
    type = factory.Faker("random_choices", elements=[name for name, _ in EventUpdate.EVENT_UPDATE_TYPES])
    changes = factory.Faker("pydict", variable_nb_elements=False, value_types=[str], allowed_types=[str])
    is_notified_of = False


class PlaylistFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Playlist

    spotify_id = factory.Faker("pystr")
    spotify_user = factory.SubFactory(SpotifyUserFactory)


class PlaylistWatchConfigFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PlaylistWatchConfig


class PlaylistUpdateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PlaylistUpdate

    target_playlist = factory.SubFactory(PlaylistFactory)
    source_playlist = factory.SubFactory(PlaylistFactory)

    albums_added = factory.Faker("pylist", variable_nb_elements=False, value_types=[str], allowed_types=[str])
    albums_removed = factory.Faker("pylist", variable_nb_elements=False, value_types=[str], allowed_types=[str])
    tracks_added = factory.Faker("pylist", variable_nb_elements=False, value_types=[str], allowed_types=[str])
    albums_removed = factory.Faker("pylist", variable_nb_elements=False, value_types=[str], allowed_types=[str])

    is_notified = False
    is_accepted = False
    is_overridden_by = None
