from prometheus_client import Counter

SPOTIFY_API_RESPONSE = Counter(
    name="spotify_api_response",
    documentation="Spotify API response",
    labelnames=["method", "url", "status_code"],
)
