PYTHON ?= python
# Docker test vars
IMAGE_NAME ?= denon-proxy
SAMPLE_CONFIG ?= config.sample.yaml
MAKE_CONFIG_FILE ?= config.make.yaml
PORTS = -p 23:23 -p 8080:8080 -p 8081:8081 -p 60006:60006
DOCKER_HEALTHCHECK_CMD = sleep 3 && curl -sf http://localhost:8081/api/status


default: code-quality
.PHONY: default init check check-all code-quality test docker mypy ruff-fix ruff-lint ruff-format ruff-format-fix ruff-lint-fix pytest docker-direct docker-compose

init:
	$(PYTHON) -m pip install --upgrade pip
	pip install -e ".[dev,test]"

check: code-quality test

check-all: check docker

code-quality:
	@rc=0; \
	printf '\n===== mypy: mypy src =====\n\n'; \
	$(MAKE) mypy || rc=1; \
	printf '\n===== ruff-lint: ruff check src tests =====\n\n'; \
	$(MAKE) ruff-lint || rc=1; \
	printf '\n===== ruff-format: ruff format --check src tests =====\n\n'; \
	$(MAKE) ruff-format || rc=1; \
	printf '\n\n'; \
	exit $$rc

ruff-fix: ruff-lint-fix ruff-format-fix

test: pytest

docker: docker-direct docker-compose

mypy:
	mypy src

ruff-lint:
	ruff check src tests

ruff-lint-fix:
	ruff check --fix src tests

ruff-format:
	ruff format --diff src tests

ruff-format-fix:
	ruff format src tests

pytest:
	pytest

docker-direct:
	cp $(SAMPLE_CONFIG) $(MAKE_CONFIG_FILE)
	docker build -t $(IMAGE_NAME) .
	docker run -d --name $(IMAGE_NAME)-run \
	  --cap-add=NET_BIND_SERVICE \
	  $(PORTS) \
	  -v "$(PWD)/$(MAKE_CONFIG_FILE):/app/config.yaml:ro" \
	  $(IMAGE_NAME)
	$(DOCKER_HEALTHCHECK_CMD)
	docker stop $(IMAGE_NAME)-run
	docker rm $(IMAGE_NAME)-run
	rm -f $(MAKE_CONFIG_FILE)

docker-compose:
	cp $(SAMPLE_CONFIG) $(MAKE_CONFIG_FILE)
	docker compose up -d --build
	$(DOCKER_HEALTHCHECK_CMD)
	docker compose down
	rm -f $(MAKE_CONFIG_FILE)
