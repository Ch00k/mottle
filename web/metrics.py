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
