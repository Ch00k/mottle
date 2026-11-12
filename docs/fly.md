# Running on Fly.io

`fly.toml` describes the app. `make deploy` builds the image on Fly's builder from the working tree and deploys it.

## Layout

The SQLite database must be reachable by both the web server and the scheduled jobs, and a Fly volume attaches to one
machine only. So everything runs on a single machine: `serve.sh` migrates the database, then runs supercronic and
uvicorn side by side. When either of them exits, the script stops the other and exits, and Fly restarts the machine. The
machine never stops, as the jobs and the tasks in `web/background.py` run between requests. A deploy stops the machine
and starts the new one, which takes a few seconds and loses any running background task. A running scheduled job gets up
to five minutes (`kill_timeout`) to finish before the machine stops, and the site is down for that long, as uvicorn
stops at once: deploy outside the jobs' runs at 04:00 and 11:00 UTC.

The volume, mounted at `/data`, holds the database and the dumped cover images. Fly snapshots it daily and keeps five
days of snapshots; there is no other backup.

The machine has 512 MB: the web server peaks at about 150 MB and a job that loads Django takes another 140 MB while it
runs, which leaves no room on 256 MB.

Static files are collected into the image when it is built and served by whitenoise.

Everything in the environment except the paths on the volume is in the app's secrets (`fly secrets list`). `APP_VERSION`
is set by `make deploy` from `pyproject.toml`.

## Moving production from oar

1. Create the app and the secrets: `fly apps create mottle`, then `fly secrets import --stage` with every variable of
   oar's project config except `WEB_LISTEN_HOST`, `WEB_LISTEN_PORT`, `STATIC_ROOT`, `DATABASE_FILE`,
   `OPENAI_IMAGES_DUMP_DIR` and `APP_VERSION`.
2. Add the certificates: `fly certs add` for `mottle.it`, `www.mottle.it` and `s.mottle.it`. As Cloudflare proxies the
   records, Fly validates them through the `_acme-challenge` CNAMEs that `fly certs show <host>` lists; add those in
   Cloudflare. Cloudflare's SSL mode must be Full (strict).
3. Stop the oar project and copy its database out with `sqlite3 database/db.sqlite3 ".backup mottle.sqlite3"`.
4. Create the volume and put the database on it from a temporary machine, so that nothing has the database open while it
   is written:

    ```bash
    fly volumes create mottle_data --region fra --size 1
    fly machine run debian:trixie-slim sleep inf --region fra --volume mottle_data:/data
    fly ssh sftp shell  # put mottle.sqlite3 /data/db.sqlite3
    fly machine destroy --force <the temporary machine's id>
    ```

5. `make deploy`. The app's machine takes the unattached volume (`fly volumes list` shows it attached) and migrates the
   database.
6. Point the three DNS records at the app's addresses (`fly ips list`), still proxied, and sign in to check.
7. Once it has run a day of jobs, delete the oar project and drop `compose.prod.yaml`.
