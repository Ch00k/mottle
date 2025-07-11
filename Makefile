SHELL := bash
DOCKER_BUILDKIT := 1

build_dev:
	@echo Building development image
	docker build . -t mottle:latest --build-arg UV_DEP_GROUPS="--group dev --group debug"

up:
	docker compose --profile default --profile taskrunner up --remove-orphans --detach; \

down:
	docker compose --profile default --profile taskrunner down --remove-orphans

logs:
	docker compose --profile default --profile taskrunner logs -f

ssh:
	docker-compose --profile default run --rm web bash

shell:
	docker compose exec web ./manage.py shell

makemigrations:
	docker compose exec web ./manage.py makemigrations

manage:
	docker compose exec web ./manage.py $(CMD)

test:
	uv run pytest web/tests

debug:
	docker compose attach web  # exit with Ctrl+PQ

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

build: tag
	@$(eval TAG_NAME=$(shell git name-rev --name-only --tags --no-undefined HEAD 2>/dev/null | sed -n 's/^\([^^~]\{1,\}\)\(\^0\)\{0,1\}$$/\1/p'))
	@echo Git tag: $(TAG_NAME)
	@if [ -z "$(TAG_NAME)" ]; then echo "Not on a tag" && exit 1; fi
	@$(eval APP_VERSION=$(shell uv version --short))
	@echo App version: $(APP_VERSION)
	@if [ "$(TAG_NAME)" != "$(APP_VERSION)" ]; then echo "Tag name $(TAG_NAME) does not match app version $(APP_VERSION)" && exit 1; fi
	@echo Building image from tag $(TAG_NAME), app version: $(APP_VERSION)
	docker build . -t mottle:$(APP_VERSION) -t mottle:latest --build-arg APP_VERSION=$(APP_VERSION)
	docker image tag mottle:$(APP_VERSION) ghcr.io/ch00k/mottle:$(APP_VERSION)
	docker image tag mottle:$(APP_VERSION) ghcr.io/ch00k/mottle:latest
	docker push ghcr.io/ch00k/mottle:$(APP_VERSION)
	docker push ghcr.io/ch00k/mottle:latest

deploy_pre:
	@$(eval APP_VERSION=$(shell uv version --short))
	@echo Deploying version: $(APP_VERSION)
	cp ${DEPLOYMENT_DIR_PRE}/database/db.sqlite3 ${DEPLOYMENT_DIR_PRE}/database/db.sqlite3.pre_$(APP_VERSION)
	cp ${DEPLOYMENT_DIR_PRE}/database/tasks.sqlite3 ${DEPLOYMENT_DIR_PRE}/database/tasks.sqlite3.pre_$(APP_VERSION)
	sed -i 's/image: ghcr.io\/ch00k\/mottle:.*/image: ghcr.io\/ch00k\/mottle:$(APP_VERSION)/' ${DEPLOYMENT_DIR_PRE}/compose.yml
	docker compose -f ${DEPLOYMENT_DIR_PRE}/compose.yml up --remove-orphans --detach

deploy:
	@$(eval APP_VERSION=$(shell uv version --short))
	@echo Deploying version: $(APP_VERSION)
	cp ${DEPLOYMENT_DIR}/database/db.sqlite3 ${DEPLOYMENT_DIR}/database/db.sqlite3.pre_$(APP_VERSION)
	cp ${DEPLOYMENT_DIR}/database/tasks.sqlite3 ${DEPLOYMENT_DIR}/database/tasks.sqlite3.pre_$(APP_VERSION)
	sed -i 's/image: ghcr.io\/ch00k\/mottle:.*/image: ghcr.io\/ch00k\/mottle:$(APP_VERSION)/' ${DEPLOYMENT_DIR}/compose.yml
	docker compose -f ${DEPLOYMENT_DIR}/compose.yml up --remove-orphans --detach

pre_release: check tag build deploy_pre
release: check tag build deploy
