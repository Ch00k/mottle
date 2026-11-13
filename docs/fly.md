# Running on Fly.io

`fly.toml` describes the app.

## Releasing

A release is a tag `X.Y.Z` pushed on `main` (`git tag 0.28.0 && git push origin 0.28.0`). CI tests the tagged commit,
builds the image in the runner, pushes it to Fly's registry, deploys it with the tag as `APP_VERSION` (shown in the
footer, the Sentry release and the User-Agent), and checks that the site serves. Pushes to `main` and pull requests
only test. The deploy authenticates with a deploy token for the app (`fly tokens create deploy`), in the repository's
`FLY_API_TOKEN` secret.

## Layout

The SQLite database must be reachable by both the web server and the scheduled jobs, and a Fly volume attaches to one
machine only. So everything runs on a single machine: `serve.sh` migrates the database, then runs supercronic and
uvicorn side by side. When either of them exits, the script stops the other and exits, and Fly restarts the machine. The
machine never stops, as the jobs and the tasks in `web/background.py` run between requests. A deploy stops the machine
and starts the new one, which takes a few seconds and loses any running background task. A running scheduled job gets up
to five minutes (`kill_timeout`) to finish before the machine stops, and the site is down for that long, as uvicorn
stops at once: tag a release outside the jobs' runs at 04:00 and 11:00 UTC.

The volume, mounted at `/data`, holds the database and the dumped cover images. Fly snapshots it daily and keeps five
days of snapshots; there is no other backup.

The machine has 512 MB: the web server peaks at about 150 MB and a job that loads Django takes another 140 MB while it
runs, which leaves no room on 256 MB.

Static files are collected into the image when it is built and served by whitenoise.

Everything in the environment except the paths on the volume and `APP_VERSION` is in the app's secrets
(`fly secrets list`).

## The move from oar

Production moved from oar on this host to Fly.io on 13 November 2026, with about four minutes of downtime.

- The secrets were imported from oar's project config, less the paths `fly.toml` and the image set and oar's listen
  address. The certificates were issued before the cut-over through `_acme-challenge` CNAMEs and `_fly-ownership` TXT
  records, which stay in the zone for renewals.
- oar was stopped between the jobs' runs, so that no job was cut off and the two never both ran the jobs. The database
  was copied out with `sqlite3 .backup`, checked with `pragma integrity_check`, and put on the new volume from a
  temporary machine, its checksum compared on both ends. The commit oar ran was deployed, so no migration ran.
- oar's project directory, with the database and `images_dump/` (debugging dumps of generated covers, which did not
  move), is archived in `~/mottle-move/oar-final.tar.gz` on this host.
- DNS moved from Cloudflare's proxy to records pointing straight at the app, and the zone was copied to the Hetzner
  project, to which the nameservers move.

Until oar's project is removed, going back is possible. Copy the database out of the volume, with Python as the image
has no sqlite3 CLI, and stop the app:

```bash
fly ssh console  # python -c "import sqlite3; sqlite3.connect('/data/db.sqlite3').backup(sqlite3.connect('/data/back.sqlite3'))"
fly ssh sftp get /data/back.sqlite3 /home/ay/mottle-move/back.sqlite3
fly scale count 0
```

Replace `/opt/oar/data/projects/3d5ef5a3-208b-450a-9a27-04dc72764c06-mottle/git/database/db.sqlite3` with that file
(with sudo, removing any `-wal` and `-shm` file beside it), start oar's project with Deploy in its web UI, and point the
web records back at this host, proxied by Cloudflare.

Once Fly has run a day of jobs, oar's project is removed (`sudo oar project remove
3d5ef5a3-208b-450a-9a27-04dc72764c06`), and `compose.prod.yaml` and this section go.
