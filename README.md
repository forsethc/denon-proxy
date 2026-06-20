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
pip install .
```

**Without a venv (not recommended):**

```bash
cd denon-proxy
pip install .
```

### Running — config file (recommended)

Create `config.yaml` in the **project root** (the `denon-proxy` directory—same folder as `requirements.txt` and `src/`):

```yaml
avr_host: "192.168.1.100"   # Your Denon AVR's IP
```

Then run from the project root (activate venv first if using one). The `--config` path is relative to your current directory:

```bash
denon-proxy run
```

### Running — environment override

Run without a config file (using environment overrides):

```bash
AVR_HOST=192.168.1.100 denon-proxy run
```

For more options (ports, SSDP, logging, etc.), see [CONFIG.md](docs/CONFIG.md).

### Accessing the Web UI

Go to `http://<proxy_ip>:8081` in a browser to view AVR status, connected clients, and send commands.

See [API](docs/API.md) for the JSON API and more information.

### Connecting to Home Assistant

Add the **Denon AVR Network Receivers** integration in Home Assistant — the proxy should appear as discovered. If not, add it manually and enter the proxy's IP.

For more, see [Connecting Clients](#connecting-clients).


## Docker

Put `config.yaml` in the project root (or the directory you run `docker compose` from). Create it from the sample (recommended), then edit with your AVR host and other settings.
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

### Unfolded Circle Remote 3

1. Add a new device
2. Select Denon AVR
3. Enter the proxy's IP address (and port if not 23)
>>  **Note:** I recommend selecting .5 as the volume step (the default is 1). If it's not .5, the remote always sends an exact volume to go to, instead of just MVUP or MVDOWN.  This means that when the remote first wakes up and doesn't know what volume the AVR is at, it will default to 65.  If you press either the volume up or down buttons, it will go to 66 or 64, which can be a large jump in volume at once (mine is generally between 45-50).

**Alternative — HTTP custom entity (recommended if the Denon AVR integration is unreliable):**
The proxy exposes a feature-oriented HTTP API (`/api/avr/power/...`, `/api/avr/volume/...`, etc.) and a YAML generator that produces a ready-to-paste `custom_entities.yaml` for the [ucr2-integration-requests](https://github.com/kennymc-c/ucr2-integration-requests) integration. Open the Web UI and use the **UC Remote 3 Custom Entity** panel to copy the YAML. See [UC-REMOTE.md](docs/UC-REMOTE.md) for the full walkthrough.

### Telnet

See [TELNET.md](docs/TELNET.md) for connecting via Telnet, example commands, and supported commands.

### CLI tools

Once installed, denon-proxy also exposes a small CLI:

```bash
denon-proxy <command> [options]
```

Common commands:

- **`run`**: start the proxy server (same behavior as `python -m denon_proxy.main`).

  ```bash
  # Use config.yaml in the current directory
  denon-proxy run

  # Or specify a config file explicitly
  denon-proxy run --config /path/to/config.yaml
  ```

- **`version`**: print the installed denon-proxy version.

  ```bash
  denon-proxy version
  ```

- **`check-config`**: validate your configuration file (including YAML syntax and Pydantic validation) without starting the proxy.

  ```bash
  # Use config.yaml in the current directory
  denon-proxy check-config

  # Or validate an explicit path
  denon-proxy check-config --config /path/to/config.yaml
  ```

`check-config` exits with status **0** when the configuration is valid. On errors it prints a human-friendly message (missing file, invalid YAML, or detailed field validation errors) and exits with a non-zero status, which makes it suitable for CI checks.


## Development

Project structure, architecture, and testing for contributors. See [DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Troubleshooting

Common issues (AVR connection, port 23, Home Assistant, SSDP). See [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## License

MIT
