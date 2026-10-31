# Spotify Web API - February 2026 Breaking Changes

Reference: https://developer.spotify.com/documentation/web-api/references/changes/february-2026

## Status (October 2026)

- Production still works against the old endpoints: in the last days of October 2026 its batch, `/playlists/{id}/tracks`
  and playlist creation calls returned 200 and 201. The changes did not reach mottle's app (in extended quota mode) by
  then.
- Playlists now come with both `tracks` and `items`. tekore 6.2 requires both, and mottle uses it.
- A first draft of the migration, [#78](https://github.com/Ch00k/mottle/pull/78), was closed unmerged: it conflicted
  with later refactors and called APIs tekore 6.2 does not have. The migration is to be redone against `main` once
  Spotify enforces the changes for this app.

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

All three are used in `MottleSpotifyClient` (`web/utils.py`) via `get_all_chunked` -> `perform_parallel_chunked_requests` -> `asyncio.gather`:

- `get_artists()`: fetches artists in chunks of 50. Called in the `user_events` view for the events page.
- `get_albums()`: fetches albums in default chunk size. Called in `compile_playlist_updates_email` (`web/views_utils.py`) for playlist update notifications.
- `get_tracks()`: fetches tracks in chunks of 50. Called in `compile_playlist_updates_email` and the `playlist_updates` view for displaying track details in updates.

**Migration**: Replace batch calls with concurrent individual calls (`artist()`, `album()`, `track()`) using `asyncio.gather` with a concurrency semaphore to respect rate limits (~15-20 concurrent requests). Call sites in views/views_utils do not need to change.

**Performance impact**: For a 10,000-track playlist, fetching track details goes from ~200 batch calls (~10s) to ~10,000 individual calls (~8.3 min at ~20 req/s). Real-world impact depends on typical list sizes in Mottle's use cases.

#### 2. `POST /users/{user_id}/playlists` removed

Used in `MottleSpotifyClient.create_playlist` and `create_playlist_with_tracks` via `playlist_create(user_id=current_user_id, ...)`.

**Migration**: Use `POST /me/playlists` (drop the `user_id` parameter). Tekore likely supports this already or will in an update.

#### 3. `DELETE /me/tracks` removed

Used in `MottleSpotifyClient.remove_user_saved_tracks` via `saved_tracks_delete(track_ids)`.

**Migration**: Use `DELETE /me/library` with Spotify URIs instead of track IDs.

#### 4. Playlist `tracks` field renamed to `items`

Used in `PlaylistData.from_tekore_model` (`web/data.py`) and `PlaylistMetadata._fetch_additional_data` (`web/views_utils.py`), both accessing `tracks.total`.

**Migration**: Update field access to use `items` instead of `tracks`. This likely depends on a Tekore library update to reflect the new response shape.

#### 5. User `email` field removed

Used in:
- `SpotifyUser.email`, set by `Playlist.watch_playlist` and `Playlist.watch_artist` (`web/models.py`)
- the `callback` view (reads email during auth, stores in session)
- `check_user_playlists_for_updates` and `acheck_artists_for_event_updates` (`web/tasks.py`, send notification emails)

The `user-read-email` scope (in `SPOTIFY_TOKEN_SCOPE`, `mottle/settings.py`) becomes useless.

**Migration**: No direct replacement. Mottle will need to collect email addresses directly from users (e.g., during signup or in settings) rather than pulling them from Spotify.

### Low Impact

- **Artist `followers`, `popularity`**: not accessed in code.
- **Track `external_ids`, `popularity`**: `external_ids` only in test fixtures.
- **Album `popularity`, `available_markets`, `label`**: not accessed in code.

## Current Data Loading Patterns

Mottle loads all Spotify data upfront with no pagination anywhere. Every view fetches the full dataset and passes it to the template in one go. There is no infinite scroll, pagination UI, or load-more pattern in the codebase.

### Display-only views (all data loaded just to render a list)

| View | View function | What it loads |
|------|----------|---------------|
| Saved tracks | `saved_tracks` | All user saved tracks |
| Playlist items | `playlist_items` | All tracks in a playlist (up to 10,000) |
| Discography modal | `albums` | All albums/singles/compilations for an artist |
| User playlists | `playlists` | All user playlists |
| Playlist update details | `playlist_updates` | New tracks for review |
| Events page | `user_events` | Artist details for events |

### Compute/write views (need full dataset before they can act)

| View | View function | What it loads | Why |
|------|----------|---------------|-----|
| Discography playlist creation | `albums` | All tracks from selected albums | Spotify API needs all URIs to create playlist |
| Playlist copying | `copy_playlist` | All tracks in source playlist | Same reason |
| Duplicate detection | `deduplicate_playlist` | All tracks in playlist | Must scan full playlist to find duplicates |
| Playlist merging | `merge_playlist` | All source playlist tracks | Needs all URIs |

### Known inefficiency

The `playlist_updates` view double-fetches tracks: first as `SimpleTrack` via `get_tracks_in_albums()`, then extracts IDs and re-fetches as `FullTrack` via `get_tracks()`.

## Mitigation Strategy

### Display-only views: htmx infinite scroll

For views that load data purely for display, implement htmx infinite scroll to load items in pages. Instead of fetching all 10,000 tracks upfront (10,000 individual API calls after batch removal), fetch ~20-50 per page as the user scrolls. This spreads API calls over time and makes initial page load fast.

Affected views: saved tracks, playlist items, discography album selection, user playlists, playlist update details, events page artists.

### Compute/write views: async background tasks

Operations that need the full dataset cannot benefit from pagination. These should be converted to fire-and-forget background tasks, run in the web process with `run_in_background` (`web/background.py`) like cover image generation:

- **Discography playlist creation**: user selects albums, task runs in background, notification when done.
- **Playlist copying**: kick off as background task.
- **Playlist merging**: already partially async, extend to cover full flow.
- **Duplicate detection/removal**: make fire-and-forget (auto-remove duplicates without confirmation step).

Users already receive email notifications for other async operations, so the UX pattern is established.

### Batch method rewrite (`web/utils.py`)

`get_artists()`, `get_albums()`, `get_tracks()` currently go through `get_all_chunked` -> `perform_parallel_chunked_requests` -> `asyncio.gather`, passing chunks of IDs to batch endpoints.

After the deprecation, replace with concurrent individual calls (`artist()`, `album()`, `track()`) using `asyncio.gather` with a concurrency semaphore (~15-20 concurrent requests) to stay within rate limits. Call sites in views/views_utils do not need to change.

## Tekore Dependency

Mottle uses Tekore 6.2 as its Spotify client library. 6.2 models the playlist `items` field alongside `tracks`, but still calls `POST /users/{user_id}/playlists`, the batch endpoints and the per-type library endpoints, so the rest of these changes still need a Tekore release that covers them. Monitor https://github.com/felix-hilden/tekore for one.

## Rate Limit Context

Spotify does not publish exact rate limits. Community estimates suggest ~20 requests/second is safe. Spotify's own best practices previously recommended using batch endpoints to reduce call volume -- removing them while keeping the same rate limits makes throttling and concurrency control more important. Mottle's Spotify client (`get_client` in `web/spotify.py`) sends through Tekore's `RetryingSender`, which waits out a 429 for as long as its `Retry-After` header says and retries a 5xx with exponential backoff, but the volume increase from individual calls may require adding a proactive concurrency limiter.
