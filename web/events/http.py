import asyncio
import logging
import random
import string
import time
import timeit
from typing import Any

import httpx
from django.conf import settings
from lxml import html as lh
from prometheus_client import Counter, Histogram

from web.metrics import (
    BANDSINTOWN_API_EXCEPTIONS,
    BANDSINTOWN_API_RESPONSE_TIME_SECONDS,
    BANDSINTOWN_API_RESPONSES_GTE_400,
    BANDSINTOWN_API_RESPONSES_THROTTLED,
    MUSICBRAINZ_API_EXCEPTIONS,
    MUSICBRAINZ_API_REQUEST_DELAY_TIME_SECONDS,
    MUSICBRAINZ_API_RESPONSE_TIME_SECONDS,
    MUSICBRAINZ_API_RESPONSES_GTE_400,
    MUSICBRAINZ_API_RESPONSES_THROTTLED,
    SONGKICK_API_EXCEPTIONS,
    SONGKICK_API_RESPONSE_TIME_SECONDS,
    SONGKICK_API_RESPONSES_GTE_400,
)

from .constants import (
    BANDSINTOWN_API_REQUEST_TIMEOUT,
    BANDSINTOWN_BASE_URL,
    MUSICBRAINZ_API_BASE_URL,
    MUSICBRAINZ_API_REQUEST_TIMEOUT,
    SONGKICK_API_REQUEST_TIMEOUT,
    SONGKICK_BASE_URL,
)
from .exceptions import HTTPClientException, RetriesExhaustedException

logger = logging.getLogger(__name__)


class AsyncRetryingClient(httpx.AsyncClient):
    def __init__(
        self,
        name: str,
        throttle_response_code: int | None = None,
        retries: int = 10,
        delay_seconds: float = 0.0,
        response_time_metric: Histogram | None = None,
        responses_gte_400_counter_metric: Counter | None = None,
        exceptions_counter_metric: Counter | None = None,
        throttle_counter_metric: Counter | None = None,
        delay_time_metric: Histogram | None = None,
        log_request_details: bool = False,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.name = name
        self.throttle_response_code = throttle_response_code
        self.retries = retries
        self.delay_seconds = delay_seconds
        self.response_time_metric = response_time_metric
        self.responses_gte_400_counter_metric = responses_gte_400_counter_metric
        self.exceptions_counter_metric = exceptions_counter_metric
        self.throttle_counter_metric = throttle_counter_metric
        self.delay_time_metric = delay_time_metric
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

    def _merge_headers(self, headers: httpx._types.HeaderTypes | None = None) -> httpx._types.HeaderTypes | None:
        headers = super()._merge_headers(headers)
        random_str = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))  # noqa: S311
        headers["User-Agent"] = headers["User-Agent"] + "-" + random_str  # type: ignore[index,operator,call-overload]
        return headers

    async def send(self, request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        retries = self.retries

        while retries > 0:
            if self.delay_seconds:
                time_to_sleep_seconds = max(self.next_request_allowed_at - time.time(), 0.0)

                if time_to_sleep_seconds:
                    if self.delay_time_metric is not None:
                        self.delay_time_metric.observe(time_to_sleep_seconds)
                    logger.debug(f"Sleeping for {time_to_sleep_seconds} seconds")
                    await asyncio.sleep(time_to_sleep_seconds)

            self.requests_total += 1

            start = timeit.default_timer()

            try:
                response = await super().send(request, *args, **kwargs)
            except httpx.ConnectError as e:
                if self.exceptions_counter_metric is None:
                    logger.warning(f"exceptions_counter_metric for {self.name} is None. Skipping metric recording")
                else:
                    self.exceptions_counter_metric.labels("connect").inc()

                self.requests_failed_to_connect += 1
                logger.warning(f"Failed to connect while requesting {request.url}: {e}. Retrying...")
                retries -= 1
                continue
            except httpx.ProxyError as e:
                if self.exceptions_counter_metric is None:
                    logger.warning(f"exceptions_counter_metric for {self.name} is None. Skipping metric recording")
                else:
                    self.exceptions_counter_metric.labels("proxy").inc()

                self.requests_failed_to_proxy += 1
                logger.warning(f"Failed to proxy while requesting {request.url}: {e}. Retrying...")
                retries -= 1
                continue
            except httpx.TimeoutException as e:
                if self.exceptions_counter_metric is None:
                    logger.warning(f"exceptions_counter_metric for {self.name} is None. Skipping metric recording")
                else:
                    self.exceptions_counter_metric.labels("timeout").inc()

                self.requests_timedout += 1
                logger.warning(f"Timeout {self.timeout} reached while requesting {request.url}: {e}. Retrying...")
                retries -= 1
                continue
            except Exception as e:
                if self.exceptions_counter_metric is None:
                    logger.warning(f"exceptions_counter_metric for {self.name} is None. Skipping metric recording")
                else:
                    self.exceptions_counter_metric.labels("other").inc()

                self.requests_errored += 1

                exc_msg = e.__class__.__name__
                if str(e):
                    exc_msg += f"({e})"

                logger.warning(f"Unexpected error while requesting {request.url}: {exc_msg}. Retrying...")
                retries -= 1
                continue

            # else:
            request_time = max(timeit.default_timer() - start, 0)

            if self.response_time_metric is None:
                logger.warning(f"response_time_metric for {self.name} is None. Skipping metric recording")
            else:
                self.response_time_metric.observe(request_time)

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
                if self.throttle_counter_metric is None:
                    logger.warning(f"throttle_counter_metric for {self.name} is None. Skipping metric recording")
                else:
                    self.throttle_counter_metric.inc()

                self.requests_throttled += 1
            else:
                self.requests_accepted += 1

            if response.status_code >= 400:
                self.requests_gte_400 += 1

            if response.status_code >= 400:
                if self.responses_gte_400_counter_metric is None:
                    logger.warning(
                        f"responses_gte_400_counter_metric for {self.name} is None. Skipping metric recording"
                    )
                else:
                    self.responses_gte_400_counter_metric.labels(response.status_code).inc()

            if self.log_request_details:
                self.log(request_time=request_time, next_request_allowed_in_seconds=next_request_allowed_in_seconds)

            if response.status_code < 400 or response.status_code == 404:  # Can't retry a 404
                return response

            logger.warning(
                f"Received {response.status_code} response ({response.text}) "
                f"while requesting {request.url}. Retrying..."
            )
            retries -= 1

        self.requests_retries_exhausted += 1

        if self.exceptions_counter_metric is None:
            logger.warning(f"exceptions_counter_metric for {self.name} is None. Skipping metric recording")
        else:
            self.exceptions_counter_metric.labels("retries_exceeded").inc()

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
    response_time_metric=MUSICBRAINZ_API_RESPONSE_TIME_SECONDS,
    responses_gte_400_counter_metric=MUSICBRAINZ_API_RESPONSES_GTE_400,
    exceptions_counter_metric=MUSICBRAINZ_API_EXCEPTIONS,
    throttle_counter_metric=MUSICBRAINZ_API_RESPONSES_THROTTLED,
    delay_time_metric=MUSICBRAINZ_API_REQUEST_DELAY_TIME_SECONDS,
    # log_request_details=True,
    timeout=httpx.Timeout(MUSICBRAINZ_API_REQUEST_TIMEOUT),
    base_url=MUSICBRAINZ_API_BASE_URL,
    headers={"Accept": "application/json", "User-Agent": settings.HTTP_USER_AGENT},
    proxy=settings.PROXY_URL,
    limits=httpx.Limits(max_connections=1000, max_keepalive_connections=1000),
)

async_songkick_client = AsyncRetryingClient(
    name="songkick",
    throttle_response_code=429,
    timeout=httpx.Timeout(SONGKICK_API_REQUEST_TIMEOUT),
    response_time_metric=SONGKICK_API_RESPONSE_TIME_SECONDS,
    responses_gte_400_counter_metric=SONGKICK_API_RESPONSES_GTE_400,
    exceptions_counter_metric=SONGKICK_API_EXCEPTIONS,
    # log_request_details=True,
    base_url=SONGKICK_BASE_URL,
    headers={"User-Agent": settings.HTTP_USER_AGENT},
    proxy=settings.PROXY_URL,
    limits=httpx.Limits(max_connections=1000, max_keepalive_connections=1000),
)

async_bandsintown_client = AsyncRetryingClient(
    name="bandsintown",
    throttle_response_code=403,
    timeout=httpx.Timeout(BANDSINTOWN_API_REQUEST_TIMEOUT),
    response_time_metric=BANDSINTOWN_API_RESPONSE_TIME_SECONDS,
    responses_gte_400_counter_metric=BANDSINTOWN_API_RESPONSES_GTE_400,
    exceptions_counter_metric=BANDSINTOWN_API_EXCEPTIONS,
    throttle_counter_metric=BANDSINTOWN_API_RESPONSES_THROTTLED,
    # log_request_details=True,
    base_url=BANDSINTOWN_BASE_URL,
    headers={"User-Agent": settings.HTTP_USER_AGENT},
    proxy=settings.PROXY_URL,
    limits=httpx.Limits(max_connections=1000, max_keepalive_connections=1000),
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
