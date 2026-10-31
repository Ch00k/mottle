import asyncio
import contextvars
import logging
from typing import TYPE_CHECKING, Any

from sentry_sdk import capture_exception

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = logging.getLogger(__name__)

# The event loop holds tasks only weakly, so a running task is kept here until it finishes.
running_tasks: set[asyncio.Task] = set()


def run_in_background(coroutine: Coroutine[Any, Any, None]) -> None:
    """Run the coroutine on the web server's event loop, independently of the request that started it.

    The task is lost if the process stops before it finishes.
    """
    # A task created in a request inherits the request's thread-sensitive context. Once the response is sent, that
    # context's executor is shut down, and the task's sync calls would get a thread of their own, with a database
    # connection nothing closes. An empty context runs them in asgiref's shared sync thread.
    task = asyncio.create_task(coroutine, context=contextvars.Context())
    running_tasks.add(task)
    task.add_done_callback(finish_task)


def finish_task(task: asyncio.Task) -> None:
    running_tasks.discard(task)

    if task.cancelled():
        return

    exception = task.exception()
    if exception is not None:
        logger.error("Background task failed", exc_info=exception)
        capture_exception(exception)
