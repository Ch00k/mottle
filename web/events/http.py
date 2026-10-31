import asyncio
import logging
import random
import string
import time
import timeit
from typing import TYPE_CHECKING, Any

import httpx2
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
from .exceptions import HTTPClientException, RetriesExhaustedException

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
    def __init__(
        self,
        name: str,
        throttle_response_code: int | None = None,
        retries: int = 10,
        delay_seconds: float = 0.0,
        log_request_details: bool = False,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.name = name
        self.throttle_response_code = throttle_response_code
        self.retries = retries
        self.delay_seconds = delay_seconds
        self.log_request_details = log_request_details
        self.requests_total = 0
        self.requests_timedout = 0
        self.requests_failed_to_connect = 0
        self.requests_failed_to_proxy = 0
        self.requests_errored = 0
        self.requests_accepted = 0
        self.requests_gte_400 = 0
        self.requests_throttled = 0
        self.requests_retries_exhausted = 0
        self.next_request_allowed_at = time.time()

    def _merge_headers(self, headers: httpx2._types.HeaderTypes | None = None) -> httpx2._types.HeaderTypes | None:
        merged_headers = httpx2.Headers(super()._merge_headers(headers))
        random_str = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))  # noqa: S311
        merged_headers["User-Agent"] = merged_headers["User-Agent"] + "-" + random_str
        return merged_headers

    async def send(self, request: httpx2.Request, *args: Any, **kwargs: Any) -> httpx2.Response:
        retries = self.retries

        while retries > 0:
            if self.delay_seconds:
                time_to_sleep_seconds = max(self.next_request_allowed_at - time.time(), 0.0)

                if time_to_sleep_seconds:
                    logger.debug(f"Sleeping for {time_to_sleep_seconds} seconds")
                    await asyncio.sleep(time_to_sleep_seconds)

            self.requests_total += 1

            start = timeit.default_timer()

            try:
                response = await super().send(request, *args, **kwargs)
            except httpx2.ConnectError as e:
                self.requests_failed_to_connect += 1
                logger.warning(f"Failed to connect while requesting {request.url}: {e}. Retrying...")
                retries -= 1
                continue
            except httpx2.ProxyError as e:
                self.requests_failed_to_proxy += 1
                logger.warning(f"Failed to proxy while requesting {request.url}: {e}. Retrying...")
                retries -= 1
                continue
            except httpx2.TimeoutException as e:
                self.requests_timedout += 1
                logger.warning(f"Timeout {self.timeout} reached while requesting {request.url}: {e}. Retrying...")
                retries -= 1
                continue
            except Exception as e:
                self.requests_errored += 1

                exc_msg = e.__class__.__name__
                if str(e):
                    exc_msg += f"({e})"

                logger.warning(f"Unexpected error while requesting {request.url}: {exc_msg}. Retrying...")
                retries -= 1
                continue

            # else:
            request_time = max(timeit.default_timer() - start, 0)

            if self.delay_seconds:
                # Assuming the request time is the time for request to reach the server + the time for response
                # to reach us, and that these times are equal. Hence dividing by 2.
                delay_before_next_request = max(self.delay_seconds - request_time / 2, 0.0)

                now = time.time()
                self.next_request_allowed_at = now + delay_before_next_request
                next_request_allowed_in_seconds = self.next_request_allowed_at - now
            else:
                next_request_allowed_in_seconds = None

            if self.throttle_response_code is not None and response.status_code == self.throttle_response_code:
                self.requests_throttled += 1
            else:
                self.requests_accepted += 1

            if response.status_code >= 400:
                self.requests_gte_400 += 1

            if self.log_request_details:
                self.log(request_time=request_time, next_request_allowed_in_seconds=next_request_allowed_in_seconds)

            if response.status_code < 400 or response.status_code == 404:  # Can't retry a 404
                return response

            logger.warning(f"Received {response.status_code} response while requesting {request.url}. Retrying...")
            retries -= 1

        self.requests_retries_exhausted += 1

        logger.error(f"Retries exhausted while requesting {request.url}")
        self.log()

        raise RetriesExhaustedException(
            f"Failed to get a successful response from {request.url} after {self.retries} retries"
        )

    def log(self, request_time: float | None = None, next_request_allowed_in_seconds: float | None = None) -> None:
        log_msg = (
            f"{self.__class__.__name__}({self.name}): "
            f"REQUESTS: total {self.requests_total}, "
            f"accepted {self.requests_accepted}, "
            f"failed to connect {self.requests_failed_to_connect}, "
            f"failed to proxy {self.requests_failed_to_proxy}, "
            f"timed out {self.requests_timedout}, "
            f"errored {self.requests_errored}, "
            f"throttled {self.requests_throttled}, "
            f">=400 {self.requests_gte_400}, "
            f"retries exhausted {self.requests_retries_exhausted}"
        )

        if request_time is not None:
            log_msg += f", request time {request_time}"

        if self.delay_seconds and next_request_allowed_in_seconds is not None:
            log_msg += f", next request allowed in {next_request_allowed_in_seconds} seconds"

        logger.debug(log_msg)


async_musicbrainz_client = AsyncRetryingClient(
    name="musicbrainz",
    throttle_response_code=503,
    # log_request_details=True,
    timeout=httpx2.Timeout(MUSICBRAINZ_API_REQUEST_TIMEOUT),
    base_url=MUSICBRAINZ_API_BASE_URL,
    headers={"Accept": "application/json", "User-Agent": settings.HTTP_USER_AGENT},
    transport=CurlCffiTransport(proxy=settings.PROXY_URL),
)

async_songkick_client = AsyncRetryingClient(
    name="songkick",
    throttle_response_code=429,
    timeout=httpx2.Timeout(SONGKICK_API_REQUEST_TIMEOUT),
    # log_request_details=True,
    base_url=SONGKICK_BASE_URL,
    transport=CurlCffiTransport(proxy=settings.PROXY_URL, impersonate="chrome"),
)

async_bandsintown_client = AsyncRetryingClient(
    name="bandsintown",
    throttle_response_code=403,
    # About one attempt in five gets past Cloudflare
    retries=20,
    timeout=httpx2.Timeout(BANDSINTOWN_API_REQUEST_TIMEOUT),
    # log_request_details=True,
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
    except RetriesExhaustedException as e:
        raise HTTPClientException(
            f"Retries ({client.retries}) exhausted while sending GET request to {url}: {e}"
        ) from e
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
