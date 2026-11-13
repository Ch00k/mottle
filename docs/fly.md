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

## Moving production from oar

What moves is the database (`database/db.sqlite3`, 34 MB in November 2026) and the environment. Two things in oar's
project directory stay behind, in the archive taken at the cut-over: `images_dump/` (96 MB of generated covers dumped
for debugging, the last from September 2025; nothing reads them) and `tasks.sqlite3` (django-q's queue, unused since
the jobs moved to supercronic). oar's files are owned by root, so the commands reading them use sudo.

```bash
OAR=/opt/oar/data/projects/3d5ef5a3-208b-450a-9a27-04dc72764c06-mottle/git
```

Never let oar and Fly both run the jobs: each would email the users the same updates. A machine on Fly runs them, so
none runs there until oar has stopped.

### Preparing, with no downtime

1. Create the app and its addresses: `fly apps create mottle`, `fly ips allocate-v4 --shared`, `fly ips allocate-v6`.
2. Import oar's environment as secrets, leaving out the paths `fly.toml` and the image set and the listen address:

    ```bash
    oar project show 3d5ef5a3-208b-450a-9a27-04dc72764c06 \
        | sed -nE 's/^ *(Environment Variables)? +[0-9]+\. ([A-Z0-9_]+=.*[^ ]) *$/\2/p' \
        | grep -vE '^(WEB_LISTEN_HOST|WEB_LISTEN_PORT|STATIC_ROOT|DATABASE_FILE|OPENAI_IMAGES_DUMP_DIR)=' \
        | fly secrets import --stage
    ```

    `fly secrets list` must then show 21 secrets.
3. Add the certificates: `fly certs add` for `mottle.it`, `www.mottle.it` and `s.mottle.it`. So that they are issued
   while the records still point at this host, Fly validates them through the `_acme-challenge` CNAMEs and the
   `_fly-ownership` TXT records that `fly certs setup <host>` lists; add those to Cloudflare's zone. Go on once
   `fly certs show` reports all three issued.
4. Create the `mottle.it` zone in the Hetzner project (`hcloud zone create --name mottle.it --zonefile <file>`) with
   every record of Cloudflare's zone (`dns_records/export` in Cloudflare's API), the web hosts pointing at the app's
   addresses instead, unproxied, and the `_acme-challenge` CNAMEs. It serves nothing until the nameservers move.

### Cutting over

The site is down from step 5 to step 9, for about fifteen minutes. Stopping oar while a job runs (from 04:00 and
11:00 UTC, for up to five minutes) would cut it off after it emailed some users and before it recorded that, so keep
step 5 clear of those minutes. The image deployed must be
built from the commit oar runs (`oar project list`), so that the cut-over applies no migration; `make deploy` needs a
clean working tree, so run it from a worktree of that commit.

5. Stop oar: `sudo oar project stop 3d5ef5a3-208b-450a-9a27-04dc72764c06`. `oar project list` shows it stopped.
6. Copy the database out and check it, then archive everything oar's project holds:

    ```bash
    mkdir -p ~/mottle-move
    sudo sqlite3 $OAR/database/db.sqlite3 ".backup /home/ay/mottle-move/db.sqlite3"
    sudo chown ay: ~/mottle-move/db.sqlite3
    sqlite3 ~/mottle-move/db.sqlite3 "pragma integrity_check"  # ok
    sha256sum ~/mottle-move/db.sqlite3
    sudo tar -C $OAR -czf ~/mottle-move/oar-final.tar.gz database images_dump
    ```

7. Create the volume and put the database on it from a temporary machine, so that nothing has the database open while
   it is written:

    ```bash
    fly volumes create mottle_data --region fra --size 1
    fly machine run debian:trixie-slim sleep inf --region fra --volume mottle_data:/data
    fly ssh sftp shell  # put /home/ay/mottle-move/db.sqlite3 /data/db.sqlite3
    fly ssh console -C "sha256sum /data/db.sqlite3"  # the checksum from step 6
    fly machine destroy --force <the temporary machine's id>
    ```

8. `make deploy`. The app's machine takes the unattached volume (`fly volumes list` shows it attached). `fly logs` shows
   the migration finding nothing to apply, and supercronic and uvicorn starting.
9. Check the site before DNS reaches it: `curl -sI --resolve www.mottle.it:443:<the app's IPv4> https://www.mottle.it/`
   answers 200, and the same for `mottle.it` and a short link on `s.mottle.it`. Then point the A and AAAA records of
   `mottle.it` and `s.mottle.it`, now proxied by Cloudflare to this host, at the app's addresses, and stop proxying them
   and `www.mottle.it` (a CNAME to `mottle.it`), as Fly terminates TLS itself. Clients that cached the proxied answer
   reach Fly within five minutes. Sign in, which also proves the sessions and the Spotify tokens came along, and open a
   playlist. Cloudflare and Hetzner now give the same answers, so the nameservers can move at any time.

### Afterwards

10. The next day, check that both jobs ran (`fly logs`, Sentry) and that the update emails went out.
11. Until oar's project is removed, going back is possible. Copy the database out of the volume, with Python as the
    image has no sqlite3 CLI, and stop the app:

    ```bash
    fly ssh console  # python -c "import sqlite3; sqlite3.connect('/data/db.sqlite3').backup(sqlite3.connect('/data/back.sqlite3'))"
    fly ssh sftp get /data/back.sqlite3 /home/ay/mottle-move/back.sqlite3
    fly scale count 0
    ```

    Replace `$OAR/database/db.sqlite3` with that file (with sudo, removing any `-wal` and `-shm` file beside it), start
    oar's project with Deploy in its web UI, and restore the proxied DNS records.
12. Once it has run a day of jobs: remove the oar project (`sudo oar project remove
    3d5ef5a3-208b-450a-9a27-04dc72764c06`), drop `compose.prod.yaml` and this section, and update `CLAUDE.md`, which
    describes production as running in oar. Keep `~/mottle-move/oar-final.tar.gz`.
