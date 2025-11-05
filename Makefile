.PHONY: lint test up down logs ssh shell makemigrations manage debug css release-patch release-minor release-major

lint:
	uv run ruff format .
	uv run ruff check --fix .
	uv run mypy --config-file pyproject.toml .

test:
	docker compose --file compose.dev.yaml run --rm --no-deps --quiet-build web pytest -s -vvv --cov --cov-branch --cov-report=xml tests/

test_ci:
	uv run pytest -s -vvv --cov --cov-branch --cov-report=xml tests/

up:
	docker compose --file compose.dev.yaml up --remove-orphans

down:
	docker compose --file compose.dev.yaml down --remove-orphans

logs:
	docker compose --file compose.dev.yaml logs -f

ssh:
	docker compose --file compose.dev.yaml run --rm web bash

shell:
	docker compose --file compose.dev.yaml exec web ./manage.py shell

makemigrations:
	docker compose --file compose.dev.yaml exec web ./manage.py makemigrations

manage:
	docker compose --file compose.dev.yaml exec web ./manage.py $(CMD)

debug:
	docker compose --file compose.dev.yaml attach web  # exit with Ctrl+PQ

css:
	tailwindcss -i web/static/web/src/style.css -o web/static/web/style.css

release-patch:
	./release.sh patch

release-minor:
	./release.sh minor

release-major:
	./release.sh major
