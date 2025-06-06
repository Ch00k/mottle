from prometheus_client import Counter, Summary

SPOTIFY_API_RESPONSE_TIME_SECONDS = Summary(
    name="spotify_api_response_time_seconds",
    documentation="Spotify API response time in seconds",
)

SPOTIFY_API_RESPONSES = Counter(
    name="spotify_api_responses",
    documentation="Spotify API responses per method, URL (with Spotify IDs replaced by placeholders), and status code",
    labelnames=["method", "url", "status_code"],
)

OPENAI_API_RESPONSE_TIME_SECONDS = Summary(
    name="openai_api_response_time_seconds",
    documentation="OpenAI API response time in seconds, by request type (chat_completion, image)",
    labelnames=["type"],
)

OPENAI_CHAT_COMPLETION_REQUEST_TOKENS_USED = Summary(
    name="openai_chat_completion_request_tokens_used",
    documentation="Number of tokens used in OpenAI chat completion requests",
)

OPENAI_CHAT_COMPLETION_RESPONSE_TOKENS_USED = Summary(
    name="openai_chat_completion_response_tokens_used",
    documentation="Number of tokens used in OpenAI chat completion responses",
)

OPENAI_CHAT_COMPLETION_REQUEST_SIZE_CHARS = Summary(
    name="openai_chat_completion_request_size_chars",
    documentation="Size of OpenAI chat completion requests in characters",
)

OPENAI_CHAT_COMPLETION_RESPONSE_SIZE_CHARS = Summary(
    name="openai_chat_completion_response_size_chars",
    documentation="Size of OpenAI chat completion responses in characters",
)

SPOTIFY_PLAYLIST_COVER_IMAGE_SIZE_BYTES = Summary(
    name="spotify_playlist_cover_image_size_bytes",
    documentation=(
        "Size of Spotify playlist cover images in bytes, by type (original, resized, converted, base64_encoded)"
    ),
    labelnames=["type"],
)

MUSICBRAINZ_API_RESPONSE_TIME_SECONDS = Summary(
    name="musicbrainz_api_response_time_seconds",
    documentation="MusicBrainz API response time in seconds",
)

MUSICBRAINZ_API_RESPONSES_GTE_400 = Counter(
    name="musicbrainz_api_responses_gte_400",
    documentation="MusicBrainz API responses with status code greater than or equal to 400, by status code",
    labelnames=["status_code"],
)

MUSICBRAINZ_API_EXCEPTIONS = Counter(
    name="musicbrainz_exceptions",
    documentation="Exceptions raised while calling MusicBrainz API, by exception type (connect, proxy, timeout, other)",
    labelnames=["type"],
)

MUSICBRAINZ_API_RESPONSES_THROTTLED = Counter(
    name="musicbrainz_api_responses_throttled",
    documentation="MusicBrainz API responses that indicate that client is being throttled",
)

MUSICBRAINZ_API_REQUEST_DELAY_TIME_SECONDS = Summary(
    name="musicbrainz_api_request_delay_time_seconds",
    documentation="Time spent waiting to send the next MusicBrainz API request (to avoid being throttled) in seconds",
)

SONGKICK_API_RESPONSE_TIME_SECONDS = Summary(
    name="songkick_api_response_time_seconds",
    documentation="Songkick API response time in seconds",
)

SONGKICK_API_RESPONSES_GTE_400 = Counter(
    name="songkick_api_responses_gte_400",
    documentation="Songkick API responses with status code greater than or equal to 400, by status code",
    labelnames=["status_code"],
)

SONGKICK_API_EXCEPTIONS = Counter(
    name="songkick_exceptions",
    documentation="Exceptions raised while calling Songkick API, by exception type (connect, proxy, timeout, other)",
    labelnames=["type"],
)

BANDSINTOWN_API_RESPONSE_TIME_SECONDS = Summary(
    name="bandsintown_api_response_time_seconds",
    documentation="Bandsintown API response time in seconds",
)

BANDSINTOWN_API_RESPONSES_GTE_400 = Counter(
    name="bandsintown_api_responses_gte_400",
    documentation="Bandsintown API responses with status code greater than or equal to 400, by status code",
    labelnames=["status_code"],
)

BANDSINTOWN_API_EXCEPTIONS = Counter(
    name="bandsintown_exceptions",
    documentation="Exceptions raised while calling Bandsintown API, by exception type (connect, proxy, timeout, other)",
    labelnames=["type"],
)

BANDSINTOWN_API_RESPONSES_THROTTLED = Counter(
    name="bandsintown_api_responses_throttled",
    documentation="Bandsintown API responses that indicate that client is being throttled",
)
