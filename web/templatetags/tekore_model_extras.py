from django import template
from django.conf import settings
from tekore.model import Album, Artist, Image, Track

register = template.Library()


@register.filter
def pick_image(images: list[Image]) -> str:
    """Given a list of images, return the URL of the smallest image, or None"""
    if not images:
        return settings.DEFAULT_IMAGE_URL

    sorted_images = sorted(images, key=lambda i: (i.width is None, i.width))
    image_url: str = sorted_images[0].url
    return image_url


@register.filter
def artists(track: Track) -> str:
    """Given a track, return a comma-separated string of artist names"""
    return ", ".join(artist.name for artist in track.artists)


@register.filter
def genres(artist: Artist) -> str:
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
