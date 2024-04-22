from functools import partial

from asgiref.sync import async_to_sync
from django.test import TestCase

from web.models import Playlist, SpotifyUser
from web.tests.factories import PlaylistFactory


class PlaylistsTestCase(TestCase):
    def test_watch_playlist(self) -> None:
        watching_playlist = PlaylistFactory.create()
        watched_playlist = PlaylistFactory.create(spotify_user=None)

        async_to_sync(partial(watching_playlist.watch, watched_playlist))()

        self.assertListEqual(list(watching_playlist.watched_playlists.all()), [watched_playlist])
        self.assertListEqual(list(watched_playlist.watched_playlists.all()), [])

    def test_playlist_watched_by_multiple(self) -> None:
        watched_playlist = PlaylistFactory.create(spotify_user=None)
        watching_playlist_1 = PlaylistFactory.create()
        watching_playlist_2 = PlaylistFactory.create()

        async_to_sync(partial(watching_playlist_1.watch, watched_playlist))()
        async_to_sync(partial(watching_playlist_2.watch, watched_playlist))()

        self.assertCountEqual(list(watching_playlist_1.watched_playlists.all()), [watched_playlist])
        self.assertCountEqual(list(watching_playlist_2.watched_playlists.all()), [watched_playlist])
        self.assertCountEqual(list(watched_playlist.watched_playlists.all()), [])

    def test_playlists_watching_each_other(self) -> None:
        playlist_1 = PlaylistFactory.create()
        playlist_2 = PlaylistFactory.create()

        async_to_sync(partial(playlist_1.watch, playlist_2))()
        async_to_sync(partial(playlist_2.watch, playlist_1))()

        async_to_sync(partial(playlist_2.unwatch, playlist_1))()

        self.assertCountEqual(list(playlist_1.watched_playlists.all()), [playlist_2])
        self.assertCountEqual(list(playlist_2.watched_playlists.all()), [])

    # def test_unwatch_and_re_watch(self) -> None:
    #     watching_playlist = PlaylistFactory.create()
    #     watched_playlist = PlaylistFactory.create(spotify_user=None)

    #     async_to_sync(partial(watching_playlist.watch, watched_playlist))()
    #     async_to_sync(partial(watching_playlist.unwatch, watched_playlist))()
    #     async_to_sync(partial(watching_playlist.watch, watched_playlist))()

    def test_unwatch_playlist_watched_only_by_us(self) -> None:
        watching_playlist = PlaylistFactory.create()
        watched_playlist = PlaylistFactory.create(spotify_user=None)

        self.assertEqual(Playlist.objects.filter(spotify_id=watching_playlist.spotify_id).count(), 1)
        self.assertEqual(Playlist.objects.filter(spotify_id=watched_playlist.spotify_id).count(), 1)
        self.assertEqual(SpotifyUser.objects.filter(spotify_id=watching_playlist.spotify_user.spotify_id).count(), 1)

        async_to_sync(partial(watching_playlist.watch, watched_playlist))()

        self.assertEqual(Playlist.objects.filter(watched_playlists=watched_playlist).count(), 1)

        async_to_sync(partial(watching_playlist.unwatch, watched_playlist))()

        self.assertEqual(Playlist.objects.filter(spotify_id=watching_playlist.spotify_id).count(), 0)
        self.assertEqual(Playlist.objects.filter(spotify_id=watched_playlist.spotify_id).count(), 0)
        self.assertEqual(SpotifyUser.objects.filter(spotify_id=watching_playlist.spotify_user.spotify_id).count(), 0)

        # This would not work (probably) because the watched_playlist does not exist anymore
        # self.assertEqual(Playlist.objects.filter(watched_playlists=watched_playlist).count(), 0)

    def test_unwatch_playlist_watched_by_us_and_another_playlist(self) -> None:
        watching_playlist_1 = PlaylistFactory.create()
        watching_playlist_2 = PlaylistFactory.create()
        watched_playlist = PlaylistFactory.create(spotify_user=None)

        self.assertEqual(Playlist.objects.filter(spotify_id=watching_playlist_1.spotify_id).count(), 1)
        self.assertEqual(Playlist.objects.filter(spotify_id=watching_playlist_2.spotify_id).count(), 1)
        self.assertEqual(Playlist.objects.filter(spotify_id=watched_playlist.spotify_id).count(), 1)
        self.assertEqual(SpotifyUser.objects.filter(spotify_id=watching_playlist_1.spotify_user.spotify_id).count(), 1)
        self.assertEqual(SpotifyUser.objects.filter(spotify_id=watching_playlist_2.spotify_user.spotify_id).count(), 1)

        async_to_sync(partial(watching_playlist_1.watch, watched_playlist))()
        async_to_sync(partial(watching_playlist_2.watch, watched_playlist))()

        self.assertEqual(Playlist.objects.filter(watched_playlists=watched_playlist).count(), 2)

        async_to_sync(partial(watching_playlist_1.unwatch, watched_playlist))()

        self.assertEqual(Playlist.objects.filter(spotify_id=watching_playlist_1.spotify_id).count(), 0)
        self.assertEqual(Playlist.objects.filter(spotify_id=watching_playlist_2.spotify_id).count(), 1)
        self.assertEqual(Playlist.objects.filter(spotify_id=watched_playlist.spotify_id).count(), 1)
        self.assertEqual(SpotifyUser.objects.filter(spotify_id=watching_playlist_1.spotify_user.spotify_id).count(), 0)
        self.assertEqual(SpotifyUser.objects.filter(spotify_id=watching_playlist_2.spotify_user.spotify_id).count(), 1)
        self.assertEqual(Playlist.objects.filter(watched_playlists=watched_playlist).count(), 1)

        self.assertCountEqual(list(watching_playlist_2.watched_playlists.all()), [watched_playlist])

    def test_unwatch_one_of_multiple_watched_playlists(self) -> None:
        watching_playlist = PlaylistFactory.create()
        watched_playlist_1 = PlaylistFactory.create(spotify_user=None)
        watched_playlist_2 = PlaylistFactory.create(spotify_user=None)

        async_to_sync(partial(watching_playlist.watch, watched_playlist_1))()
        async_to_sync(partial(watching_playlist.watch, watched_playlist_2))()

        self.assertEqual(watching_playlist.watched_playlists.count(), 2)

        async_to_sync(partial(watching_playlist.unwatch, watched_playlist_1))()

        self.assertEqual(watching_playlist.watched_playlists.count(), 1)
        self.assertEqual(Playlist.objects.filter(spotify_id=watched_playlist_1.spotify_id).count(), 0)
        self.assertEqual(Playlist.objects.filter(spotify_id=watched_playlist_2.spotify_id).count(), 1)
