from prometheus_client import Counter

SPOTIFY_RESPONSE = Counter(
    name="spotify_response",
    documentation="Spotify API response",
    labelnames=["method", "url", "status_code"],
)
