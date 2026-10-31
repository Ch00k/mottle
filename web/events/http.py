import logging
from typing import TYPE_CHECKING, Any

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
        except curl_exceptions.ProxyError as e:
            raise httpx2.ProxyError(str(e), request=request) from e
        except curl_exceptions.ConnectTimeout as e:
            raise httpx2.ConnectTimeout(str(e), request=request) from e
        except curl_exceptions.Timeout as e:
            raise httpx2.ReadTimeout(str(e), request=request) from e
        except curl_exceptions.ConnectionError as e:
            raise httpx2.ConnectError(str(e), request=request) from e
        except curl_exceptions.RequestException as e:
            raise httpx2.TransportError(str(e), request=request) from e

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
    """Retries a request that fails to reach the site or gets an error response, waiting longer before each retry.

    A 404 is returned as it is, since asking again will not change it.
    """

    def __init__(
        self, *args: Any, attempts: int = 10, wait_initial: float = 1.0, wait_max: float = 10.0, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)

        self.attempts = attempts
        self.wait_initial = wait_initial
        self.wait_max = wait_max

    async def send(self, request: httpx2.Request, *args: Any, **kwargs: Any) -> httpx2.Response:
        async for attempt in stamina.retry_context(
            on=(httpx2.TransportError, httpx2.HTTPStatusError),
            attempts=self.attempts,
            timeout=None,
            wait_initial=self.wait_initial,
            wait_max=self.wait_max,
            wait_jitter=self.wait_initial,
        ):
            with attempt:
                response = await super().send(request, *args, **kwargs)

                if response.is_error and response.status_code != httpx2.codes.NOT_FOUND:
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
    timeout=httpx2.Timeout(SONGKICK_API_REQUEST_TIMEOUT),
    base_url=SONGKICK_BASE_URL,
    transport=CurlCffiTransport(proxy=settings.PROXY_URL, impersonate="chrome"),
)

async_bandsintown_client = AsyncRetryingClient(
    # About one attempt in five gets past Cloudflare. Each attempt comes from a new proxy IP, so it can go at once.
    attempts=20,
    wait_initial=0,
    wait_max=0,
    timeout=httpx2.Timeout(BANDSINTOWN_API_REQUEST_TIMEOUT),
    base_url=BANDSINTOWN_BASE_URL,
    transport=CurlCffiTransport(proxy=settings.PROXY_URL, impersonate="chrome"),
)


async def asend_get_request(
    client: AsyncRetryingClient,
    url: str,
    params: dict[str, Any] | None = None,
    parse_json: bool = False,
    xpath: str | None = None,
    redirect_url: bool = False,
    raise_for_lte_300: bool = True,
    follow_redirects: bool = False,
) -> Any:  # TODO
    ret_json = None
    ret_xpath = None
    ret_redirect_url = None
    ret_url = None

    try:
        response = await client.get(url, params=params, follow_redirects=follow_redirects)

        if raise_for_lte_300:
            response.raise_for_status()
        else:  # noqa: PLR5501
            if response.is_error:
                response.raise_for_status()
    except Exception as e:
        raise HTTPClientException(f"Failed to send GET request to {url}: {e}") from e

    if parse_json:
        try:
            ret_json = response.json()
        except Exception as e:
            raise HTTPClientException(f"Failed to parse JSON data from {url}: {e}") from e

    if xpath:
        try:
            html = lh.fromstring(response.text)
        except Exception as e:
            raise HTTPClientException(f"Failed to parse HTML data from {url}: {e}") from e

        try:
            ret_xpath = html.xpath(xpath)
        except Exception as e:
            raise HTTPClientException(f"Failed to extract data from {url}: {e}") from e

    if redirect_url and response.has_redirect_location:
        ret_redirect_url = response.headers["Location"]

    ret_url = response.url

    return ret_json, ret_xpath, ret_redirect_url, ret_url
