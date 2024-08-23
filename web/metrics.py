from prometheus_client import Counter

SPOTIFY_API_RESPONSES = Counter(
    name="spotify_api_responses",
    documentation="Spotify API responses per method, URL (with Spotify IDs replaced by placeholders), and status code",
    labelnames=["method", "url", "status_code"],
)
