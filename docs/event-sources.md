# Event sources

The daily event check (`EVENT_UPDATES` schedule, `web.tasks.check_artists_for_event_updates`) collects each tracked
artist's upcoming concerts from two websites and emails users about new or changed ones near their location. Neither
site has a public API mottle can use, so both are read from their HTML, and a change to their markup stops events from
arriving without any error: the pages still load, they just yield nothing.

An artist is matched to the sites through MusicBrainz, which links an artist's Spotify page to their Songkick and
Bandsintown pages (`web.events.data.MusicBrainzArtist`), with a name search on each site as the fallback.

## Songkick

- Upcoming events: the JSON-LD `MusicEvent` scripts in the artist's calendar page, under `div#calendar-summary.upcoming`
  (`SONGKICK_EVENTS_XPATH`). The page also lists past events under `div#gigography-summary`, which must not be read.
- Ticket links: the `data-ticket-path` of each `a.buy-ticket-link` on the event page (`SONGKICK_TICKETS_XPATH`).
- Songkick moves an artist's calendar when it merges or renames the artist, and answers the old URL with a 301. The
  redirect is followed and the new URL stored on the `EventArtist`.

In January 2026 Songkick wrapped the event list in a `div.event-listings-container` and moved ticket links from `href`
to `data-ticket-path`; nothing new was found from then until the XPaths were updated in October 2026.
`tests/test_events_data.py` reads trimmed copies of the current pages from `tests/data/`. When events stop arriving,
compare those with the live pages first.

## Bandsintown

- Events: the JSON-LD in `window.__data` on the artist page (`BANDSINTOWN_WINDOW_DATA_XPATH`).
- Since about August 2026 every request gets a 403 from Cloudflare with `cf-mitigated: challenge`, a JavaScript
  challenge, both through the production proxy and directly. Impersonating a browser's TLS fingerprint (`curl_cffi` with
  Chrome, Firefox or Safari profiles) still gets the challenge, so passing it would take a real browser.
- In December 2025 the same 403s were a TLS fingerprint block, which moving to Python 3.14 got past.
- A failing source is logged and leaves the events read from the other source in place.

## Production proxy

Requests to all three sites go through `PROXY_URL` when it is set. An empty value means no proxy.
