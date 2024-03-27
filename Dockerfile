FROM python:3.12-slim

ENV POETRY_VIRTUALENVS_CREATE=false

RUN pip install poetry
WORKDIR /src
COPY . .
RUN poetry install --only=main

ENTRYPOINT ["/src/run.sh"]
