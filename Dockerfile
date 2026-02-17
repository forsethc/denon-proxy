FROM python:3.12-slim

WORKDIR /app

# Build deps for packages with C extensions (e.g. cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY denon_proxy.py avr_emulator.py web_ui.py .
COPY config.sample.yaml config.yaml

EXPOSE 23 8080 8081 80 60006 1900/udp

CMD ["python", "denon_proxy.py", "--config", "/app/config.yaml"]
