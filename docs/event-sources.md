# Event sources

The daily event check (the `get_event_updates` job in `crontab`, `web.tasks.check_artists_for_event_updates`)
collects each tracked artist's upcoming concerts from two websites and emails users about new or changed ones near their
location. Neither site has a public API mottle can use, so both are read from their HTML, and a change to their markup
stops events from arriving without any error: the pages still load, they just yield nothing.

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

- Upcoming events: the JSON-LD `MusicEvent` list in the artist page's head (`BANDSINTOWN_JSON_LD_XPATH`), the only
  list among the head's JSON-LD scripts, empty when the artist has none. A page without that list fails the source.
- Venue and ticket links: `window.__data` on the event page (`BANDSINTOWN_WINDOW_DATA_XPATH`). The venue comes from
  `jsonLdContainer.eventJsonLd.location`, as the artist page's events leave out the address; the ticket links from
  `eventView.body.detailedTicketList.ticketList[].directTicketUrl`, and a stream's link from
  `eventView.body.hybridEventDetails.streamingUrl`.
- Until about August 2026 the events were in `window.__data` on the artist page, under `jsonLdContainer.eventsJsonLd`,
  with the full address. `tests/data/` holds trimmed copies of the pages as of October 2026.
- A failing source is logged and leaves the events read from the other source in place.

### Getting past Cloudflare

Bandsintown is behind Cloudflare's bot protection, which since about August 2026 answers 403 to most requests. What
decides it, as measured in October 2026:

- The TLS fingerprint. Python's (httpx, httpx2, over HTTP/1.1 or HTTP/2, with any User-Agent) was refused on every
  attempt from every exit IP. curl_cffi impersonating Chrome gets through from some exit IPs, about one attempt in
  six.
- The exit IP. Artist pages requested straight from the production host, without the proxy, were all refused, with
  or without impersonation.

So the Bandsintown client impersonates Chrome, sends each request on a new connection through the rotating proxy, and
retries a 403 (`throttle_response_code`) up to 20 times, each from a different IP. With one attempt in five or six
getting through, all twenty fail about one time in fifty; the page is requested again on the next day's run.
Cookies Cloudflare sets make no difference.

In December 2025 the 403s were a TLS fingerprint block that moving to Python 3.14 got past; Cloudflare tightened it
since.

## HTTP clients and the production proxy

The clients for all three sites (`web/events/http.py`) send their requests with curl_cffi, through
`CurlCffiTransport`, and through `PROXY_URL` when it is set; an empty value means no proxy. The Songkick and
Bandsintown clients impersonate Chrome, sending its User-Agent and Accept headers. The MusicBrainz client sends
mottle's own User-Agent, which MusicBrainz's API asks for.

Every request opens a new connection. The proxy assigns an exit IP per connection: a client that kept its
connections open sent all its retries from one IP, which made retrying after a refusal useless.
