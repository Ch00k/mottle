SHELL := bash
DOCKER_BUILDKIT := 1

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

test:
	uv run pytest web/tests

debug:
	docker compose --file compose.dev.yaml attach web  # exit with Ctrl+PQ

css:
	tailwindcss -i web/static/web/src/style.css -o web/static/web/style.css

check:
	@if ! git diff-index --quiet HEAD --; then echo "Working directory is not clean" && exit 1; fi
	@if [ -n "$(shell git ls-files --exclude-standard --others)" ]; then echo "Working directory has untracked files" && exit 1; fi

tag: check
	@if [ -z "$(VERSION)" ]; then echo "VERSION is not set" && exit 1; fi
	@$(eval APP_VERSION=$(shell uv version --bump $(VERSION) --short))
	@echo Releasing version: $(APP_VERSION)
	git add pyproject.toml uv.lock
	git commit -m "Release $(APP_VERSION)"
	git push
	git tag -a $(APP_VERSION) -m $(APP_VERSION)
	git push origin $(APP_VERSION)

pre_release: check tag build deploy_pre
release: check tag build deploy
