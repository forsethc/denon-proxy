"""
Web UI for denon-proxy.

Serves a monitoring dashboard at GET / and a JSON API at GET /status, POST /state,
POST /api/command, POST /api/refresh.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional


# -----------------------------------------------------------------------------
# HTML Dashboard
# -----------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Denon AVR Proxy</title>
  <style>
    :root {
      --bg: #0f1419;
      --surface: #1a2332;
      --surface-hover: #243044;
      --border: #2d3a4d;
      --text: #e6edf3;
      --text-muted: #8b949e;
      --accent: #58a6ff;
      --success: #3fb950;
      --warning: #d29922;
      --error: #f85149;
    }
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 1.5rem;
      line-height: 1.5;
    }
    h1 { font-size: 1.5rem; margin: 0 0 1.5rem 0; }
    h2 { font-size: 1rem; margin: 0 0 0.5rem 0; color: var(--text-muted); font-weight: 600; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .grid { display: grid; gap: 1.5rem; }
    @media (min-width: 768px) { .grid { grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); } }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem 1.25rem;
    }
    .card h2 { margin-bottom: 0.75rem; }
    .status-row { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }
    .status-dot {
      width: 8px; height: 8px; border-radius: 50%;
      flex-shrink: 0;
    }
    .status-dot.connected { background: var(--success); }
    .status-dot.disconnected { background: var(--error); }
    .status-dot.virtual { background: var(--accent); }
    .client-list { font-family: ui-monospace, monospace; font-size: 0.875rem; }
    .client-list li { margin: 0.25rem 0; }
    ul { margin: 0; padding-left: 1.25rem; }
    .state-table { width: 100%; border-collapse: collapse; }
    .state-table td { padding: 0.35rem 0; border-bottom: 1px solid var(--border); }
    .state-table td:first-child { color: var(--text-muted); width: 40%; }
    .state-table tr:last-child td { border-bottom: none; }
    .btn {
      display: inline-block;
      padding: 0.4rem 0.75rem;
      margin: 0.25rem 0.25rem 0.25rem 0;
      background: var(--surface-hover);
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--text);
      cursor: pointer;
      font-size: 0.875rem;
    }
    .btn:hover { background: var(--border); }
    .btn-primary { background: var(--accent); border-color: var(--accent); color: var(--bg); }
    .btn-primary:hover { filter: brightness(1.1); }
    .btn-group { display: flex; flex-wrap: wrap; gap: 0.25rem; margin-top: 0.5rem; }
    .cmd-input { display: flex; gap: 0.5rem; margin-top: 0.5rem; }
    .cmd-input input {
      flex: 1;
      padding: 0.4rem 0.75rem;
      background: var(--surface-hover);
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--text);
      font-family: ui-monospace, monospace;
    }
    .muted { color: var(--text-muted); font-size: 0.875rem; }
    .last-update { margin-top: 1rem; font-size: 0.75rem; color: var(--text-muted); }
    .json-block {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0.75rem;
      font-family: ui-monospace, monospace;
      font-size: 0.75rem;
      max-height: 200px;
      overflow: auto;
    }
  </style>
</head>
<body>
  <h1>Denon AVR Proxy</h1>
  <div class="grid">
    <div class="card">
      <h2>AVR Connection</h2>
      <div id="avr-status">Loading...</div>
      <div id="avr-details" class="muted"></div>
    </div>
    <div class="card">
      <h2>Connected Clients</h2>
      <div id="clients">Loading...</div>
    </div>
    <div class="card">
      <h2>AVR State</h2>
      <table class="state-table" id="state-table"></table>
      <div class="btn-group">
        <button class="btn btn-primary" onclick="refreshState()">Refresh from AVR</button>
      </div>
    </div>
    <div class="card">
      <h2>Send Commands</h2>
      <div class="btn-group">
        <button class="btn" onclick="sendCmd('PWON')">Power On</button>
        <button class="btn" onclick="sendCmd('PWSTANDBY')">Power Off</button>
        <button class="btn" onclick="sendCmd('MUON')">Mute On</button>
        <button class="btn" onclick="sendCmd('MUOFF')">Mute Off</button>
        <button class="btn" onclick="sendCmd('MVUP')">Vol Up</button>
        <button class="btn" onclick="sendCmd('MVDOWN')">Vol Down</button>
      </div>
      <div class="muted" style="margin-top: 0.5rem;">Input:</div>
      <div class="btn-group">
        <button class="btn" onclick="sendCmd('SIHDMI1')">HDMI1</button>
        <button class="btn" onclick="sendCmd('SIHDMI2')">HDMI2</button>
        <button class="btn" onclick="sendCmd('SINET')">NET</button>
        <button class="btn" onclick="sendCmd('SIBD')">BD</button>
        <button class="btn" onclick="sendCmd('SICD')">CD</button>
      </div>
      <div class="cmd-input">
        <input type="text" id="custom-cmd" placeholder="e.g. SIHDMI1" onkeydown="if(event.key==='Enter')sendCustomCmd()">
        <button class="btn" onclick="sendCustomCmd()">Send</button>
      </div>
    </div>
    <div class="card">
      <h2>Internal State (JSON)</h2>
      <pre class="json-block" id="json-state">{}</pre>
      <div class="last-update">Auto-refresh: <span id="countdown">30</span>s</div>
    </div>
  </div>
  <script>
    let pollInterval;
    function api(path, opts) {
      return fetch(path, { ...opts, headers: { 'Content-Type': 'application/json', ...opts?.headers } });
    }
    async function load() {
      try {
        const r = await api('/status');
        const d = await r.json();
        render(d);
      } catch (e) {
        document.getElementById('avr-status').innerHTML = '<span class="muted">Failed to load</span>';
      }
    }
    function render(d) {
      const avr = d.avr || {};
      const clients = d.clients || [];
      const state = d.state || {};
      const avrType = avr.type || 'none';
      const connected = avrType === 'physical' && avr.connected;
      const statusEl = document.getElementById('avr-status');
      statusEl.innerHTML = '<span class="status-dot ' + (avrType === 'virtual' ? 'virtual' : (connected ? 'connected' : 'disconnected')) + '"></span> ' +
        (avrType === 'physical' ? (connected ? 'Connected to ' + (avr.host || '?') + ':' + (avr.port || 23) : 'Disconnected') :
         avrType === 'virtual' ? 'Virtual AVR (demo mode)' : 'No AVR configured');
      let details = [];
      if (avr.manufacturer) details.push(avr.manufacturer);
      if (avr.model_name) details.push(avr.model_name);
      if (avr.friendly_name) details.push(avr.friendly_name);
      document.getElementById('avr-details').textContent = details.length ? details.join(' · ') : '';
      document.getElementById('clients').innerHTML = clients.length
        ? '<ul class="client-list">' + clients.map(c => '<li>' + escapeHtml(c) + '</li>').join('') + '</ul>'
        : '<span class="muted">No clients connected</span>';
      const rows = [
        ['Power', state.power || '—'],
        ['Volume', state.volume != null ? state.volume + (typeof state.volume === 'number' ? ' (0–98)' : '') : '—'],
        ['Input', state.input_source || '—'],
        ['Mute', state.mute != null ? (state.mute ? 'On' : 'Off') : '—'],
        ['Sound mode', state.sound_mode || '—']
      ];
      document.getElementById('state-table').innerHTML = rows.map(([k,v]) => '<tr><td>' + escapeHtml(k) + '</td><td>' + escapeHtml(String(v)) + '</td></tr>').join('');
      document.getElementById('json-state').textContent = JSON.stringify(d, null, 2);
    }
    function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
    async function sendCmd(cmd) { await api('/api/command', { method: 'POST', body: JSON.stringify({ command: cmd }) }); load(); }
    function sendCustomCmd() { const v = document.getElementById('custom-cmd').value.trim(); if (v) { sendCmd(v); document.getElementById('custom-cmd').value = ''; } }
    async function refreshState() { await api('/api/refresh', { method: 'POST' }); load(); }
    let cd = 30;
    function tick() { cd--; document.getElementById('countdown').textContent = cd; if (cd <= 0) { cd = 30; load(); } }
    load();
    pollInterval = setInterval(tick, 1000);
  </script>
</body>
</html>
"""


class WebUIHandler(asyncio.Protocol):
    """HTTP handler: HTML dashboard at GET /, JSON API at /status, /api/command, /api/refresh."""

    def __init__(
        self,
        get_state: Callable[[], dict[str, Any]],
        logger: logging.Logger,
        set_state: Optional[Callable[[dict[str, Any]], None]] = None,
        send_command: Optional[Callable[[str], None]] = None,
        request_state: Optional[Callable[[], None]] = None,
    ) -> None:
        self.get_state = get_state
        self.set_state = set_state
        self.send_command = send_command
        self.request_state = request_state
        self.logger = logger
        self.transport: Optional[asyncio.BaseTransport] = None
        self._buffer = b""

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def data_received(self, data: bytes) -> None:
        self._buffer += data
        if b"\r\n\r\n" not in self._buffer:
            return
        lines = self._buffer.decode("utf-8", errors="ignore").split("\r\n")
        req_line = lines[0] if lines else ""
        parts = req_line.split()
        method = parts[0].upper() if len(parts) >= 1 else ""
        path = parts[1].split("?")[0] if len(parts) >= 2 else "/"

        if method == "GET" and path == "/":
            self._handle_dashboard()
        elif method == "GET" and path in ("/status", "/api/status"):
            self._handle_get_status()
        elif method == "POST" and path == "/state":
            self._handle_post_state()
        elif method == "POST" and path == "/api/command":
            self._handle_post_command()
        elif method == "POST" and path == "/api/refresh":
            self._handle_post_refresh()
        else:
            self._send_json(404, {"error": "Not Found"})

    def _handle_dashboard(self) -> None:
        self._send_html(200, DASHBOARD_HTML)

    def _handle_get_status(self) -> None:
        try:
            body = json.dumps(self.get_state(), indent=2).encode("utf-8")
        except Exception as e:
            self.logger.warning("get_state error: %s", e)
            self._send_json(500, {"error": "Internal Server Error"})
            return
        self._send_body(200, body, content_type="application/json")

    def _handle_post_state(self) -> None:
        if not self.set_state:
            self._send_json(501, {"error": "set_state not configured (only available in virtual AVR mode)"})
            return
        headers_end = self._buffer.index(b"\r\n\r\n")
        body_bytes = self._buffer[headers_end + 4:]
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_json(400, {"error": f"Invalid JSON: {e}"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "Body must be a JSON object"})
            return
        try:
            self.set_state(payload)
            self._send_json(200, {"ok": True, "state": self.get_state().get("state", {})})
        except Exception as e:
            self.logger.warning("set_state error: %s", e)
            self._send_json(500, {"error": str(e)})

    def _handle_post_command(self) -> None:
        if not self.send_command:
            self._send_json(501, {"error": "send_command not configured"})
            return
        headers_end = self._buffer.index(b"\r\n\r\n")
        body_bytes = self._buffer[headers_end + 4:]
        try:
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes.strip() else {}
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_json(400, {"error": f"Invalid JSON: {e}"})
            return
        cmd = payload.get("command") if isinstance(payload, dict) else None
        if not cmd or not isinstance(cmd, str):
            self._send_json(400, {"error": "Body must be JSON object with 'command' string"})
            return
        cmd = cmd.strip()
        if len(cmd) < 2:
            self._send_json(400, {"error": "Command too short"})
            return
        try:
            self.send_command(cmd)
            self._send_json(200, {"ok": True, "command": cmd})
        except Exception as e:
            self.logger.warning("send_command error: %s", e)
            self._send_json(500, {"error": str(e)})

    def _handle_post_refresh(self) -> None:
        if not self.request_state:
            self._send_json(501, {"error": "request_state not configured"})
            return
        try:
            self.request_state()
            self._send_json(200, {"ok": True})
        except Exception as e:
            self.logger.warning("request_state error: %s", e)
            self._send_json(500, {"error": str(e)})

    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self._send_body(status, body, content_type="application/json")

    def _send_html(self, status: int, html_content: str) -> None:
        body = html_content.encode("utf-8")
        self._send_body(status, body, content_type="text/html; charset=utf-8")

    def _send_body(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        reasons = {
            200: "OK", 400: "Bad Request", 404: "Not Found",
            405: "Method Not Allowed", 500: "Internal Server Error", 501: "Not Implemented",
        }
        reason = reasons.get(status, "Error")
        resp = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode() + body
        if self.transport:
            self.transport.write(resp)
        self._close()

    def _close(self) -> None:
        if self.transport:
            self.transport.close()
            self.transport = None


async def run_json_api(
    config: dict,
    logger: logging.Logger,
    get_state: Callable[[], dict[str, Any]],
    set_state: Optional[Callable[[dict[str, Any]], None]] = None,
    send_command: Optional[Callable[[str], None]] = None,
    request_state: Optional[Callable[[], None]] = None,
) -> Optional[asyncio.Server]:
    """
    Start the Web UI server.

    GET / - HTML dashboard (monitoring, state, commands)
    GET /status - JSON status (avr, clients, state)
    POST /state - Set virtual AVR state (virtual mode only)
    POST /api/command - Send telnet command to AVR (JSON body: {"command": "PWON"})
    POST /api/refresh - Request current state from AVR

    Returns the server if started, None if disabled or failed.
    """
    if not config.get("enable_web_ui"):
        return None
    port = int(config.get("web_ui_port", 8081))
    try:
        def factory():
            return WebUIHandler(
                get_state, logger,
                set_state=set_state,
                send_command=send_command,
                request_state=request_state,
            )
        server = await asyncio.get_running_loop().create_server(
            factory, "0.0.0.0", port, reuse_address=True,
        )
        return server
    except OSError as e:
        logger.warning("Web UI port %d unavailable: %s", port, e)
        return None
