import os
import subprocess
import sys
from unittest.mock import patch

from web.images import get_openai_client


def test_app_loads_without_openai() -> None:
    """Loading the app, Sentry included, leaves the OpenAI library unimported.

    It takes about 20 MB of memory that only cover image generation needs. The check runs in a fresh interpreter, as
    the test process may already have imported the library.
    """
    code = (
        "import sys, django; django.setup(); import mottle.asgi, web.views, web.tasks; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'openai'))"
    )

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "mottle.settings"},
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "[]"


def test_openai_client_is_created_once() -> None:
    """The OpenAI client is created on first use and reused after that."""
    get_openai_client.cache_clear()

    try:
        with patch("openai.OpenAI") as openai_class:
            first = get_openai_client()
            second = get_openai_client()
    finally:
        get_openai_client.cache_clear()

    openai_class.assert_called_once()
    assert first is second is openai_class.return_value
