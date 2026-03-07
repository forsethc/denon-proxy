# Development

This document covers project structure, architecture, testing, and releasing for contributors.

## Project Structure

- [**`denon_proxy.py`**](../denon_proxy.py) – Main proxy: Telnet multiplexer, client handling, AVR connection
- [**`http_server.py`**](../http_server.py) – Optional HTTP server: JSON API + SSE (status, commands) and Web UI wiring; only used when the HTTP interface is enabled
- [**`web_ui.html`**](../web_ui.html) – HTML dashboard for the browser UI, served only when the HTTP interface is enabled
- [**`avr_state.py`**](../avr_state.py) – Canonical Denon state model (`AVRState`) and volume presentation helpers; used by proxy, connection, and discovery
- [**`avr_connection.py`**](../avr_connection.py) – AVR I/O: physical Telnet connection or in-process virtual AVR (same interface for the proxy)
- [**`avr_discovery.py`**](../avr_discovery.py) – AVR discovery: HTTP/SSDP (device discovery, Deviceinfo, AppCommand, MainZone XML). Used by the proxy when SSDP is enabled; can also be used standalone for testing
- [**`runtime_utils.py`**](../runtime_utils.py) – Runtime/environment helpers: container detection (`is_running_in_docker`), internal IP classification (`is_docker_internal_ip`), version from git

## State and configuration

The proxy keeps configuration and runtime data in four distinct layers. Keeping them separate avoids mutating config at runtime and makes it clear where each value comes from.

| Class | Module | Role |
|-------|--------|------|
| **Config** | `config.py` | User- and environment-driven inputs (avr_host, proxy_port, sources override, etc.). Immutable after parse; read via mapping interface. |
| **AVRInfo** | `avr_info.py` | AVR identity and capabilities discovered at runtime (manufacturer, model, serial, friendly name, raw input sources). Frozen dataclass; set once at startup from HTTP sync (or `unknown`/`virtual` placeholders). |
| **RuntimeState** | `runtime_state.py` | Resolved, cached views derived from Config + AVRInfo: resolved sources, resolved friendly name, chosen ports (when config uses 0), and callbacks (e.g. notify Web UI). Single mutable instance passed through proxy and discovery. |
| **AVRState** | `avr_state.py` | Live AVR state (power, volume, input, mute, sound mode, smart select). Updated from Telnet responses; used by proxy, connection, and discovery for JSON/XML and optimistic updates. |

- **Config** → never mutated after load; overrides come from env vars at startup.
- **AVRInfo** → immutable value; the reference in RuntimeState is set once after discovery (or virtual/unknown).
- **RuntimeState** → holds that reference plus derived caches and callbacks.
- **AVRState** → the only frequently mutating state; it reflects the current device (or optimistic) state.

## Tests

The project is covered by unit, integration, and end-to-end tests.

```bash
pytest
```

See [tests/README.md](../tests/README.md) for details.

## Releasing

1. Go to **Actions → Release → Run workflow**
2. Enter the version (e.g. `1.0.0`)
3. The workflow will:
   - Write `v1.0.0` to the `VERSION` file
   - Commit and push to the current branch
   - Create tag `v1.0.0` pointing to that commit

The version is shown in the Web UI footer (linked to the tag on GitHub) and in logs at startup. Docker builds use the `VERSION` file; local runs use `git describe` when in a git repo.

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

## SSDP Discovery

Home Assistant discovers Denon AVRs via SSDP/UPnP:

1. Home Assistant sends **M-SEARCH** (UDP multicast to 239.255.255.250:1900)
2. Devices respond with an HTTP-like reply containing a **LOCATION** URL
3. Home Assistant fetches that URL to get the device description XML (manufacturer, modelName, serialNumber, friendlyName)
4. Home Assistant uses the host from the LOCATION URL to connect (telnet port 23)

With `enable_ssdp` true, the proxy emulates this by:

- Responding to M-SEARCH on UDP 1900 (requires root on Linux)
- Serving a minimal UPnP device description at `http://<proxy_ip>:8080/description.xml`

The advertised name is taken from `ssdp_friendly_name` when set; otherwise the proxy uses the physical AVR's friendly name + " Proxy" (once known from HTTP sync), or "Denon AVR Proxy" as a fallback. Home Assistant will show the proxy under that name when discovered.
