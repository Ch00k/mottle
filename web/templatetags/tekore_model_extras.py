from typing import Optional

from django import template
from django.templatetags.static import static
from tekore.model import Album, Artist, FullArtist, Image, Track

register = template.Library()


ALLOWED_IMAGE_SIZES = [70]
DEFAULT_IMAGE_SIZE = 70


@register.filter
def pick_image(images: list[Image], size: int = 70) -> Optional[str]:
    """Given a list of images, return the URL of the smallest image, or None"""
    if not images:
        return None

    sorted_images = sorted(images, key=lambda i: (i.width is None, i.width))
    image_url: str = sorted_images[0].url
    return image_url


@register.filter
def default_image(image: Optional[str], size: int = 70) -> str:
    """Return the URL of the default image"""
    if image is not None:
        return image

    if size not in ALLOWED_IMAGE_SIZES:
        size = DEFAULT_IMAGE_SIZE
    return static(f"web/spotify_icon_black_{size}.png")


@register.filter
def spotify_url(item: Artist | Album | Track) -> str:
    """Given an item, return its Spotify URL"""
    external_urls = getattr(item, "external_urls", None)
    if external_urls is None:
        return "#"

    external_url: str = external_urls.get("spotify", "#")
    return external_url


@register.filter
def artists(track: Track) -> list[tuple[str, str]]:
    """Given a track, return a comma-separated string of artist names"""
    return [(artist.name, spotify_url(artist)) for artist in track.artists]


@register.filter
def genres(artist: FullArtist) -> str:
    """Given an artist, return a comma-separated string of genres"""
    return ", ".join(genre for genre in artist.genres)


@register.filter
def year(release_date: str) -> str:
    return release_date.split("-", 1)[0]


@register.filter
def duration_humanized(duration_ms: int) -> str:
    """Given a duration in milliseconds, return a humanized duration"""
    duration_s = duration_ms // 1000
    minutes, seconds = divmod(duration_s, 60)
    return f"{minutes}:{seconds:02d}"


@register.filter
def times(sequence: list) -> str:
    if len(sequence) == 0:
        return "never"
    elif len(sequence) == 1:
        return "once"
    elif len(sequence) == 2:
        return "twice"
    else:
        return f"{len(sequence)} times"
