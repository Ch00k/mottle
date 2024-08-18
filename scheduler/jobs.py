from web.jobs import check_playlists_for_updates
from web.spotify import get_client_token
from web.utils import MottleSpotifyClient


async def get_playlist_updates() -> None:
    token = get_client_token()
    spotify_client = MottleSpotifyClient(token.access_token)
    await check_playlists_for_updates(spotify_client)
