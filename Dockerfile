FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS builder

RUN apt-get update && apt-get install \
    --no-install-recommends --no-install-suggests -y \
    curl \
    git \
    && rm -rf /var/cache/apt/archives /var/lib/apt/lists/*

ARG SUPERCRONIC_VERSION=v0.2.51
ARG SUPERCRONIC_SHA1SUM=1692066e350a88ff4d7d6474fe3c7a6c41ec5e89

RUN curl -fsSL -o /usr/local/bin/supercronic \
    "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
    && echo "${SUPERCRONIC_SHA1SUM}  /usr/local/bin/supercronic" | sha1sum -c - \
    && chmod +x /usr/local/bin/supercronic

WORKDIR /app

ARG UV_DEP_GROUPS="--group debug"

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --all-extras ${UV_DEP_GROUPS}

COPY . ./

FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS runtime

LABEL org.opencontainers.image.source=https://github.com/ch00k/mottle

RUN apt-get update && apt-get install \
    --no-install-recommends --no-install-suggests -y \
    libsqlite3-mod-spatialite \
    libgdal36 \
    && rm -rf /var/cache/apt/archives /var/lib/apt/lists/*

COPY --from=builder /usr/local/bin/supercronic /usr/local/bin/supercronic
COPY --from=builder /app /app

WORKDIR /app

ENV PATH=/app/.venv/bin:${PATH} \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/app/run.sh"]
