FROM python:3.12-slim AS builder

ENV PATH=/tmp/poetry/bin:${PATH} \
    POETRY_CACHE_DIR=/tmp/poetry_cache \
    POETRY_HOME=/tmp/poetry \
    POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_CREATE=true \
    POETRY_VIRTUALENVS_IN_PROJECT=true

RUN apt-get update && apt-get install \
    --no-install-recommends --no-install-suggests -y \
    curl \
    && rm -rf /var/cache/apt/archives /var/lib/apt/lists/*

RUN curl -sSL https://install.python-poetry.org | python3 -

WORKDIR /app

COPY pyproject.toml poetry.lock ./
RUN --mount=type=cache,target=${POETRY_CACHE_DIR} poetry install --only main,debug --no-root

COPY . ./

FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.source=https://github.com/ch00k/mottle

ARG APP_VERSION=dev

ENV APP_VERSION=${APP_VERSION} \
    PATH=/app/.venv/bin:${PATH} \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install \
    --no-install-recommends --no-install-suggests -y \
    libsqlite3-mod-spatialite \
    libgdal32 \
    && rm -rf /var/cache/apt/archives /var/lib/apt/lists/*

RUN groupadd -g 1000 app
RUN useradd -d /app -u 1000 -g 1000 -s /bin/bash app

COPY --from=builder --chown=app /app /app

USER app
WORKDIR /app

RUN mkdir -p /tmp/prometheus_multiproc_dir

ENTRYPOINT ["/app/run.sh"]
