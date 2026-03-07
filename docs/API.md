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
