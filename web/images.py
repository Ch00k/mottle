import logging
import math
import uuid
from base64 import b64decode, b64encode
from functools import cache
from io import BytesIO
from typing import TYPE_CHECKING

from django.conf import settings
from PIL import Image

if TYPE_CHECKING:
    from openai import OpenAI

logger = logging.getLogger(__name__)


GPT_MODEL = "gpt-4o"
IMAGE_MODEL = "dall-e-3"


@cache
def get_openai_client() -> OpenAI:
    # The OpenAI library takes about 18 MB of memory, so only a process that generates a cover image loads it
    from openai import OpenAI

    return OpenAI(api_key=settings.OPENAI_API_KEY)


def generate_image_prompt(playlist_title: str) -> str:
    message = (
        f"I need a cover image for a Spotify playlist titled '{playlist_title}'. "
        "The resulting image must have no text on it. "
        "Help me generate a prompt that I could give to DALL-E. "
        "Add as many details as you can. "
        "In your output include only the prompt itself."
    )

    logger.debug(f"Input message size: {len(message)} chars")

    completion = get_openai_client().chat.completions.create(
        model=GPT_MODEL, messages=[{"role": "user", "content": message}], n=1
    )

    result = completion.choices[0].message.content
    if not result:
        raise ValueError("Failed to generate prompt")

    logger.debug(f"Output message size: {len(result)} chars, token usage: {completion.usage}")

    return result


def generate_image(prompt: str) -> bytes:
    image = get_openai_client().images.generate(
        prompt=prompt,
        model=IMAGE_MODEL,
        quality="standard",
        style="vivid",
        n=1,
        response_format="b64_json",
        size="1024x1024",
    )

    if not image.data or not image.data[0].b64_json:
        raise ValueError("Failed to generate image")

    return b64decode(image.data[0].b64_json)


def resize_image(image_data: bytes, size: tuple[int, int]) -> bytes:
    original_image = Image.open(BytesIO(image_data))
    new_image_fp = BytesIO()

    resized_image = original_image.resize(size, resample=Image.Resampling.LANCZOS)
    resized_image.save(new_image_fp, format="PNG", optimize=True)

    return new_image_fp.getvalue()


def round_to_nearest_multiple_of_four(n: float) -> int:
    return 4 * math.ceil(n / 4)


def calculate_base64_size(data: bytes) -> int:
    return round_to_nearest_multiple_of_four(len(data) / 3 * 4)


def convert_image_to_jpg(image_data: bytes, default_quality: int = 95, size_limit: int = 256000) -> bytes:
    png_image = Image.open(BytesIO(image_data))
    quality = default_quality

    while True:
        jpg_image = BytesIO()
        png_image.save(jpg_image, format="JPEG", quality=quality, optimize=True)

        jpg_size = jpg_image.tell()
        base64_size = calculate_base64_size(jpg_image.getvalue())
        logger.debug(f"Resulting JPEG quality: {quality}, size: {jpg_size}, base64 size: {base64_size}")

        if base64_size >= size_limit:
            logger.debug(f"Base64 encoded size of {base64_size} bytes is too large, reducing quality and retrying")
            jpg_image.close()
            quality -= 1
        else:
            logger.debug(f"Base64 encoded size of {jpg_size} bytes is acceptable")
            break

    return jpg_image.getvalue()


def dump_image_to_disk(image_data: bytes, file_name: str, trace: str) -> None:
    file_path = settings.OPENAI_IMAGES_DUMP_DIR / f"{trace}_{file_name}"

    logger.debug(f"Dumping image data to disk: {file_path}")
    with open(file_path, "wb") as f:  # noqa: PTH123
        f.write(image_data)


def create_cover_image(playlist_title: str, dump_to_disk: bool = False) -> bytes:
    trace = str(uuid.uuid4())
    logger.debug(f"Generating cover image for playlist '{playlist_title}' with trace {trace}")

    prompt = generate_image_prompt(playlist_title)
    logger.debug(f"Generated cover image prompt for playlist '{playlist_title}': '{prompt}'")

    original_image_data = generate_image(prompt)
    size_original = len(original_image_data)
    logger.debug(f"Generated cover image data size: {size_original} bytes")

    if dump_to_disk:
        dump_image_to_disk(original_image_data, "original.png", trace)

    logger.debug(f"Resizing image data of size {size_original} bytes to 640x640")
    resized_png_data = resize_image(original_image_data, (640, 640))
    size_resized = len(resized_png_data)
    logger.debug(f"Resized image data size: {size_resized} bytes")

    if dump_to_disk:
        dump_image_to_disk(resized_png_data, "resized.png", trace)

    jpg_data = convert_image_to_jpg(resized_png_data)
    size_jpg = len(jpg_data)
    logger.debug(f"Converted image data size: {size_jpg} bytes")

    if dump_to_disk:
        dump_image_to_disk(jpg_data, "final.jpg", trace)

    base64_encoded = b64encode(jpg_data)
    size_base64 = len(base64_encoded)
    logger.debug(f"Base64 encoded image data size: {size_base64} bytes")

    return base64_encoded
