FROM python:3.12-slim

WORKDIR /app

# Build deps for packages with C extensions (e.g. cryptography) and git for setuptools-scm
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY config.sample.yaml config.yaml

# Mount .git so setuptools-scm can infer version from tags (no copy into image).
# SOURCE_DATE_EPOCH pins the local-scheme timestamp so wheel filename matches metadata; otherwise pip fails the build.
# Requires Docker BuildKit (default in Docker 23+).
RUN --mount=source=.git,target=.git,type=bind \
    SOURCE_DATE_EPOCH=$(date +%s) pip install --no-cache-dir .

EXPOSE 23 8080 8081 80 60006 1900/udp

CMD ["denon-proxy", "run", "--config", "/app/config.yaml"]
