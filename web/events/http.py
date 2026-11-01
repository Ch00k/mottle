import logging
from typing import TYPE_CHECKING, Any, cast

import httpx2
import stamina
from curl_cffi.requests import AsyncSession
from curl_cffi.requests import exceptions as curl_exceptions
from django.conf import settings
from lxml import html as lh

from .constants import (
    BANDSINTOWN_API_REQUEST_TIMEOUT,
    BANDSINTOWN_BASE_URL,
    MUSICBRAINZ_API_BASE_URL,
    MUSICBRAINZ_API_REQUEST_TIMEOUT,
    SONGKICK_API_REQUEST_TIMEOUT,
    SONGKICK_BASE_URL,
)
from .exceptions import HTTPClientException

if TYPE_CHECKING:
    from curl_cffi.requests.impersonate import BrowserTypeLiteral

logger = logging.getLogger(__name__)

# Headers curl sets itself from the connection and the body it sends. It decodes the body it receives, so a response's
# own encoding and length no longer describe it.
CURL_REQUEST_HEADERS = {"host", "connection", "accept-encoding", "content-length"}
CURL_RESPONSE_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}
# Headers an impersonated browser sends with its own values, which a request's would give away
BROWSER_REQUEST_HEADERS = {"user-agent", "accept"}


class CurlCffiTransport(httpx2.AsyncBaseTransport):
    """Sends requests with curl_cffi, which can present a browser's TLS and HTTP/2 fingerprint.

    Every request opens a new connection, which through a rotating proxy gets a new exit IP, so a retry after a
    refusal comes from a different address.
    """

    def __init__(self, proxy: str | None = None, impersonate: BrowserTypeLiteral | None = None) -> None:
        self.proxy = proxy
        self.impersonate = impersonate

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        dropped_headers = CURL_REQUEST_HEADERS | (BROWSER_REQUEST_HEADERS if self.impersonate else set())
        headers = [(k, v) for k, v in request.headers.multi_items() if k.lower() not in dropped_headers]
        timeout = request.extensions.get("timeout", {})

        try:
            async with AsyncSession() as session:
                response = await session.request(
                    request.method,
                    str(request.url),
                    headers=headers,
                    content=await request.aread(),
                    timeout=(timeout.get("connect"), timeout.get("read")),
                    allow_redirects=False,
                    proxy=self.proxy,
                    impersonate=self.impersonate,
                    quote=False,
                )
        except curl_exceptions.RequestException as e:
            # curl's messages leave out the URL, which the retry logs need to name the failed request
            message = f"{e} ({request.method} {request.url})"

            if isinstance(e, curl_exceptions.ProxyError):
                raise httpx2.ProxyError(message, request=request) from e
            if isinstance(e, curl_exceptions.ConnectTimeout):
                raise httpx2.ConnectTimeout(message, request=request) from e
            if isinstance(e, curl_exceptions.Timeout):
                raise httpx2.ReadTimeout(message, request=request) from e
            if isinstance(e, curl_exceptions.ConnectionError):
                raise httpx2.ConnectError(message, request=request) from e
            raise httpx2.TransportError(message, request=request) from e

        return httpx2.Response(
            response.status_code,
            headers=[
                (k, v)
                for k, v in response.headers.multi_items()
                if v is not None and k.lower() not in CURL_RESPONSE_HEADERS
            ],
            content=response.content,
            request=request,
        )


class AsyncRetryingClient(httpx2.AsyncClient):
    """Retries a request that fails to reach the site or gets a response saying to try again later.

    Those are a 429, a 5xx and the client's `retry_statuses`. Any other response is returned as it is, as asking again
    would get the same answer. Each client sets how long it waits before a retry: the wait starts at `wait_initial` and
    doubles up to `wait_max`.
    """

    def __init__(
        self,
        *args: Any,
        attempts: int = 10,
        wait_initial: float = 1.0,
        wait_max: float = 10.0,
        retry_statuses: frozenset[int] = frozenset(),
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.attempts = attempts
        self.wait_initial = wait_initial
        self.wait_max = wait_max
        self.retry_statuses = retry_statuses | {httpx2.codes.TOO_MANY_REQUESTS}

    async def send(self, request: httpx2.Request, *args: Any, **kwargs: Any) -> httpx2.Response:
        async for attempt in stamina.retry_context(
            on=(httpx2.TransportError, httpx2.HTTPStatusError),
            attempts=self.attempts,
            timeout=None,
            wait_initial=self.wait_initial,
            wait_max=self.wait_max,
            # Up to as much again at random, which keeps a client that does not wait from waiting at all
            wait_jitter=self.wait_initial,
        ):
            with attempt:
                response = await super().send(request, *args, **kwargs)

                if response.is_server_error or response.status_code in self.retry_statuses:
                    response.raise_for_status()

                return response

        raise AssertionError("stamina raises the last error once the attempts run out")


async_musicbrainz_client = AsyncRetryingClient(
    timeout=httpx2.Timeout(MUSICBRAINZ_API_REQUEST_TIMEOUT),
    base_url=MUSICBRAINZ_API_BASE_URL,
    headers={"Accept": "application/json", "User-Agent": settings.HTTP_USER_AGENT},
    transport=CurlCffiTransport(proxy=settings.PROXY_URL),
)

async_songkick_client = AsyncRetryingClient(
    # Songkick refuses some requests with a 406 that the next attempt gets past
    retry_statuses=frozenset({httpx2.codes.NOT_ACCEPTABLE}),
    timeout=httpx2.Timeout(SONGKICK_API_REQUEST_TIMEOUT),
    base_url=SONGKICK_BASE_URL,
    transport=CurlCffiTransport(proxy=settings.PROXY_URL, impersonate="chrome"),
)

async_bandsintown_client = AsyncRetryingClient(
    # Cloudflare refuses about four attempts in five with a 403. Each attempt comes from a new proxy IP, so it can go
    # at once.
    attempts=20,
    wait_initial=0,
    wait_max=0,
    retry_statuses=frozenset({httpx2.codes.FORBIDDEN}),
    timeout=httpx2.Timeout(BANDSINTOWN_API_REQUEST_TIMEOUT),
    base_url=BANDSINTOWN_BASE_URL,
    transport=CurlCffiTransport(proxy=settings.PROXY_URL, impersonate="chrome"),
)


async def send_get_request(
    client: AsyncRetryingClient,
    url: str,
    params: dict[str, Any] | None = None,
    follow_redirects: bool = False,
    accept_redirect: bool = False,
) -> httpx2.Response:
    """Return the response to a GET request, raising HTTPClientException for an unsuccessful one.

    A redirect counts as unsuccessful, unless `accept_redirect` is set.
    """
    try:
        response = await client.get(url, params=params, follow_redirects=follow_redirects)

        if not (response.is_success or (accept_redirect and response.is_redirect)):
            response.raise_for_status()
    except Exception as e:
        raise HTTPClientException(f"Failed to send GET request to {url}: {e}") from e

    return response


async def get_json(client: AsyncRetryingClient, url: str, params: dict[str, Any] | None = None) -> Any:
    response = await send_get_request(client, url, params=params)

    try:
        return response.json()
    except Exception as e:
        raise HTTPClientException(f"Failed to parse JSON data from {url}: {e}") from e


async def get_xpath(
    client: AsyncRetryingClient, url: str, xpath: str, follow_redirects: bool = False
) -> tuple[list[Any], str]:
    """Return what the XPath expression selects on the page at the URL, and the URL the page was read from."""
    response = await send_get_request(client, url, follow_redirects=follow_redirects)

    try:
        html = lh.fromstring(response.text)
    except Exception as e:
        raise HTTPClientException(f"Failed to parse HTML data from {url}: {e}") from e

    try:
        # The expressions select nodes or strings, which come as a list
        return cast("list[Any]", html.xpath(xpath)), str(response.url)
    except Exception as e:
        raise HTTPClientException(f"Failed to extract data from {url}: {e}") from e


async def get_redirect_location(client: AsyncRetryingClient, url: str) -> str | None:
    """Return the URL the URL redirects to, or None if it does not redirect."""
    response = await send_get_request(client, url, accept_redirect=True)
    return response.headers["Location"] if response.has_redirect_location else None
