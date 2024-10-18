# ruff: noqa

import pytest
from django.db import connection

# from web.jobs import _get_users_with_event_updates

from . import factories


@pytest.mark.django_db
def test_get_users_with_event_updates() -> None:
    artist = factories.ArtistFactory()
    __import__("pdb").set_trace()

    user_1 = factories.SpotifyUserFactory()
    user_2 = factories.SpotifyUserFactory()
    user_3 = factories.SpotifyUserFactory()
    user_4 = factories.SpotifyUserFactory()

    event_artist_1 = factories.EventArtistFactory(watching_users=[user_1, user_2])
    event_artist_2 = factories.EventArtistFactory(watching_users=[user_3])
    event_artist_3 = factories.EventArtistFactory(watching_users=None)
    event_artist_4 = factories.EventArtistFactory(watching_users=[user_1])
    __import__("pdb").set_trace()

    event_1 = factories.EventFactory(artist=event_artist_1)
    event_2 = factories.EventFactory(artist=event_artist_1)
    event_3 = factories.EventFactory(artist=event_artist_1)

    event_4 = factories.EventFactory(artist=event_artist_2)
    event_5 = factories.EventFactory(artist=event_artist_2)

    event_6 = factories.EventFactory(artist=event_artist_3)
    event_7 = factories.EventFactory(artist=event_artist_3)

    event_update_1 = factories.EventUpdateFactory(event=event_1)
    event_update_2 = factories.EventUpdateFactory(event=event_2)
    event_update_3 = factories.EventUpdateFactory(event=event_3)

    # users = _get_users_with_event_updates()
    # for user in users:
    #     for artist in user.watched_event_artists.all():  # pyright: ignore
    #         for event in artist.events.all():
    #             for update in event.updates.all():
    #                 print(update)

    # selects = [i for i in connection.queries if i["sql"].startswith("SELECT")]
    # assert len(selects) == 4
