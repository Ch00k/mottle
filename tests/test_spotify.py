from tekore import AsyncSender, RetryingSender

from web.spotify import get_client


def test_get_client() -> None:
    """The client sends asynchronously with the timeout given, retrying twice, and chunks and maximises its requests."""
    client = get_client("test_access_token", http_timeout=7)

    assert isinstance(client.sender, RetryingSender)
    assert client.sender.retries == 2
    async_sender = client.sender.sender
    assert isinstance(async_sender, AsyncSender)
    assert async_sender.client.timeout.read == 7
    assert client.chunked_on is True
    assert client.max_limits_on is True
