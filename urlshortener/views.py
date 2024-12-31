from django.http import HttpRequest, HttpResponse
from django.shortcuts import aget_object_or_404, redirect
from django.views.decorators.http import require_GET

from urlshortener.models import ShortURL


@require_GET
async def get_url(_: HttpRequest, hash: str) -> HttpResponse:
    short_url = await aget_object_or_404(ShortURL, hash=hash)
    return redirect(short_url.url)
