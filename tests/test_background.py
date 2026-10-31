import asyncio
import contextvars
import logging
import threading

import pytest
from asgiref.sync import ThreadSensitiveContext, sync_to_async

from web.background import run_in_background, running_tasks

pytestmark = pytest.mark.asyncio

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


async def wait_for_background_tasks() -> None:
    await asyncio.wait(set(running_tasks))


async def test_task_runs_after_caller_returns() -> None:
    """The coroutine starts only once the caller yields to the event loop, and is forgotten once it finishes."""
    calls = []

    async def work() -> None:
        calls.append("ran")

    run_in_background(work())

    assert calls == []
    assert len(running_tasks) == 1

    await wait_for_background_tasks()

    assert calls == ["ran"]
    assert running_tasks == set()


async def test_task_runs_sync_code_in_shared_thread() -> None:
    """Sync code a task runs after its request has finished goes to asgiref's shared sync thread.

    Django wraps every ASGI request in a ThreadSensitiveContext. A task that inherited it would get a new thread for
    its sync calls once the request's context exited, with a database connection of its own that nothing closes.
    """
    shared_thread_id = await sync_to_async(threading.get_ident)()
    context_exited = asyncio.Event()
    thread_ids = []

    async def work() -> None:
        await context_exited.wait()
        thread_ids.append(await sync_to_async(threading.get_ident)())

    async with ThreadSensitiveContext():
        run_in_background(work())

    context_exited.set()
    await wait_for_background_tasks()

    assert thread_ids == [shared_thread_id]


async def test_task_does_not_see_callers_context() -> None:
    """Context variables set by the caller are not visible to the background task."""
    seen = []

    async def work() -> None:
        seen.append(request_id.get())

    request_id.set("request-1")
    run_in_background(work())
    await wait_for_background_tasks()

    assert seen == [None]


async def test_failing_task_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """An exception raised by the task is logged with its traceback, and the task is forgotten."""

    async def work() -> None:
        raise ValueError("boom")

    with caplog.at_level(logging.ERROR, logger="web.background"):
        run_in_background(work())
        await wait_for_background_tasks()

    assert [(r.getMessage(), r.levelno) for r in caplog.records] == [("Background task failed", logging.ERROR)]
    exc_info = caplog.records[0].exc_info
    assert isinstance(exc_info, tuple)
    assert isinstance(exc_info[1], ValueError)
    assert str(exc_info[1]) == "boom"
    assert running_tasks == set()
