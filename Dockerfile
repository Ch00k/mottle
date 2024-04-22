FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/cache/apt/archives /var/lib/apt/lists/*

ENV SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.29/supercronic-linux-amd64 \
    SUPERCRONIC=supercronic-linux-amd64

RUN curl -fsSLO ${SUPERCRONIC_URL} \
    && mv "$SUPERCRONIC" "/usr/local/bin/supercronic" \
    && chmod +x /usr/local/bin/supercronic

ARG APP_VERSION=dev

ENV APP_VERSION=${APP_VERSION}
ENV POETRY_VIRTUALENVS_CREATE=false

RUN pip install poetry
WORKDIR /src
COPY . .
RUN poetry install --only=main

ENTRYPOINT ["/src/run.sh"]
