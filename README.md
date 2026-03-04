# Denon AVR Proxy

[![CI - Tests](https://github.com/forsethc/denon-proxy/actions/workflows/tests.yml/badge.svg)](https://github.com/forsethc/denon-proxy/actions/workflows/tests.yml)

A virtual Denon AVR that allows **multiple clients** (Home Assistant, UC Remote 3, etc.) to connect simultaneously, while maintaining a **single Telnet connection** to the physical AVR.

## Why?

Denon AVR receivers only support **one active Telnet connection** at a time. If Home Assistant and UC Remote 3 both try to connect directly, they compete for that connection. This proxy:

- Accepts multiple client connections on a configurable port
- Forwards all commands to the physical AVR over one Telnet connection
- Broadcasts AVR responses to all connected clients
- Maintains internal state (power, volume, input, mute) so new clients see the correct state immediately

## Project Structure

- **`denon_proxy.py`** – Main proxy: Telnet multiplexer, client handling, AVR connection
- **`avr_state.py`** – Canonical Denon state model (`AVRState`) and volume presentation helpers; used by proxy, connection, and discovery
- **`avr_connection.py`** – AVR I/O: physical Telnet connection or in-process virtual AVR (same interface for the proxy)
- **`avr_discovery.py`** – AVR discovery: HTTP/SSDP (device discovery, Deviceinfo, AppCommand, MainZone XML). Used by the proxy when SSDP is enabled; can also be used standalone for testing

## Requirements

- Python 3.10+
- Physical Denon AVR on the same network with **Network Remote** enabled

## Installation

### Using a virtual environment (recommended)

```bash
cd denon-proxy
python3 -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Without a venv

```bash
cd denon-proxy
pip install -r requirements.txt
```

## Configuration

1. Copy `config.sample.yaml` to `config.yaml`
2. Edit `config.yaml` and set your AVR's IP address:

```yaml
avr_host: "192.168.1.100"   # Your Denon AVR's IP
```

### Configuration Options

| Option      | Default   | Description                                    |
|------------|-----------|------------------------------------------------|
| `avr_host` | "" (demo) | IP or hostname of the physical AVR. Empty = demo mode (no AVR) |
| `avr_port` | 23        | Telnet port on the AVR                         |
| `proxy_host` | 0.0.0.0 | Bind address (0.0.0.0 = all interfaces)       |
| `proxy_port` | 23      | Port clients connect to (Home Assistant requires 23)       |
| `log_level` | INFO     | DEBUG, INFO, WARNING, or ERROR (proxy logging)   |
| `denonavr_log_level` | INFO | Log level for the denonavr library (initial HTTP state sync); independent of `log_level` |
| `log_command_groups_info` | [] | Command groups to log at INFO: power, volume, input, mute, sound_mode |
| `enable_ssdp` | true   | SSDP discovery for Home Assistant (HTTP discovery server and SSDP responder) |
| `ssdp_friendly_name` | (optional) | Name shown in Home Assistant. If omitted: uses the physical AVR’s friendly name + " Proxy" (after HTTP sync), or "Denon AVR Proxy" if unknown. |
| `ssdp_http_port` | 8080 | Port for device description XML                |
| `ssdp_advertise_ip` | "" | IP to advertise (empty = auto-detect)      |
| `sources` | (from AVR or default) | Custom input sources: dict of `func_code: "Display Name"`. Omit to use actual device sources (including custom renames) when a physical AVR is connected |

### Troubleshooting "Unknown Error" or Timeout

If adding the device in Home Assistant fails with "Unknown error" or "Timeout":

1. **Check Home Assistant logs** – Settings → System → Logs. The actual exception (e.g. `AvrIncompleteResponseError`) will appear there and pinpoint the issue.
2. **Ensure port 60006 is available** – denonavr fetches device info from port 60006 for AVR-X models. The proxy binds 80, 8080, and 60006. If 60006 is in use, restart the proxy.
3. **Use the proxy IP** – When adding manually, enter the **proxy's** IP (the machine running denon-proxy), not the physical AVR's IP.
4. **Run proxy with DEBUG** – `log_level: DEBUG` in config will log which AppCommand requests denonavr sends.

### Environment Variables

You can override config with environment variables:

```bash
AVR_HOST=192.168.1.100 python denon_proxy.py
```

## Running

If using a venv, activate it first (`source venv/bin/activate` on macOS/Linux, `venv\Scripts\activate` on Windows).

```bash
python denon_proxy.py
```

Or with a custom config file:

```bash
python denon_proxy.py --config config.yaml
```

### Running on Port 23

The Home Assistant Denon integration only connects to port 23.

On some systems (especially many Linux distros), binding to low ports (<1024) may require elevated privileges or special capabilities. If you see a “permission denied” error when starting the proxy on port 23, re-run it with `sudo`, grant `cap_net_bind_service`, or use Docker with `--cap-add=NET_BIND_SERVICE`. If it starts fine without any of that (e.g. your environment already allows binding to 23), you can just run `python denon_proxy.py` as shown above.

### Docker

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

## Tests

This project uses **pytest** (with `pytest-asyncio` for async tests and `pytest-cov` for coverage).

- **Install test dependencies** (after installing runtime deps from `requirements.txt`):

```bash
pip install -r tests/requirements.txt
```

- **Run all tests** from the project root:

```bash
pytest
```

- **Run a specific test file or directory**:

```bash
pytest tests/unit/test_avr_state_volume.py      # single file
pytest tests/unit                               # all unit tests
```

- **Run tests with coverage**

```bash
pytest --cov                                 # text summary + missing lines
# or override the report format, e.g. HTML:
pytest --cov --cov-report=html               # then open htmlcov/index.html
```

## Connecting Clients

### Home Assistant

**With SSDP discovery** (recommended):

1. Ensure `enable_ssdp` is true in config (it defaults to true)
2. Ensure the proxy is reachable on port 23 from Home Assistant. If your OS refuses to bind to port 23 with a “permission denied” error, run the proxy with `sudo` or via Docker with `--cap-add=NET_BIND_SERVICE`.
3. Set `ssdp_advertise_ip` to your proxy's IP if auto-detect fails
4. Add the **Denon AVR Network Receivers** integration — the proxy should appear as "Discovered"

**Manual configuration:**

1. Ensure `enable_ssdp` is true in config (the HTTP server is needed for denonavr setup; it defaults to true)
2. Add the **Denon AVR Network Receivers** integration
3. Enter the proxy's IP address when prompted
4. The Home Assistant integration does not support custom ports — the proxy must run on port 23.

### UC Remote 3

1. Add a new device
2. Select Denon AVR
3. Enter the proxy's IP address (and port if not 23)

### Telnet Test

```bash
telnet <proxy-ip> 23
```

Then type Denon commands and press Enter:

- `PW?` - Query power state
- `PWON` - Power on
- `PWSTANDBY` - Power off
- `MV?` - Query volume
- `MV50` - Set volume to 50
- `SI?` - Query input
- `SICD` - Set input to CD
- `MUON` / `MUOFF` - Mute on/off

## Supported Commands

The proxy forwards **all** Denon Telnet commands transparently. Common commands:

| Command     | Description        | Example      |
|------------|--------------------|--------------|
| PW         | Power              | PWON, PWSTANDBY, PW? |
| MV         | Master volume      | MV50, MV?, MVUP, MVDOWN |
| SI         | Source/input       | SICD, SITUNER, SI? |
| MU         | Mute               | MUON, MUOFF, MU? |
| ZM         | Main zone power    | ZMON, ZMOFF, ZM? |
| MS         | Surround mode      | MSSTEREO, MS? |
| MSSMART    | Smart Select slot  | MSSMART ?, MSSMART0, MSSMART1 |

See Denon's protocol documentation for the full command set.

## SSDP Discovery

Home Assistant discovers Denon AVRs via SSDP/UPnP:

1. Home Assistant sends **M-SEARCH** (UDP multicast to 239.255.255.250:1900)
2. Devices respond with an HTTP-like reply containing a **LOCATION** URL
3. Home Assistant fetches that URL to get the device description XML (manufacturer, modelName, serialNumber, friendlyName)
4. Home Assistant uses the host from the LOCATION URL to connect (telnet port 23)

With `enable_ssdp` true, the proxy emulates this by:

- Responding to M-SEARCH on UDP 1900 (requires root on Linux)
- Serving a minimal UPnP device description at `http://<proxy_ip>:8080/description.xml`

The advertised name is taken from `ssdp_friendly_name` when set; otherwise the proxy uses the physical AVR’s friendly name + " Proxy" (once known from HTTP sync), or "Denon AVR Proxy" as a fallback. Home Assistant will show the proxy under that name when discovered.

## Architecture

```
[Home Assistant] ─┐
[UC Remote 3]   ─┼─► [Denon Proxy] ──► [Physical Denon AVR]
[Other client]  ─┘       (single Telnet)
```

- **Proxy server** listens on TCP port 23
- **State tracking** parses AVR responses to maintain power, volume, input, mute
- **New clients** receive current state immediately upon connection
- **Command forwarding** sends client commands to the AVR; responses are broadcast to all clients
- **Initial state** is optionally fetched via HTTP using the `denonavr` library

## Troubleshooting

### "Failed to connect to AVR"

- Ensure the AVR is on and connected to the network
- Enable **Network Remote** in the AVR settings
- Verify you can ping the AVR: `ping 192.168.1.100`
- Try telnet directly: `telnet 192.168.1.100 23`

### Clients don't see updates

- Ensure the proxy is running and connected to the AVR
- Check logs with `log_level: DEBUG` in config

### Power state not updating in Home Assistant (2026.x)

- The denonavr integration has **Use telnet** off by default. With telnet disabled, power updates come from HTTP polling (~10 seconds).
- For **instant** power updates: Integration → Configure → enable **Use telnet**. The proxy broadcasts ZM/ZMSTANDBY so Home Assistant receives power changes via telnet.

### Port 23 binding issues

- If you see a “permission denied” error when binding to port 23, run the proxy with `sudo` (or Docker with `cap_net_bind_service`), or choose a different port and use manual Home Assistant configuration if supported by your setup.

### Manual add fails instantly

- Set `log_level: "DEBUG"` in config and check logs for `HTTP: GET /goform/Deviceinfo.xml` — if you don't see it, Home Assistant isn't reaching the proxy
- Verify the HTTP server: `curl http://<proxy_ip>:8080/goform/Deviceinfo.xml` — should return XML
- Ports 80 and 60006: denonavr needs these for AVR-X 2016. The proxy binds 80, 8080, and 60006 when possible. Run with `sudo` if 80 or 60006 fail to bind
- Ensure `enable_ssdp` is true (the HTTP server only runs when SSDP is enabled; it defaults to true)

### SSDP discovery not working

- UDP 1900 often requires root on Linux: if binding fails with a permission error, run with `sudo` or adjust capabilities
- Set `ssdp_advertise_ip` to your proxy's LAN IP if auto-detect fails
- Ensure port 8080 is free for the device description HTTP server (or set `ssdp_http_port`)
- Home Assistant requires port 23 — make sure the proxy is listening on 23 and reachable from Home Assistant. If your OS refuses to bind to port 23 with a “permission denied” error, run the proxy with `sudo` or via Docker with `--cap-add=NET_BIND_SERVICE`.

## License

MIT
