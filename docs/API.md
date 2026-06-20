# JSON API

When `enable_http` is `true`, the proxy starts an HTTP server on `http_port` (default 8081) that exposes a JSON API, an SSE stream, and the Web UI.

**Base URL:** `http://<proxy_ip>:<http_port>`

## Endpoints

- **GET `/`** – **Web UI dashboard**
  - Served when `web_ui.html` is present and `enable_http` is `true`.
  - Browser UI for status, commands, and SSE updates.

- **GET `/api/status`** – **Current status JSON**
  - Returns a JSON object with:
    - `friendly_name`: advertised name of the proxy
    - `avr`: AVR details (`type`, `host`, `port`, `connected`, `volume_max`, `sources`, and optional metadata)
    - `clients`: list of connected client IPs
    - `client_count`: number of connected clients
    - `state`: AVR state (power, volume, input_source, mute, sound_mode, smart_select, etc.)
    - `discovery`: SSDP/discovery info (enabled flag, proxy IP, `http_port`, Docker hints)
    - `version`: proxy version

- **POST `/api/command`** – **Send a Telnet command**
  - Body: JSON `{"command": "PWON"}` (any valid Denon Telnet command, without CRLF)
  - Response: `{"ok": true, "command": "PWON"}` on success, or `{"error": "..."}`

- **POST `/api/refresh`** – **Request a state refresh**
  - Triggers a fresh state request from the AVR (if supported by the current backend).
  - Response: `{"ok": true}` on success, or `{"error": "..."}`

- **GET `/events`** – **Server-Sent Events stream**
  - Long-lived SSE connection that pushes the same JSON payload as `/api/status` whenever state changes.
  - Each event is sent as: `data: <json>\n\n`

---

## AVR Control Endpoints

Feature-oriented GET (or POST) endpoints that map directly to Denon telnet commands. Each returns `{"ok": true, "command": "<telnet>"}` on success or `{"error": "..."}` with an appropriate HTTP status code on failure. Commands go through the same path as the existing `/api/command` endpoint (optimistic state, SSE broadcast).

### Power

| Endpoint | Telnet command | Notes |
|---|---|---|
| `GET /api/avr/power/on` | `PWON` | |
| `GET /api/avr/power/off` | `PWSTANDBY` | |
| `GET /api/avr/power/toggle` | `PWON` or `PWSTANDBY` | State-aware: reads current power state |

### Volume

| Endpoint | Telnet command | Notes |
|---|---|---|
| `GET /api/avr/volume/up` | `MVUP` | |
| `GET /api/avr/volume/down` | `MVDOWN` | |
| `GET /api/avr/volume/set?level=<n>` | `MV<n>` | `n` is 0–98 (or 0–`volume_max`); rounded to nearest 0.5 step |

Volume `set` example: `GET /api/avr/volume/set?level=45` sends `MV45`; `?level=45.5` sends `MV455`.

### Mute

| Endpoint | Telnet command | Notes |
|---|---|---|
| `GET /api/avr/mute/on` | `MUON` | |
| `GET /api/avr/mute/off` | `MUOFF` | |
| `GET /api/avr/mute/toggle` | `MUON` or `MUOFF` | State-aware: reads current mute state |

### Source

| Endpoint | Telnet command | Notes |
|---|---|---|
| `GET /api/avr/source/<CODE>` | `SI<CODE>` | `CODE` must be in the resolved source list; unknown codes return 404 |

Example: `GET /api/avr/source/HDMI1` sends `SIHDMI1`.

Available source codes are listed in `/api/status` (`avr.sources[].func`) and `/api/uc/capabilities`.

---

## UC Remote 3 Endpoints

These endpoints help configure the [ucr2-integration-requests](https://github.com/kennymc-c/ucr2-integration-requests) integration on Unfolded Circle Remote 3 with custom entities that talk to the proxy. See [UC-REMOTE.md](UC-REMOTE.md) for a setup walkthrough.

- **GET `/api/uc/capabilities`** – **JSON description of all AVR control endpoints**
  - Returns `base_url`, a `features` object (with endpoint URLs for power/volume/mute and source option list), and a flat `endpoints` list.
  - The `base_url` is derived from the HTTP request's `Host` header so it reflects whatever address you used to reach the proxy.

- **GET `/api/uc/custom_entities.yaml`** – **Ready-to-paste UC custom-entity YAML**
  - Returns `text/yaml`. The output is a complete `custom_entities.yaml` with a `_vars` block, `On`/`Off`/`Toggle` features, simple commands for volume and mute, one `SRC_*` command per resolved source, and a `Source` select entity.
  - Paste the output directly into the UC integration's custom-entity configuration field (or use the copy button in the Web UI).
  - The `denon_proxy_url` variable in the `_vars` block is set to the proxy's base URL from the `Host` header; edit it if you later move the proxy to a different address.
