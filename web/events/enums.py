from enum import IntEnum, StrEnum


class ArtistNameMatchAccuracy(IntEnum):
    musicbrainz = 1100
    exact = 1000
    exact_alnum = 900
    exact_ascii = 800
    fuzzy = 700
    no_match = 0


class EventDataSource(StrEnum):
    songkick = "songkick"
    bandsintown = "bandsintown"


class EventType(StrEnum):
    concert = "concert"
    festival = "festival"  # TODO: Need to get the name of the festival in `Event`
    live_stream = "live_stream"
    other = "other"


SONGKICK_EVENT_TYPE_MAP = {
    "concerts": EventType.concert,
    "festivals": EventType.festival,
    "live-stream-concerts": EventType.live_stream,
}
