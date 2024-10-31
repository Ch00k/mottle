import logging
import math
import uuid
from base64 import b64decode, b64encode
from io import BytesIO

import tiktoken
from django.conf import settings
from openai import OpenAI
from PIL import Image

from web.metrics import (
    OPENAI_API_RESPONSE_TIME_SECONDS,
    OPENAI_CHAT_COMPLETION_REQUEST_SIZE_CHARS,
    OPENAI_CHAT_COMPLETION_REQUEST_TOKENS_USED,
    OPENAI_CHAT_COMPLETION_RESPONSE_SIZE_CHARS,
    OPENAI_CHAT_COMPLETION_RESPONSE_TOKENS_USED,
    SPOTIFY_PLAYLIST_COVER_IMAGE_SIZE_BYTES,
)

client = OpenAI(api_key=settings.OPENAI_API_KEY)

logger = logging.getLogger(__name__)


GPT_MODEL = "gpt-4o"
IMAGE_MODEL = "dall-e-3"
TIKTOKEN_ENCODING = tiktoken.encoding_for_model(GPT_MODEL)
TOKENS_PER_MESSAGE = 3
TOKENS_PER_REPLY = 3


# https://cookbook.openai.com/examples/how_to_count_tokens_with_tiktoken#6-counting-tokens-for-chat-completions-api-calls
def message_to_num_tokens(message: str) -> int:
    num_tokens = len(TIKTOKEN_ENCODING.encode(message))
    num_tokens += TOKENS_PER_MESSAGE + TOKENS_PER_REPLY
    return num_tokens


def generate_image_prompt(playlist_title: str) -> str:
    message = (
        f"I need a cover image for a Spotify playlist titled '{playlist_title}'. "
        "The resulting image must have no text on it. "
        "Help me generate a prompt that I could give to DALL-E. "
        "Add as many details as you can. "
        "In your output include only the prompt itself."
    )

    message_size = len(message)
    message_tokens = message_to_num_tokens(message)

    OPENAI_CHAT_COMPLETION_REQUEST_SIZE_CHARS.observe(message_size)
    OPENAI_CHAT_COMPLETION_REQUEST_TOKENS_USED.observe(message_tokens)

    logger.debug(f"Input message size: {message_size} chars, {message_tokens} tokens")

    with OPENAI_API_RESPONSE_TIME_SECONDS.labels("chat_completion").time():
        completion = client.chat.completions.create(
            model=GPT_MODEL, messages=[{"role": "user", "content": message}], n=1
        )

    result = completion.choices[0].message.content
    if not result:
        raise ValueError("Failed to generate prompt")

    response_size = len(result)
    response_tokens = message_to_num_tokens(result)

    OPENAI_CHAT_COMPLETION_RESPONSE_SIZE_CHARS.observe(response_size)
    OPENAI_CHAT_COMPLETION_RESPONSE_TOKENS_USED.observe(response_tokens)

    logger.debug(f"Output message size: {response_size} chars, {response_tokens} tokens")

    return result


def generate_image(prompt: str) -> bytes:
    with OPENAI_API_RESPONSE_TIME_SECONDS.labels("image").time():
        image = client.images.generate(
            prompt=prompt,
            model=IMAGE_MODEL,
            quality="standard",
            style="vivid",
            n=1,
            response_format="b64_json",
            size="1024x1024",
        )

    data = image.data[0].b64_json
    if not data:
        raise ValueError("Failed to generate image")

    png_data = b64decode(data)
    return png_data


def resize_image(image_data: bytes, size: tuple[int, int]) -> bytes:
    original_image = Image.open(BytesIO(image_data))
    new_image_fp = BytesIO()

    resized_image = original_image.resize(size, resample=Image.Resampling.LANCZOS)
    resized_image.save(new_image_fp, format="PNG", optimize=True)

    return new_image_fp.getvalue()


def round_to_nearest_multiple_of_four(n: float | int) -> int:
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
    with open(file_path, "wb") as f:
        f.write(image_data)


def create_cover_image(playlist_title: str, dump_to_disk: bool = False) -> bytes:
    trace = str(uuid.uuid4())
    logger.debug(f"Generating cover image for playlist '{playlist_title}' with trace {trace}")

    prompt = generate_image_prompt(playlist_title)
    logger.debug(f"Generated cover image prompt for playlist '{playlist_title}': '{prompt}'")

    original_image_data = generate_image(prompt)
    size_original = len(original_image_data)
    logger.debug(f"Generated cover image data size: {size_original} bytes")
    SPOTIFY_PLAYLIST_COVER_IMAGE_SIZE_BYTES.labels("original").observe(size_original)

    if dump_to_disk:
        dump_image_to_disk(original_image_data, "original.png", trace)

    logger.debug(f"Resizing image data of size {size_original} bytes to 640x640")
    resized_png_data = resize_image(original_image_data, (640, 640))
    size_resized = len(resized_png_data)
    logger.debug(f"Resized image data size: {size_resized} bytes")
    SPOTIFY_PLAYLIST_COVER_IMAGE_SIZE_BYTES.labels("resized").observe(size_resized)

    if dump_to_disk:
        dump_image_to_disk(resized_png_data, "resized.png", trace)

    jpg_data = convert_image_to_jpg(resized_png_data)
    size_jpg = len(jpg_data)
    logger.debug(f"Converted image data size: {size_jpg} bytes")
    SPOTIFY_PLAYLIST_COVER_IMAGE_SIZE_BYTES.labels("converted").observe(size_jpg)

    if dump_to_disk:
        dump_image_to_disk(jpg_data, "final.jpg", trace)

    base64_encoded = b64encode(jpg_data)
    size_base64 = len(base64_encoded)
    logger.debug(f"Base64 encoded image data size: {size_base64} bytes")
    SPOTIFY_PLAYLIST_COVER_IMAGE_SIZE_BYTES.labels("base64_encoded").observe(size_base64)

    return base64_encoded
