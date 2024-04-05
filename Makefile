SHELL := bash

release:
	@$(eval APP_VERSION=$(shell poetry version $(VERSION) --short))
	@echo Releasing version: $(APP_VERSION)
	git add pyproject.toml
	git commit -m "Release $(APP_VERSION)"
	git push
	git tag -a $(APP_VERSION) -m $(APP_VERSION)
	git push origin $(APP_VERSION)

build:
	@$(eval TAG_NAME=$(shell git name-rev --name-only --tags --no-undefined HEAD 2>/dev/null | sed -n 's/^\([^^~]\{1,\}\)\(\^0\)\{0,1\}$$/\1/p'))
	@echo Git tag: $(TAG_NAME)
	@if [ -z "$(TAG_NAME)" ]; then echo "Not on a tag" && exit 1; fi
	@$(eval APP_VERSION=$(shell poetry version --short))
	@echo App version: $(APP_VERSION)
	@if [ "$(TAG_NAME)" != "$(APP_VERSION)" ]; then echo "Tag name $(TAG_NAME) does not match app version $(APP_VERSION)" && exit 1; fi
	@echo Building image from tag $(TAG_NAME), app version: $(APP_VERSION)
	docker build . -t mottle:$(APP_VERSION) -t mottle:latest --build-arg APP_VERSION=$(APP_VERSION)

deploy:
	@$(eval APP_VERSION=$(shell poetry version --short))
	@echo Deploying version: $(APP_VERSION)
	sed -i 's/image: mottle:.*/image: mottle:$(APP_VERSION)/' ${DEPLOYMENT_DIR}/docker-compose.yml
	docker-compose -f ${DEPLOYMENT_DIR}/docker-compose.yml up -d

PHONY: release_patch build deploy
