#!/usr/bin/env bash
# Runs all of mottle in one container, as on Fly.io: the scheduled jobs and the web server share the SQLite database
# on the volume, and a volume attaches to one machine only.

set -eux

mkdir -p "$OPENAI_IMAGES_DUMP_DIR"
/app/manage.py migrate --noinput

supercronic -passthrough-logs -json /app/crontab &
uvicorn --host 0.0.0.0 --port 8000 --lifespan off --no-access-log mottle.asgi:application &

# Fly stops the machine by signalling this script. On SIGTERM uvicorn finishes its requests and supercronic its jobs.
trap 'kill -TERM $(jobs -p) 2>/dev/null || true' INT TERM

# When either process exits on its own, the other is stopped too: the machine exits and Fly restarts it.
wait -n || true
kill -TERM $(jobs -p) 2>/dev/null || true
wait
