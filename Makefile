# The release of the Tailwind standalone CLI web/static/web/style.css is built with; another builds a different stylesheet.
TAILWIND_VERSION := v4.3.3

.PHONY: lint check css css-check tailwind-version test test_ci up down logs ssh shell makemigrations manage debug release-patch release-minor release-major

lint:
	uv run ruff format .
	uv run ruff check --fix --unsafe-fixes .
	uv run ty check

# What has to pass before a merge. Unlike lint it changes no file.
check: css-check
	uv lock --check
	uv run ruff format --check .
	uv run ruff check .
	uv run ty check

test:
	docker compose --file compose.dev.yaml run --rm --no-deps --quiet-build web pytest --cov --cov-branch --cov-report=xml $(if $(ARGS),$(ARGS),tests/)

test_ci:
	uv run pytest -s -vvv --cov --cov-branch --cov-report=xml tests/

up:
	docker compose --file compose.dev.yaml up --remove-orphans

up-all:
	docker compose --file compose.dev.yaml --profile taskrunner up --remove-orphans

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

# Without NO_COLOR the CLI colours its output where CI is set, as on GitHub's runners, even into a pipe.
tailwind-version:
	@[ "$$(NO_COLOR=1 tailwindcss --help 2>&1 | head -1 | awk '{print $$NF}')" = "$(TAILWIND_VERSION)" ] \
		|| { echo "The Tailwind CLI $(TAILWIND_VERSION) is required"; exit 1; }

css: tailwind-version
	tailwindcss -i web/static/web/src/style.css -o web/static/web/style.css --minify

# Fails when the committed stylesheet is not what its sources build. The CLI's output is shown only when the build
# itself fails.
css-check: tailwind-version
	@built=$$(mktemp) && trap 'rm -f "$$built"' EXIT; \
		output=$$(tailwindcss -i web/static/web/src/style.css -o "$$built" --minify 2>&1) || { echo "$$output"; exit 1; }; \
		cmp -s "$$built" web/static/web/style.css || { echo "web/static/web/style.css is out of date: run make css"; exit 1; }

release-patch:
	./release.sh patch

release-minor:
	./release.sh minor

release-major:
	./release.sh major
