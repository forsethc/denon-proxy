# Stage 1: build with .git copied (writable) so we can git reset --hard and get a clean tree for setuptools-scm.
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY .git /app/.git
COPY pyproject.toml README.md /app/
COPY src /app/src
COPY config.sample.yaml config.yaml

# Match working tree to HEAD so setuptools-scm does not see a dirty tree (COPY can change mtime/mode).
RUN git reset --hard HEAD

RUN SOURCE_DATE_EPOCH=$(date +%s) pip install --no-cache-dir --target /opt/app .

# Stage 2: runtime image without .git.
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /opt/app /opt/app
COPY config.sample.yaml config.yaml

ENV PATH="/opt/app/bin:$PATH" \
    PYTHONPATH="/opt/app"

EXPOSE 23 8080 8081 80 60006 1900/udp

CMD ["denon-proxy", "run", "--config", "/app/config.yaml"]
