from typing import Optional

from django import template
from django.templatetags.static import static
from tekore.model import Image, Item

register = template.Library()


ALLOWED_IMAGE_SIZES = [70, 300]
DEFAULT_IMAGE_SIZE = 70


def pick_image(images: Optional[list[Image]], largest_first: bool = True) -> Optional[str]:
    if not images:
        return None

    sorted_images = sorted(images, key=lambda i: (i.width is None, i.width), reverse=largest_first)
    image_url: str = sorted_images[0].url
    return image_url


def get_smallest_image(images: Optional[list[Image]]) -> Optional[str]:
    return pick_image(images, largest_first=False) or get_default_image(70)


def get_largest_image(images: Optional[list[Image]]) -> Optional[str]:
    return pick_image(images, largest_first=True) or get_default_image(300)


def get_default_image(size: int = 70) -> str:
    """Return the URL of the default image"""
    if size not in ALLOWED_IMAGE_SIZES:
        size = DEFAULT_IMAGE_SIZE
    return static(f"web/spotify_icon_black_{size}.png")


def get_spotify_url(item: Item) -> str:
    """Given an item, return its Spotify URL"""
    external_urls = getattr(item, "external_urls", None)
    if external_urls is None:
        return "#"

    external_url: str = external_urls.get("spotify", "#")
    return external_url


@register.filter
def year(release_date: str) -> str:
    return release_date.split("-", 1)[0]


def humanize_duration(duration_ms: int) -> str:
    """Given a duration in milliseconds, return a humanized duration"""
    duration_s = duration_ms // 1000
    minutes, seconds = divmod(duration_s, 60)
    return f"{minutes}:{seconds:02d}"
