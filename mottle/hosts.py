from django.conf import settings
from django_hosts import host, patterns

host_patterns = patterns(
    "",
    host(r"", settings.ROOT_URLCONF, name="default"),
    host(r"s", "urlshortener.urls", name="urlshortener"),
)
