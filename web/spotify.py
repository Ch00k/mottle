from django.conf import settings
from httpx import AsyncClient, Timeout
from tekore import AsyncSender, Credentials, RetryingSender, Spotify, Token, UserAuth


def get_auth(credentials: Credentials, scope: list[str], state: str | None = None) -> UserAuth:
    auth = UserAuth(cred=credentials, scope=scope)

    if state is not None:
        auth.state = state

    return auth


def get_client(
    access_token: str,
    http_timeout: int,
    retries: int = 2,
    max_limits_on: bool = True,
    chunked_on: bool = True,
) -> Spotify:
    sender = RetryingSender(retries=retries, sender=AsyncSender(AsyncClient(timeout=Timeout(http_timeout))))
    return Spotify(token=access_token, sender=sender, max_limits_on=max_limits_on, chunked_on=chunked_on)


def authenticate(code: str, state: str) -> Token:
    auth = get_auth(credentials=settings.SPOTIFY_CREDEINTIALS, scope=settings.SPOTIFY_TOKEN_SCOPE, state=state)
    token = auth.request_token(code, state)

    # Plain Credentials, as opposed to RefreshingCredentials, issue a plain Token
    assert isinstance(token, Token)
    return token


def get_client_token() -> Token:
    return settings.SPOTIFY_CREDEINTIALS.request_client_token()
