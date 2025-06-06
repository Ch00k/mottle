import re
import string

MUSICBRAINZ_API_BASE_URL = "https://musicbrainz.org/ws/2/"

BANDSINTOWN_BASE_URL = "https://www.bandsintown.com"
SONGKICK_BASE_URL = "https://www.songkick.com"

MUSICBRAINZ_API_REQUEST_TIMEOUT = 20
SONGKICK_API_REQUEST_TIMEOUT = 20
BANDSINTOWN_API_REQUEST_TIMEOUT = 20

SONGKICK_EVENT_URL_REGEX = re.compile(r"^https://www\.songkick\.com/(concerts|festivals|live-stream-concerts).*$")
SONGKICK_EVENTS_XPATH = (
    "//div[@id='calendar-summary' and contains(@class, 'upcoming')]/ol/li/div[@class='microformat']/script/text()"
)
SONGKICK_TICKETS_XPATH = "//a[contains(@class, 'buy-ticket-link')]/@href"
SONGKICK_LIVE_STREAM_XPATH = "//div[contains(@class, 'live-stream-link')]/a/@href"

BANDSINTOWN_WINDOW_DATA_XPATH = "//head/script[contains(text(),'window.__data=')]/text()"

ALNUM_TABLE = str.maketrans("", "", string.punctuation + string.whitespace)
