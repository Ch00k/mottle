FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/cache/apt/archives /var/lib/apt/lists/*

ARG APP_VERSION=dev

ENV APP_VERSION=${APP_VERSION}
ENV POETRY_VIRTUALENVS_CREATE=false

RUN pip install poetry
WORKDIR /src
COPY . .
RUN poetry install --only=main

ENTRYPOINT ["/src/run.sh"]
