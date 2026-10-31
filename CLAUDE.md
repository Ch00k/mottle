# mottle

Django app at <https://www.mottle.it> built on Spotify: discography playlists, merged and watched playlists with emailed
updates, and emailed live events of followed artists near the user's location. Short URLs for ticket links are served
from <https://s.mottle.it> (`urlshortener`, routed by `django-hosts`).

## Stage

`0.x` - beta: a few real users (three accounts in production). Migrations must preserve their data; avoid breaking
changes to their playlists, settings and notifications.

## Running and deploying

- Production runs in [oar](https://github.com/oar-cd/oar) on this host, from `compose.prod.yaml`: web (daphne) and two
  django-q taskrunners. oar polls `main` and redeploys within minutes of every push, so a merge to `main` is a deploy.
  Its environment lives in oar's project config (`oar project show <id>`), not in the repository.
- GeoDjango needs GDAL and SpatiaLite. Homebrew's GDAL needs a newer glibc than this host has, so `make test` and
  anything that loads Django run in Docker (`compose.dev.yaml`); `make check` and `make lint` run on the host.
- `make check` is what CI runs before the tests, and changes no file.

## Decisions

- Type checking is ty with django-stubs; Django's reverse relations are annotated on the models for it.
- Django stays on 6.0 until django-prometheus supports 6.1, and pandas below 3, which country-converter cannot import
  with (`[tool.uv]` in `pyproject.toml`).
- Events are scraped from Songkick and Bandsintown, whose markup changes break them silently. Bandsintown has been
  behind a Cloudflare challenge since August 2026. Details: `docs/event-sources.md`.
- Spotify's February 2026 API changes have not reached the production app yet: `docs/spotify-feb-2026.md`.
- Repository: <https://github.com/Ch00k/mottle>. Changes go through pull requests; no merging without an explicit go.
