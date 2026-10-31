# Spotify Web API - February 2026 Breaking Changes

Reference: https://developer.spotify.com/documentation/web-api/references/changes/february-2026

## Status (October 2026)

- Production still works against the old endpoints: in the last days of October 2026 its batch, `/playlists/{id}/tracks`
  and playlist creation calls returned 200 and 201. The changes did not reach mottle's app (in extended quota mode) by
  then.
- Playlists now come with both `tracks` and `items`. tekore 6.2 requires both, and mottle uses it.
- The migration is drafted in [#78](https://github.com/Ch00k/mottle/pull/78), unmerged: it calls endpoints and fields
  the production app may not have yet, so merge it only once Spotify enforces the changes for this app.

## Summary of API Changes

### Removed Endpoints

| Endpoint | Replacement |
|----------|-------------|
| `POST /users/{user_id}/playlists` | `POST /me/playlists` |
| `GET /artists/{id}/top-tracks` | None |
| `GET /markets` | None |
| `GET /browse/new-releases` | None |
| `GET /albums` (batch) | None |
| `GET /artists` (batch) | None |
| `GET /audiobooks` (batch) | None |
| `GET /browse/categories` | None |
| `GET /browse/categories/{id}` | None |
| `GET /chapters` (batch) | None |
| `GET /episodes` (batch) | None |
| `GET /shows` (batch) | None |
| `GET /tracks` (batch) | None |
| `GET /users/{id}/playlists` | `GET /me/playlists` |
| `GET /users/{id}` | `GET /me` |

### Library Endpoints Consolidated

Removed:
- `DELETE /me/albums`, `DELETE /me/audiobooks`, `DELETE /me/episodes`, `DELETE /me/shows`, `DELETE /me/tracks`
- `PUT /me/albums`, `PUT /me/audiobooks`, `PUT /me/episodes`, `PUT /me/shows`, `PUT /me/tracks`

Replaced with:
- `DELETE /me/library` (accepts Spotify URIs)
- `PUT /me/library` (accepts Spotify URIs)

### Field Removals

- **Album**: `album_group`, `available_markets`, `external_ids`, `label`, `popularity`
- **Artist**: `followers`, `popularity`
- **Playlist**: `tracks` renamed to `items` (`tracks.tracks` -> `items.items`, `tracks.tracks.track` -> `items.items.item`)
- **Track**: `available_markets`, `external_ids`, `linked_from`, `popularity`
- **User**: `country`, `email`, `explicit_content`, `followers`, `product`

### Developer Mode Changes (also February 2026)

- Test user limit reduced from 25 to 5
- Premium subscription required for developers
- Extended quota mode now requires a registered business with 250K+ MAU

## Impact on Mottle

### Critical

#### 1. Batch endpoints removed (`GET /artists`, `GET /albums`, `GET /tracks`)

All three are used in `web/utils.py` via `get_all_chunked` -> `perform_parallel_chunked_requests` -> `asyncio.gather`:

- `get_artists()` (line 99): fetches artists in chunks of 50. Called in `views.py:1111` for the events page.
- `get_albums()` (line 142): fetches albums in default chunk size. Called in `views_utils.py:312` for playlist update notifications.
- `get_tracks()` (line 146): fetches tracks in chunks of 50. Called in `views_utils.py:292`, `views.py:402`, `views.py:409` for displaying track details in updates.

**Migration**: Replace batch calls with concurrent individual calls (`artist()`, `album()`, `track()`) using `asyncio.gather` with a concurrency semaphore to respect rate limits (~15-20 concurrent requests). Call sites in views/views_utils do not need to change.

**Performance impact**: For a 10,000-track playlist, fetching track details goes from ~200 batch calls (~10s) to ~10,000 individual calls (~8.3 min at ~20 req/s). Real-world impact depends on typical list sizes in Mottle's use cases.

#### 2. `POST /users/{user_id}/playlists` removed

Used in `web/utils.py:197,227` via `playlist_create(user_id=current_user_id, ...)`.

**Migration**: Use `POST /me/playlists` (drop the `user_id` parameter). Tekore likely supports this already or will in an update.

#### 3. `DELETE /me/tracks` removed

Used in `web/utils.py:62,70` via `saved_tracks_delete(track_ids)`.

**Migration**: Use `DELETE /me/library` with Spotify URIs instead of track IDs.

#### 4. Playlist `tracks` field renamed to `items`

Used in `web/data.py:231` accessing `playlist.tracks.total`.

**Migration**: Update field access to use `items` instead of `tracks`. This likely depends on a Tekore library update to reflect the new response shape.

#### 5. User `email` field removed

Used in:
- `web/models.py:150` (SpotifyUser model stores email)
- `web/views.py:158,180` (reads email during auth, stores in session)
- `web/tasks.py:171-185,317-331` (sends notification emails)

The `user-read-email` scope (in `mottle/settings.py:266`) becomes useless.

**Migration**: No direct replacement. Mottle will need to collect email addresses directly from users (e.g., during signup or in settings) rather than pulling them from Spotify.

### Low Impact

- **Artist `followers`, `popularity`**: not accessed in code.
- **Track `external_ids`, `popularity`**: `external_ids` only in test fixtures.
- **Album `popularity`, `available_markets`, `label`**: not accessed in code.

## Current Data Loading Patterns

Mottle loads all Spotify data upfront with no pagination anywhere. Every view fetches the full dataset and passes it to the template in one go. There is no infinite scroll, pagination UI, or load-more pattern in the codebase.

### Display-only views (all data loaded just to render a list)

| View | Location | What it loads |
|------|----------|---------------|
| Saved tracks | `views.py:1132` | All user saved tracks |
| Playlist items | `views.py:479` | All tracks in a playlist (up to 10,000) |
| Discography modal | `views.py:258` | All albums/singles/compilations for an artist |
| User playlists | `views.py:362` | All user playlists |
| Playlist update details | `views.py:402,409` | New tracks for review |
| Events page | `views.py:1111` | Artist details for events |

### Compute/write views (need full dataset before they can act)

| View | Location | What it loads | Why |
|------|----------|---------------|-----|
| Discography playlist creation | `views.py:302` | All tracks from selected albums | Spotify API needs all URIs to create playlist |
| Playlist copying | `views.py:675` | All tracks in source playlist | Same reason |
| Duplicate detection | `views.py:637` | All tracks in playlist | Must scan full playlist to find duplicates |
| Playlist merging | `views.py:748` | All source playlist tracks | Needs all URIs |

### Known inefficiency

`views.py:407-409` double-fetches tracks: first as `SimpleTrack` via `get_tracks_in_albums()`, then extracts IDs and re-fetches as `FullTrack` via `get_tracks()`.

## Mitigation Strategy

### Display-only views: htmx infinite scroll

For views that load data purely for display, implement htmx infinite scroll to load items in pages. Instead of fetching all 10,000 tracks upfront (10,000 individual API calls after batch removal), fetch ~20-50 per page as the user scrolls. This spreads API calls over time and makes initial page load fast.

Affected views: saved tracks, playlist items, discography album selection, user playlists, playlist update details, events page artists.

### Compute/write views: async background tasks via django-q2

Operations that need the full dataset cannot benefit from pagination. These should be converted to fire-and-forget background tasks using the existing django-q2/taskrunner infrastructure:

- **Discography playlist creation**: user selects albums, task runs in background, notification when done.
- **Playlist copying**: kick off as background task.
- **Playlist merging**: already partially async, extend to cover full flow.
- **Duplicate detection/removal**: make fire-and-forget (auto-remove duplicates without confirmation step).

Users already receive email notifications for other async operations, so the UX pattern is established.

### Batch method rewrite (`web/utils.py`)

`get_artists()`, `get_albums()`, `get_tracks()` currently go through `get_all_chunked` -> `perform_parallel_chunked_requests` -> `asyncio.gather`, passing chunks of IDs to batch endpoints.

After the deprecation, replace with concurrent individual calls (`artist()`, `album()`, `track()`) using `asyncio.gather` with a concurrency semaphore (~15-20 concurrent requests) to stay within rate limits. Call sites in views/views_utils do not need to change.

## Tekore Dependency

Mottle uses Tekore v6.1.0 as its Spotify client library. Several of these changes (batch endpoint removal, playlist field rename, library endpoint consolidation) will require Tekore to release a compatible update. Monitor https://github.com/felix-hilden/tekore for a release addressing the February 2026 API changes.

## Rate Limit Context

Spotify does not publish exact rate limits. Community estimates suggest ~20 requests/second is safe. Spotify's own best practices previously recommended using batch endpoints to reduce call volume -- removing them while keeping the same rate limits makes throttling and concurrency control more important. Mottle's `MottleRetryingSender` (in `web/spotify.py`) already handles 429 responses with exponential backoff, but the volume increase from individual calls may require adding a proactive concurrency limiter.
