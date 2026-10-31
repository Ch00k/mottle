# mottle

The code repository that is powering the [mottle](https://www.mottle.it/) website.

mottle is set features built on top of Spotify, that include but are not limited to:

- discographies (playlists with all albums of an artist), with optional tracking of new releases and automatic updates
- merging playlists (combining multiple playlists into one), with optional tracking of updates
- tracking live shows of artists within a given radius

mottle is currently in a closed beta. Ask for an invite via feedback[at]mottle.it

## Development

- `make up` runs the app in Docker at <http://127.0.0.1:65534>.
- `make test` runs the tests in Docker, which provides GDAL and SpatiaLite.
- `make lint` formats and fixes what it can; `make check` verifies the lock file, formatting, lint, types and the
  stylesheet without changing anything.
- `make css` and `make check` need the Tailwind CLI at the version `TAILWIND_VERSION` in the `Makefile` names: the
  standalone binary from [its releases](https://github.com/tailwindlabs/tailwindcss/releases), as CI installs it, or
  Homebrew's `tailwindcss` at that version.

Notes on the event sources and on Spotify's API changes are in [docs](docs/).
