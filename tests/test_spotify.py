from tekore import AsyncSender, CachingSender

from web.spotify import MottleRetryingSender, get_client


def test_get_client() -> None:
    """The client sends asynchronously with the timeout given, retrying twice, and chunks and maximises its requests."""
    client = get_client("test_access_token", http_timeout=7)

    assert isinstance(client.sender, CachingSender)
    retrying_sender = client.sender.sender
    assert isinstance(retrying_sender, MottleRetryingSender)
    assert retrying_sender.retries == 2
    async_sender = retrying_sender.sender
    assert isinstance(async_sender, AsyncSender)
    assert async_sender.client.timeout.read == 7
    assert client.chunked_on is True
    assert client.max_limits_on is True
