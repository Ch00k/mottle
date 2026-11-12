# Event sources

The daily event check (the `get_event_updates` job in `crontab`, `web.tasks.acheck_artists_for_event_updates`)
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
  The page of an artist with no upcoming events redirects to itself with `?noindex=1`, which is followed; seen in
  November 2026, when it failed about one tracked artist in five every day. A redirect to any other path fails the
  source, as that page's events would be read as the artist's.
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
makes up to 20 attempts without waiting between them, each from a different IP. With one attempt in five or six
getting through, all twenty fail about one time in fifty; the page is requested again on the next day's run.
Cookies Cloudflare sets make no difference.

In December 2025 the 403s were a TLS fingerprint block that moving to Python 3.14 got past; Cloudflare tightened it
since.

## One event per concert

Both sources list most concerts, under URLs that have nothing in common, so an artist's events are matched by date: an
event on a date the other source already has an event of the artist on is the same concert, and is left out. Whichever
source had the event stored first keeps it, so users are not told about a concert twice. Songkick is read first, so a
concert neither source has stored yet goes to Songkick.

An event is recognised by the ID its URL's last segment starts with (`EVENT_URL_END_PATTERN`): both sites rename an
event's URL when its venue or title changes, and keep the ID, so a renamed event's page is fetched again and the event
updated under its new URL.

An upcoming stored event its source no longer lists is deleted: it was cancelled, or moved to a date the other source
has an event on. A source whose reading failed deletes nothing, and neither does one that lists none of the artist's
events, as a markup change makes Songkick's calendar yield no events without failing; the events of an artist who really
has none left stay until their date passes. Songkick event data holding anything but one event fails the source, as the
events left out of it would be deleted. When the source that kept a concert drops it while the other still lists it, the
other source's event is stored on the next day's run, and users are told about it as a new event.

In November 2026 neither rule existed: the Bandsintown events stored while Songkick was broken, from January to
October, got a Songkick twin once Songkick was read again, about 400 upcoming concerts in all, and each renamed URL left
its old event behind. Migration `0013_remove_duplicate_events` deleted the twins by the same rules, matching the
sources by each event's latest URL and date.

`get_event_updates --force-refetch` fetches the page of every listed event, including those already stored with their
links, which are otherwise not fetched again.

## HTTP clients and the production proxy

The clients for all three sites (`web/events/http.py`) send their requests with curl_cffi, through
`CurlCffiTransport`, and through `PROXY_URL` when it is set; an empty value means no proxy. The Songkick and
Bandsintown clients impersonate Chrome, sending its User-Agent and Accept headers. The MusicBrainz client sends
mottle's own User-Agent, which MusicBrainz's API asks for.

Each client retries, with stamina, a request that fails to reach the site and a 429 or 5xx response; the Bandsintown
client also retries Cloudflare's 403, and the Songkick client Songkick's 406, which refused about one request in 70 in
November 2026 and let nearly all of them through when asked again. Any other error response fails at once, as asking
again would get the same answer. The MusicBrainz and Songkick clients wait before each retry, starting at about a
second and doubling up to ten. The Bandsintown client retries at once, as only a new exit IP helps there.

Each request of a redirect chain is retried on its own: a refused redirect target is requested again without the
redirect. Retrying the whole chain would need Cloudflare to let every request in it through in the same attempt.

Every request opens a new connection. The proxy assigns an exit IP per connection: a client that kept its
connections open sent all its retries from one IP, which made retrying after a refusal useless.
