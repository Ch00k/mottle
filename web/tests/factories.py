import factory

from web.models import Playlist, PlaylistUpdate, PlaylistWatchConfig, SpotifyUser


class SpotifyUserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SpotifyUser

    spotify_id = factory.Faker("pystr")
    display_name = factory.Faker("user_name")
    email = factory.Faker("email")


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
