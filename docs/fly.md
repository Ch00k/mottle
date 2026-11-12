# Running on Fly.io

`fly.toml` describes the app. `make deploy` builds the image on Fly's builder from the working tree and deploys it.

## Layout

The SQLite database must be reachable by both the web server and the scheduled jobs, and a Fly volume attaches to one
machine only. So everything runs on a single machine: `serve.sh` migrates the database, starts supercronic in the
background and then runs uvicorn. The machine never stops, as the jobs and the tasks in `web/background.py` run between
requests. A deploy stops the machine and starts the new one, which takes a few seconds and loses any running background
task.

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
2. `make deploy`. The first deploy creates the volume and an empty database; with no users in it the jobs do nothing.
3. Add the certificates: `fly certs add` for `mottle.it`, `www.mottle.it` and `s.mottle.it`. As Cloudflare proxies the
   records, Fly validates them through the `_acme-challenge` CNAMEs that `fly certs show <host>` lists; add those in
   Cloudflare. Cloudflare's SSL mode must be Full (strict).
4. Stop the oar project and copy its database out with `sqlite3 database/db.sqlite3 ".backup mottle.sqlite3"`.
5. Upload it and put it in place of the empty one, then restart the machine:

    ```bash
    fly ssh sftp shell  # put mottle.sqlite3 /data/upload.sqlite3
    fly ssh console -C "sh -c 'mv /data/upload.sqlite3 /data/db.sqlite3 && rm -f /data/db.sqlite3-wal /data/db.sqlite3-shm'"
    fly machine restart
    ```

6. Point the three DNS records at the app's addresses (`fly ips list`), still proxied, and sign in to check.
7. Once it has run a day of jobs, delete the oar project and drop `compose.prod.yaml`.
