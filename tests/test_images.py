import os
import subprocess
import sys
from base64 import b64decode, b64encode
from io import BytesIO
from typing import TYPE_CHECKING
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from web.images import (
    calculate_base64_size,
    convert_image_to_jpg,
    create_cover_image,
    get_openai_client,
    resize_image,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_django.fixtures import Settings


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


def make_png(size: tuple[int, int], noisy: bool = False) -> bytes:
    """Return a PNG of the given size: random pixels when noisy, which compress poorly, otherwise one colour."""
    if noisy:
        image = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    else:
        image = Image.new("RGB", size, (200, 30, 90))

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def save_as_jpg(png: bytes, quality: int) -> bytes:
    """Return the PNG converted to a JPEG of the given quality."""
    buffer = BytesIO()
    Image.open(BytesIO(png)).save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def test_resize_image() -> None:
    """A PNG is resized to the given size and comes back as a PNG."""
    resized = resize_image(make_png((1024, 1024)), (640, 640))

    image = Image.open(BytesIO(resized))
    assert image.format == "PNG"
    assert image.size == (640, 640)


@pytest.mark.parametrize("length", range(10))
def test_calculate_base64_size(length: int) -> None:
    """The size is the length of the data once base64 encoded, padding included."""
    data = b"x" * length

    assert calculate_base64_size(data) == len(b64encode(data))


def test_convert_image_to_jpg_keeps_default_quality_under_limit() -> None:
    """A PNG whose JPEG fits the limit at the default quality is converted at that quality."""
    png = make_png((640, 640))

    assert convert_image_to_jpg(png) == save_as_jpg(png, quality=95)


def test_convert_image_to_jpg_lowers_quality_until_under_limit() -> None:
    """A PNG whose JPEG is too large once base64 encoded is converted at lower qualities until it fits.

    The image is random noise, and the limit is set just under the size of its JPEG at the default quality. The result
    is the JPEG of the highest quality that fits.
    """
    png = make_png((300, 300), noisy=True)
    size_limit = len(b64encode(save_as_jpg(png, quality=95))) - 1000

    jpg = convert_image_to_jpg(png, size_limit=size_limit)

    quality = next(q for q in range(95, 0, -1) if len(b64encode(save_as_jpg(png, quality=q))) < size_limit)
    assert quality < 95
    assert jpg == save_as_jpg(png, quality=quality)


def mock_openai_client(prompt: str | None, image: bytes | None) -> Mock:
    """Return an OpenAI client whose chat completion answers the prompt and whose image generation returns the image."""
    client = Mock()
    client.chat.completions.create.return_value = Mock(choices=[Mock(message=Mock(content=prompt))])
    client.images.generate.return_value = Mock(
        data=[Mock(b64_json=None if image is None else b64encode(image).decode())]
    )
    return client


def test_create_cover_image(settings: Settings, tmp_path: Path) -> None:
    """A cover image is generated from a prompt written for the playlist title, then resized and converted to a JPEG.

    The OpenAI client is replaced. The result is the base64 encoded JPEG, and the generated, resized and final images
    are written to the dump directory, named after a trace the three share.
    """
    settings.OPENAI_IMAGES_DUMP_DIR = tmp_path
    generated = make_png((1024, 1024))
    client = mock_openai_client("A sunset over a desert highway", generated)

    with patch("web.images.get_openai_client", return_value=client):
        cover_image = create_cover_image("Road Trip", dump_to_disk=True)

    assert "'Road Trip'" in client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert client.images.generate.call_args.kwargs["prompt"] == "A sunset over a desert highway"

    jpg = b64decode(cover_image)
    image = Image.open(BytesIO(jpg))
    assert image.format == "JPEG"
    assert image.size == (640, 640)

    traces, file_names = zip(*(path.name.split("_", 1) for path in tmp_path.iterdir()), strict=True)
    assert len(set(traces)) == 1
    assert sorted(file_names) == ["final.jpg", "original.png", "resized.png"]

    trace = traces[0]
    assert (tmp_path / f"{trace}_original.png").read_bytes() == generated
    assert Image.open(tmp_path / f"{trace}_resized.png").size == (640, 640)
    assert (tmp_path / f"{trace}_final.jpg").read_bytes() == jpg


def test_create_cover_image_fails_without_prompt(settings: Settings, tmp_path: Path) -> None:
    """An empty prompt from the chat completion fails the generation before an image is requested."""
    settings.OPENAI_IMAGES_DUMP_DIR = tmp_path
    client = mock_openai_client("", make_png((1024, 1024)))

    with patch("web.images.get_openai_client", return_value=client), pytest.raises(ValueError, match="prompt"):
        create_cover_image("Road Trip")

    client.images.generate.assert_not_called()


def test_create_cover_image_fails_without_image(settings: Settings, tmp_path: Path) -> None:
    """Image generation that returns no image data fails, and nothing is written to the dump directory."""
    settings.OPENAI_IMAGES_DUMP_DIR = tmp_path
    client = mock_openai_client("A sunset over a desert highway", None)

    with patch("web.images.get_openai_client", return_value=client), pytest.raises(ValueError, match="image"):
        create_cover_image("Road Trip", dump_to_disk=True)

    assert list(tmp_path.iterdir()) == []
