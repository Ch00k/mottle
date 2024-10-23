import asyncio
import logging
import time
import timeit
from typing import Any, Optional

import httpx
from django.conf import settings
from lxml import html as lh

from .constants import BANDSINTOWN_BASE_URL, MUSICBRAINZ_API_BASE_URL, SONGKICK_BASE_URL
from .exceptions import HTTPClientException, RetriesExhaustedException

logger = logging.getLogger(__name__)


class AsyncRetryingClient(httpx.AsyncClient):
    def __init__(
        self,
        name: str,
        throttle_response_code: Optional[int] = None,
        retries: int = 10,
        delay_seconds: float = 0.0,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.name = name
        self.throttle_response_code = throttle_response_code
        self.retries = retries
        self.delay_seconds = delay_seconds
        self.requests_total = 0
        self.requests_timedout = 0
        self.requests_errored = 0
        self.requests_accepted = 0
        self.requests_throttled = 0
        self.next_request_allowed_at = time.time()

    async def send(self, request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        retries = self.retries

        while retries > 0:
            if self.delay_seconds:
                time_to_sleep_seconds = max(self.next_request_allowed_at - time.time(), 0.0)

                if time_to_sleep_seconds:
                    logger.debug(f"Sleeping for {time_to_sleep_seconds} seconds")
                    await asyncio.sleep(time_to_sleep_seconds)

            self.requests_total += 1

            try:
                start = timeit.default_timer()
                response = await super().send(request, *args, **kwargs)
                request_time = timeit.default_timer() - start
            except httpx.TimeoutException as e:
                self.requests_timedout += 1
                logger.error(f"Timeout {self.timeout} reached while requesting {request.url}: {e}. Retrying...")
                retries -= 1
                continue
            except Exception as e:
                self.requests_errored += 1
                logger.error(f"Unexpected error while requesting {request.url}: {e}. Retrying...")
                retries -= 1
                continue
            else:
                if self.delay_seconds:
                    # Assuming the request time is the time for request to reach the server + the time for response
                    # to reach us, and that these times are equal. Hence dividing by 2.
                    delay_before_next_request = max(self.delay_seconds - request_time / 2, 0.0)

                    now = time.time()
                    self.next_request_allowed_at = now + delay_before_next_request
                    next_request_allowed_in_seconds = self.next_request_allowed_at - now

            if self.throttle_response_code is not None and response.status_code == self.throttle_response_code:
                self.requests_throttled += 1
            else:
                self.requests_accepted += 1

            log_msg = (
                f"{self.__class__.__name__}({self.name}): "
                f"requests total: {self.requests_total}, "
                f"requests timed out: {self.requests_timedout}, "
                f"requests errored: {self.requests_errored}, "
                f"requests succeeded: {self.requests_accepted}, "
                f"requests throttled: {self.requests_throttled}, "
                f"request time: {request_time}"
            )
            if self.delay_seconds:
                log_msg += f", next request allowed in {next_request_allowed_in_seconds} seconds"

            logger.debug(log_msg)

            if response.status_code < 400:  # TODO: 404?
                return response

            retries -= 1
        else:
            raise RetriesExhaustedException(
                f"Failed to get a successful response from {request.url} after {self.retries} retries"
            )


async_musicbrainz_client = AsyncRetryingClient(
    name="musicbrainz",
    throttle_response_code=429,
    delay_seconds=1.0,
    timeout=httpx.Timeout(10),
    base_url=MUSICBRAINZ_API_BASE_URL,
    headers={"Accept": "application/json", "User-Agent": settings.HTTP_USER_AGENT},
)

async_songkick_client = AsyncRetryingClient(
    name="songkick",
    timeout=httpx.Timeout(10),
    base_url=SONGKICK_BASE_URL,
    headers={"User-Agent": settings.HTTP_USER_AGENT},
)

async_bandsintown_client = AsyncRetryingClient(
    name="bandsintown",
    throttle_response_code=403,
    timeout=httpx.Timeout(10),
    base_url=BANDSINTOWN_BASE_URL,
    headers={"User-Agent": settings.HTTP_USER_AGENT},
    proxy=settings.BRIGHTDATA_PROXY_URL,
    limits=httpx.Limits(max_connections=1000, max_keepalive_connections=0),
)


def send_get_request(
    client: httpx.Client,
    url: str,
    parse_json: bool = False,
    xpath: Optional[str] = None,
    redirect_url: bool = False,
    raise_for_lte_300: bool = True,
    follow_redirects: bool = False,
) -> Any:  # TODO
    ret_json = None
    ret_xpath = None
    ret_redirect_url = None
    ret_url = None

    try:
        response = client.get(url, follow_redirects=follow_redirects)

        if raise_for_lte_300:
            response.raise_for_status()
        else:
            if response.is_error:
                response.raise_for_status()
    except Exception as e:
        raise HTTPClientException(f"Failed to send GET request to {url}: {e}")

    if parse_json:
        try:
            ret_json = response.json()
        except Exception as e:
            raise HTTPClientException(f"Failed to parse JSON data from {url}: {e}")

    if xpath:
        try:
            html = lh.fromstring(response.text)
        except Exception as e:
            raise HTTPClientException(f"Failed to parse HTML data from {url}: {e}")

        try:
            ret_xpath = html.xpath(xpath)
        except Exception as e:
            raise HTTPClientException(f"Failed to extract data from {url}: {e}")

    if redirect_url:
        if response.has_redirect_location:
            ret_redirect_url = response.headers["Location"]
        else:
            ret_redirect_url = None

    ret_url = response.url

    return ret_json, ret_xpath, ret_redirect_url, ret_url


async def asend_get_request(
    client: httpx.AsyncClient,
    url: str,
    parse_json: bool = False,
    xpath: Optional[str] = None,
    redirect_url: bool = False,
    raise_for_lte_300: bool = True,
    follow_redirects: bool = False,
) -> Any:  # TODO
    ret_json = None
    ret_xpath = None
    ret_redirect_url = None
    ret_url = None

    try:
        response = await client.get(url, follow_redirects=follow_redirects)

        if raise_for_lte_300:
            response.raise_for_status()
        else:
            if response.is_error:
                response.raise_for_status()
    except Exception as e:
        raise HTTPClientException(f"Failed to send GET request to {url}: {e}")

    if parse_json:
        try:
            ret_json = response.json()
        except Exception as e:
            raise HTTPClientException(f"Failed to parse JSON data from {url}: {e}")

    if xpath:
        try:
            html = lh.fromstring(response.text)
        except Exception as e:
            raise HTTPClientException(f"Failed to parse HTML data from {url}: {e}")

        try:
            ret_xpath = html.xpath(xpath)
        except Exception as e:
            raise HTTPClientException(f"Failed to extract data from {url}: {e}")

    if redirect_url and response.has_redirect_location:
        ret_redirect_url = response.headers["Location"]

    ret_url = response.url

    return ret_json, ret_xpath, ret_redirect_url, ret_url
