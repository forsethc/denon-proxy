FROM python:3.12-slim

WORKDIR /app

# Build deps for packages with C extensions (e.g. cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src /app/src
COPY config.sample.yaml config.yaml
COPY VERSION /app/VERSION

ENV PYTHONPATH=/app/src

EXPOSE 23 8080 8081 80 60006 1900/udp

CMD ["python", "-m", "denon_proxy.main", "--config", "/app/config.yaml"]
