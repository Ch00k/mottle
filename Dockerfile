FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

RUN apt-get update && apt-get install \
    --no-install-recommends --no-install-suggests -y \
    curl \
    && rm -rf /var/cache/apt/archives /var/lib/apt/lists/*

WORKDIR /app

ARG UV_DEP_GROUPS="--group debug"

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --all-extras ${UV_DEP_GROUPS}

COPY . ./

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime

LABEL org.opencontainers.image.source=https://github.com/ch00k/mottle

RUN apt-get update && apt-get install \
    --no-install-recommends --no-install-suggests -y \
    libsqlite3-mod-spatialite \
    libgdal32 \
    && rm -rf /var/cache/apt/archives /var/lib/apt/lists/*

COPY --from=builder /app /app

WORKDIR /app

RUN mkdir -p /tmp/prometheus_multiproc_dir

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION} \
    PATH=/app/.venv/bin:${PATH} \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/app/run.sh"]
