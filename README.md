# Denon AVR Proxy

[![CI - Tests](https://github.com/forsethc/denon-proxy/actions/workflows/tests.yml/badge.svg)](https://github.com/forsethc/denon-proxy/actions/workflows/tests.yml)

A virtual Denon AVR that allows **multiple clients** (Home Assistant, UC Remote 3, etc.) to connect simultaneously, while maintaining a **single Telnet connection** to the physical AVR.

## Why?

Denon AVR receivers only support **one active Telnet connection** at a time. If Home Assistant and UC Remote 3 both try to connect directly, they compete for that connection. This proxy:

- Accepts multiple client connections on a configurable port
- Forwards all commands to the physical AVR over one Telnet connection
- Broadcasts AVR responses to all connected clients
- Maintains internal state (power, volume, input, mute) so new clients see the correct state immediately

## Requirements

- Python 3.10+
- Physical Denon AVR on the same network with **Network Remote** enabled

## Quickstart

### Installation

**Using a virtual environment (recommended):**

```bash
cd denon-proxy
python3 -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**Without a venv:**

```bash
cd denon-proxy
pip install -r requirements.txt
```

### Running — config file (recommended)

Create `config.yaml` at the project root:

```yaml
avr_host: "192.168.1.100"   # Your Denon AVR's IP
```

Then run (activate venv first if using one):

```bash
python denon_proxy.py
```

### Running — environment override

Run without a config file:

```bash
AVR_HOST=192.168.1.100 python denon_proxy.py
```

For more options (ports, SSDP, logging, etc.), see [CONFIG.md](docs/CONFIG.md).

### Accessing the Web UI

Go to `http://<proxy_ip>:8081` in a browser to view AVR status, connected clients, and send commands.

See [API](docs/API.md) for the JSON API and more information.

### Connecting to Home Assistant

Add the **Denon AVR Network Receivers** integration in Home Assistant — the proxy should appear as discovered. If not, add it manually and enter the proxy's IP.

For more, see [Connecting Clients](#connecting-clients).


## Docker

Create a `config.yaml` from `config.sample.yaml` (recommended), then edit it with your AVR host and other settings.
You can still override individual values with environment variables by:

- Updating the `environment:` section in `docker-compose.yml`, or
- Adding `-e` flags to the `docker run` command.

```bash
cp config.sample.yaml config.yaml
```

**Option 1 – `docker compose` (recommended):**

```bash
docker compose up -d --build   # --build rebuilds the image (omit for a faster start if nothing changed)
```

**Option 2 – `docker` directly:**

```bash
docker build -t denon-proxy .

docker run -d --name denon-proxy \
  --cap-add=NET_BIND_SERVICE \
  -p 23:23 -p 8080:8080 -p 8081:8081 \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  denon-proxy
```

> **Note:** When running in Docker, the Web UI client list may show the Docker gateway address (e.g. `192.168.65.1` on Docker Desktop) multiple times—once per connected client. Each connection is still separate; the proxy handles multiple clients correctly.

## Connecting Clients

### Home Assistant

**With SSDP discovery** (recommended):

1. Ensure `enable_ssdp` is true in config (it defaults to true)
2. Ensure the proxy is reachable on port 23 from Home Assistant. If your OS refuses to bind to port 23 with a “permission denied” error, run the proxy with `sudo` or via Docker with `--cap-add=NET_BIND_SERVICE`.
3. Set `ssdp_advertise_ip` to your proxy's IP if auto-detect fails (common when running in Docker)
4. Add the **Denon AVR Network Receivers** integration — the proxy should be discovered by Home Assistant

**Manual configuration:**

1. Ensure `enable_ssdp` is true in config (the HTTP server is needed for denonavr setup; it defaults to true)
2. Add the **Denon AVR Network Receivers** integration
3. Enter the proxy's IP address when prompted
4. The Home Assistant integration does not support custom ports — the proxy must run on port 23.

### UC Remote 3

1. Add a new device
2. Select Denon AVR
3. Enter the proxy's IP address (and port if not 23)

### Telnet

See [TELNET.md](docs/TELNET.md) for connecting via Telnet, example commands, and supported commands.


## Development

Project structure, architecture, and testing for contributors. See [DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Troubleshooting

Common issues (AVR connection, port 23, Home Assistant, SSDP). See [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## License

MIT