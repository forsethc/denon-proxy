PYTHON ?= python

INIT_STAMP := .venv-init.stamp
DEPS_FILES := pyproject.toml

PACKAGE_NAME ?= denon-proxy

# Docker test vars
SAMPLE_CONFIG ?= config.sample.yaml
MAKE_CONFIG_FILE ?= config.make.yaml
PORTS = -p 23:23 -p 8080:8080 -p 8081:8081 -p 60006:60006
DOCKER_HEALTHCHECK_CMD = sleep 3 && curl -sf http://localhost:8081/api/status


default: code-quality
.PHONY: default version run check check-all code-quality test docker mypy fix lint format format-fix lint-fix pytest docker-direct docker-compose version clean

clean:
	rm -f $(INIT_STAMP) $(MAKE_CONFIG_FILE)


init: $(INIT_STAMP)
	@echo "Python environment initialized."

$(INIT_STAMP): $(DEPS_FILES)
	@echo "Initializing Python environment..."
	@$(PYTHON) -m pip install --upgrade pip >/dev/null 2>&1
	@pip install -e ".[dev,test]" >/dev/null 2>&1
	@touch $(INIT_STAMP)

version: init
	$(PACKAGE_NAME) version

run: init
	$(PACKAGE_NAME) run

check: code-quality test

check-all: check docker

code-quality: init
	@rc=0; \
	printf '\n===== Checking type safety =====\n\n'; \
	$(MAKE) mypy || rc=1; \
	printf '\n===== Linting =====\n\n'; \
	$(MAKE) lint || rc=1; \
	printf '\n===== Formatting =====\n\n'; \
	$(MAKE) format || rc=1; \
	printf '\n\n'; \
	exit $$rc

fix: init
	lint-fix
	format-fix

test: init
	pytest

docker: init
	$(MAKE) docker-direct
	$(MAKE) docker-compose

mypy: init
	mypy src

lint: init
	ruff check src tests

lint-fix: init
	ruff check --fix src tests

format: init
	ruff format --diff src tests

format-fix: init
	ruff format src tests

pytest: init
	pytest

docker-direct:
	cp $(SAMPLE_CONFIG) $(MAKE_CONFIG_FILE)
	docker build -t $(PACKAGE_NAME) .
	docker run -d --name $(PACKAGE_NAME)-run \
	  --cap-add=NET_BIND_SERVICE \
	  $(PORTS) \
	  -v "$(PWD)/$(MAKE_CONFIG_FILE):/app/config.yaml:ro" \
	  $(PACKAGE_NAME)
	$(DOCKER_HEALTHCHECK_CMD)
	docker stop $(PACKAGE_NAME)-run
	docker rm $(PACKAGE_NAME)-run
	rm -f $(MAKE_CONFIG_FILE)

docker-compose:
	cp $(SAMPLE_CONFIG) $(MAKE_CONFIG_FILE)
	DENON_PROXY_CONFIG_PATH=./$(MAKE_CONFIG_FILE) docker compose up -d --build
	$(DOCKER_HEALTHCHECK_CMD)
	docker compose down
	rm -f $(MAKE_CONFIG_FILE)
