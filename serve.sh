#!/usr/bin/env bash
# Runs all of mottle in one container, as on Fly.io: the scheduled jobs and the web server share the SQLite database
# on the volume, and a volume attaches to one machine only.

set -eux

mkdir -p "$OPENAI_IMAGES_DUMP_DIR"
/app/manage.py migrate --noinput

supercronic -passthrough-logs -json /app/crontab &

exec uvicorn --host 0.0.0.0 --port 8000 --lifespan off --no-access-log mottle.asgi:application
