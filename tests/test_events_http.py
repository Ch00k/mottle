import gzip
import json
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, cast

import httpx2
import pytest

from web.events.http import AsyncRetryingClient, CurlCffiTransport

if TYPE_CHECKING:
    from collections.abc import Iterator


class SiteHandler(BaseHTTPRequestHandler):
    """Answers the requests the tests send, and records the client port each one arrived from."""

    protocol_version = "HTTP/1.1"

    @property
    def site(self) -> Site:
        return cast("Site", self.server)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:
        self.site.client_ports.append(self.client_address[1])

        if self.path == "/echo":
            headers = {name.lower(): value for name, value in self.headers.items()}
            self.send_body(json.dumps(headers).encode(), content_type="application/json")
        elif self.path == "/gzip":
            self.send_body(gzip.compress(b"Deep Purple"), headers={"Content-Encoding": "gzip"})
        elif self.path == "/moved":
            self.send_body(b"", status=301, headers={"Location": "/echo"})
        elif self.path == "/slow":
            time.sleep(2)
            self.send_body(b"Too late")
        elif self.path == "/refused-twice":
            refused = len(self.site.client_ports) <= 2
            self.send_body(b"Just a moment..." if refused else b"Deep Purple", status=403 if refused else 200)
        else:
            self.send_body(b"", status=404)

    def send_body(
        self, body: bytes, status: int = 200, content_type: str = "text/plain", headers: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


class Site(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), SiteHandler)
        self.client_ports: list[int] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"


@pytest.fixture
def site() -> Iterator[Site]:
    server = Site()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.mark.asyncio
async def test_transport_opens_a_new_connection_per_request(site: Site) -> None:
    """Each request arrives on a connection of its own, though the server would keep the first one open.

    Through a rotating proxy every connection gets a new exit IP, which is what lets a retry come from another address.
    """
    async with httpx2.AsyncClient(base_url=site.url, transport=CurlCffiTransport()) as client:
        for _ in range(3):
            response = await client.get("/echo")
            assert response.status_code == 200

    assert len(set(site.client_ports)) == 3


@pytest.mark.asyncio
async def test_transport_returns_decoded_body(site: Site) -> None:
    """A compressed body is decoded once, and the response no longer claims an encoding its body does not have."""
    async with httpx2.AsyncClient(base_url=site.url, transport=CurlCffiTransport()) as client:
        response = await client.get("/gzip")

    assert response.text == "Deep Purple"
    assert "content-encoding" not in response.headers


@pytest.mark.asyncio
async def test_transport_leaves_redirects_to_the_client(site: Site) -> None:
    """The transport returns a redirect as it is, so the client's own setting decides whether it is followed."""
    async with httpx2.AsyncClient(base_url=site.url, transport=CurlCffiTransport()) as client:
        redirect = await client.get("/moved")
        followed = await client.get("/moved", follow_redirects=True)

    assert redirect.status_code == 301
    assert redirect.headers["Location"] == "/echo"
    assert followed.status_code == 200
    assert followed.url == f"{site.url}/echo"


@pytest.mark.asyncio
async def test_transport_sends_request_headers(site: Site) -> None:
    """Without impersonation the request's own User-Agent and Accept headers are sent."""
    async with httpx2.AsyncClient(
        base_url=site.url,
        headers={"User-Agent": "mottle/1.0", "Accept": "application/json"},
        transport=CurlCffiTransport(),
    ) as client:
        headers = (await client.get("/echo")).json()

    assert headers["user-agent"] == "mottle/1.0"
    assert headers["accept"] == "application/json"


@pytest.mark.asyncio
async def test_impersonating_transport_sends_browser_headers(site: Site) -> None:
    """An impersonated browser sends its own User-Agent and Accept headers in place of the request's."""
    async with httpx2.AsyncClient(
        base_url=site.url,
        headers={"User-Agent": "mottle/1.0", "Accept": "application/json"},
        transport=CurlCffiTransport(impersonate="chrome"),
    ) as client:
        headers = (await client.get("/echo")).json()

    assert "Chrome/" in headers["user-agent"]
    assert headers["accept"].startswith("text/html")


@pytest.mark.asyncio
async def test_transport_raises_connect_error_for_unreachable_server() -> None:
    """A server that refuses the connection raises the client library's own ConnectError."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]

    async with httpx2.AsyncClient(transport=CurlCffiTransport()) as client:
        with pytest.raises(httpx2.ConnectError, match=r"Failed to connect to 127\.0\.0\.1"):
            await client.get(f"http://127.0.0.1:{closed_port}/echo")


@pytest.mark.asyncio
async def test_transport_raises_read_timeout_for_slow_server(site: Site) -> None:
    """A response that does not arrive in time raises the client library's own ReadTimeout."""
    async with httpx2.AsyncClient(base_url=site.url, timeout=0.5, transport=CurlCffiTransport()) as client:
        with pytest.raises(httpx2.ReadTimeout, match=r"Operation timed out"):
            await client.get("/slow")


@pytest.mark.asyncio
async def test_retrying_client_retries_refusals_on_new_connections(
    site: Site, caplog: pytest.LogCaptureFixture
) -> None:
    """Refused requests are retried, each on a new connection, until one is let through.

    Each retry is logged as a warning naming the refusal and the refused URL.
    """
    client = AsyncRetryingClient(wait_initial=0, wait_max=0, base_url=site.url, transport=CurlCffiTransport())

    with caplog.at_level(logging.WARNING, logger="stamina"):
        response = await client.get("/refused-twice")

    assert response.text == "Deep Purple"
    assert len(set(site.client_ports)) == 3
    assert [r.getMessage() for r in caplog.records] == ["stamina.retry_scheduled"] * 2
    for record in caplog.records:
        caused_by = getattr(record, "stamina.caused_by")
        assert "403 Forbidden" in caused_by
        assert f"{site.url}/refused-twice" in caused_by


@pytest.mark.asyncio
async def test_retrying_client_raises_last_error_when_attempts_run_out(
    site: Site, caplog: pytest.LogCaptureFixture
) -> None:
    """A request refused on every attempt raises the last refusal once the attempts run out."""
    client = AsyncRetryingClient(
        attempts=2, wait_initial=0, wait_max=0, base_url=site.url, transport=CurlCffiTransport()
    )

    with caplog.at_level(logging.WARNING, logger="stamina"), pytest.raises(httpx2.HTTPStatusError, match="403"):
        await client.get("/refused-twice")

    assert len(site.client_ports) == 2
    assert [r.getMessage() for r in caplog.records] == ["stamina.retry_scheduled"]


@pytest.mark.asyncio
async def test_retrying_client_returns_not_found_without_retrying(site: Site) -> None:
    """A 404 is returned at once, since asking again will not change it."""
    client = AsyncRetryingClient(wait_initial=0, wait_max=0, base_url=site.url, transport=CurlCffiTransport())

    response = await client.get("/missing")

    assert response.status_code == 404
    assert len(site.client_ports) == 1
