FROM python:3.12-slim

WORKDIR /app

# Build deps for packages with C extensions (e.g. cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY config.sample.yaml config.yaml

RUN pip install --no-cache-dir .

EXPOSE 23 8080 8081 80 60006 1900/udp

CMD ["denon-proxy", "run", "--config", "/app/config.yaml"]
