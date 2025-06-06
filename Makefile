SHELL := bash
DOCKER_BUILDKIT := 1

build_dev:
	@echo Building development image
	docker build . -t mottle:latest --build-arg POETRY_GROUPS=main,debug,dev

up:
	@# TODO: This is awful
	@if [ "$(PROFILE)" == "T" ]; then \
		docker compose --profile default --profile taskrunner up --remove-orphans --detach; \
	elif [ "$(PROFILE)" == "S" ]; then \
		docker compose --profile default --profile scheduler up --remove-orphans --detach; \
	elif [ "$(PROFILE)" == "TS" ]; then \
		docker compose --profile default --profile taskrunner --profile scheduler up --remove-orphans --detach; \
	else \
		docker compose --profile default up --remove-orphans --detach; \
	fi

down:
	docker compose --profile default --profile taskrunner --profile scheduler down --remove-orphans

logs:
	docker compose --profile default --profile taskrunner --profile scheduler logs -f

ssh:
	docker-compose --profile default run --rm web bash

shell:
	docker compose exec web ./manage.py shell

test:
	poetry run pytest web/tests

debug:
	docker compose attach web

check:
	@if ! git diff-index --quiet HEAD --; then echo "Working directory is not clean" && exit 1; fi
	@if [ -n "$(shell git ls-files --exclude-standard --others)" ]; then echo "Working directory has untracked files" && exit 1; fi

tag: check
	@if [ -z "$(VERSION)" ]; then echo "VERSION is not set" && exit 1; fi
	@$(eval APP_VERSION=$(shell poetry version $(VERSION) --short))
	@echo Releasing version: $(APP_VERSION)
	git add pyproject.toml
	git commit -m "Release $(APP_VERSION)"
	git push
	git tag -a $(APP_VERSION) -m $(APP_VERSION)
	git push origin $(APP_VERSION)

build: tag
	@$(eval TAG_NAME=$(shell git name-rev --name-only --tags --no-undefined HEAD 2>/dev/null | sed -n 's/^\([^^~]\{1,\}\)\(\^0\)\{0,1\}$$/\1/p'))
	@echo Git tag: $(TAG_NAME)
	@if [ -z "$(TAG_NAME)" ]; then echo "Not on a tag" && exit 1; fi
	@$(eval APP_VERSION=$(shell poetry version --short))
	@echo App version: $(APP_VERSION)
	@if [ "$(TAG_NAME)" != "$(APP_VERSION)" ]; then echo "Tag name $(TAG_NAME) does not match app version $(APP_VERSION)" && exit 1; fi
	@echo Building image from tag $(TAG_NAME), app version: $(APP_VERSION)
	docker build . -t mottle:$(APP_VERSION) -t mottle:latest --build-arg APP_VERSION=$(APP_VERSION)
	docker image tag mottle:$(APP_VERSION) ghcr.io/ch00k/mottle:$(APP_VERSION)
	docker image tag mottle:$(APP_VERSION) ghcr.io/ch00k/mottle:latest
	docker push ghcr.io/ch00k/mottle:$(APP_VERSION)
	docker push ghcr.io/ch00k/mottle:latest

deploy_pre:
	@$(eval APP_VERSION=$(shell poetry version --short))
	@echo Deploying version: $(APP_VERSION)
	cp ${DEPLOYMENT_DIR_PRE}/database/db.sqlite3 ${DEPLOYMENT_DIR_PRE}/database/db.sqlite3.pre_$(APP_VERSION)
	cp ${DEPLOYMENT_DIR_PRE}/database/tasks.sqlite3 ${DEPLOYMENT_DIR_PRE}/database/tasks.sqlite3.pre_$(APP_VERSION)
	sed -i 's/image: ghcr.io\/ch00k\/mottle:.*/image: ghcr.io\/ch00k\/mottle:$(APP_VERSION)/' ${DEPLOYMENT_DIR_PRE}/compose.yml
	docker compose -f ${DEPLOYMENT_DIR_PRE}/compose.yml up -d

deploy:
	@$(eval APP_VERSION=$(shell poetry version --short))
	@echo Deploying version: $(APP_VERSION)
	cp ${DEPLOYMENT_DIR}/database/db.sqlite3 ${DEPLOYMENT_DIR}/database/db.sqlite3.pre_$(APP_VERSION)
	cp ${DEPLOYMENT_DIR}/database/tasks.sqlite3 ${DEPLOYMENT_DIR}/database/tasks.sqlite3.pre_$(APP_VERSION)
	sed -i 's/image: ghcr.io\/ch00k\/mottle:.*/image: ghcr.io\/ch00k\/mottle:$(APP_VERSION)/' ${DEPLOYMENT_DIR}/compose.yml
	docker compose -f ${DEPLOYMENT_DIR}/compose.yml up -d

pre_release: check tag build deploy_pre
release: check tag build deploy
