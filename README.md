# Denon AVR Proxy

A virtual Denon AVR that allows **multiple clients** (Home Assistant, UC Remote 3, etc.) to connect simultaneously, while maintaining a **single Telnet connection** to the physical AVR.

## Why?

Denon AVR receivers only support **one active Telnet connection** at a time. If Home Assistant and UC Remote 3 both try to connect directly, they compete for that connection. This proxy:

- Accepts multiple client connections on a configurable port
- Forwards all commands to the physical AVR over one Telnet connection
- Broadcasts AVR responses to all connected clients
- Maintains internal state (power, volume, input, mute) so new clients see the correct state immediately

## Project Structure

- **`denon_proxy.py`** – Main proxy: Telnet multiplexer, client handling, AVR connection
- **`avr_emulator.py`** – Denon AVR emulation: `AVRState` (canonical state model), HTTP/SSDP (device discovery, Deviceinfo, AppCommand, MainZone XML). Used by the proxy when SSDP is enabled; can also be used standalone for testing

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
proxy_port: 2323             # Port for clients (2323 avoids needing root for port 23)
```

### Configuration Options

| Option      | Default   | Description                                    |
|------------|-----------|------------------------------------------------|
| `avr_host` | "" (demo) | IP or hostname of the physical AVR. Empty = demo mode (no AVR) |
| `avr_port` | 23        | Telnet port on the AVR                         |
| `proxy_host` | 0.0.0.0 | Bind address (0.0.0.0 = all interfaces)       |
| `proxy_port` | 2323    | Port clients connect to                        |
| `log_level` | INFO     | DEBUG, INFO, WARNING, or ERROR                  |
| `enable_ssdp` | false  | SSDP discovery for Home Assistant              |
| `ssdp_friendly_name` | Denon AVR Proxy | Name shown in Home Assistant           |
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
AVR_HOST=192.168.1.100 PROXY_PORT=2323 python denon_proxy.py
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

### Running on Port 23 (Standard Telnet)

On Linux/macOS, port 23 typically requires root:

```bash
sudo python denon_proxy.py
# And set proxy_port: 23 in config
```

Or use port 2323 (default) and configure clients to connect to that port instead.

## Connecting Clients

### Home Assistant

**With SSDP discovery** (recommended):

1. Set `enable_ssdp: true` in config
2. Set `proxy_port: 23` (Home Assistant expects telnet on port 23) and run with `sudo` if needed
3. Set `ssdp_advertise_ip` to your proxy's IP if auto-detect fails
4. Add the **Denon AVR Network Receivers** integration — the proxy should appear as "Discovered"

**Manual configuration:**

1. Set `enable_ssdp: true` in config (the HTTP server is needed for denonavr setup)
2. Add the **Denon AVR Network Receivers** integration
3. Enter the proxy's IP address when prompted
4. For custom port (e.g. 2323), use manual configuration if the integration supports it

> **Note:** The standard Home Assistant Denon integration uses port 23. If you use proxy_port 2323, you may need a custom integration or to run the proxy as root on port 23.

### UC Remote 3

1. Add a new device
2. Select Denon AVR
3. Enter the proxy's IP address (and port if not 23)

### Telnet Test

```bash
telnet <proxy-ip> 2323
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

See Denon's protocol documentation for the full command set.

## SSDP Discovery

Home Assistant discovers Denon AVRs via SSDP/UPnP:

1. HA sends **M-SEARCH** (UDP multicast to 239.255.255.250:1900)
2. Devices respond with an HTTP-like reply containing a **LOCATION** URL
3. HA fetches that URL to get the device description XML (manufacturer, modelName, serialNumber, friendlyName)
4. HA uses the host from the LOCATION URL to connect (telnet port 23)

With `enable_ssdp: true`, the proxy emulates this by:

- Responding to M-SEARCH on UDP 1900 (requires root on Linux)
- Serving a minimal UPnP device description at `http://<proxy_ip>:8080/description.xml`

The advertised name is configurable via `ssdp_friendly_name`. Home Assistant will show the proxy as a discovered Denon AVR.

## Architecture

```
[Home Assistant] ─┐
[UC Remote 3]   ─┼─► [Denon Proxy] ──► [Physical Denon AVR]
[Other client]  ─┘       (single Telnet)
```

- **Proxy server** listens on TCP (default 2323)
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
- For **instant** power updates: Integration → Configure → enable **Use telnet**. The proxy broadcasts ZM/ZMSTANDBY so HA receives power changes via telnet.

### Port 23 requires root

- Use `proxy_port: 2323` (or another high port) and configure clients to use that port
- Or run the proxy with `sudo` for port 23

### Manual add fails instantly

- Set `log_level: "DEBUG"` in config and check logs for `HTTP: GET /goform/Deviceinfo.xml` — if you don't see it, Home Assistant isn't reaching the proxy
- Verify the HTTP server: `curl http://<proxy_ip>:8080/goform/Deviceinfo.xml` — should return XML
- Ports 80 and 60006: denonavr needs these for AVR-X 2016. The proxy binds 80, 8080, and 60006 when possible. Run with `sudo` if 80 or 60006 fail to bind
- Ensure `enable_ssdp: true` (the HTTP server only runs when SSDP is enabled)

### SSDP discovery not working

- UDP 1900 requires root on Linux: run with `sudo`
- Set `ssdp_advertise_ip` to your proxy's LAN IP if auto-detect fails
- Ensure port 8080 is free for the device description HTTP server (or set `ssdp_http_port`)
- Home Assistant expects telnet on port 23 — use `proxy_port: 23` for seamless discovery

## License

MIT
