"""
Web UI for denon-proxy.

Serves a monitoring dashboard at GET / and a JSON API at GET /status, POST /state,
POST /api/command, POST /api/refresh. GET /events streams state updates via SSE.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional, Set


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
    .btn:not(.btn-primary):not(.btn-danger):hover { background: var(--border); }
    .btn-primary { background: var(--accent); border-color: var(--accent); color: var(--bg); }
    .btn-primary:hover { filter: brightness(1.1); }
    .btn-danger { background: var(--error); border-color: var(--error); color: var(--bg); }
    .btn-danger:hover { background: #da3633; border-color: #da3633; }
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
    .xml-link-pills { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.35rem; }
    .xml-link-pills a {
      display: inline-block;
      padding: 0.25rem 0.6rem;
      border-radius: 4px;
      font-size: 0.8rem;
      font-weight: 500;
      text-decoration: none;
    }
    .xml-link-pills a.proxy-link {
      background: var(--accent);
      color: var(--bg);
      border: 1px solid var(--accent);
    }
    .xml-link-pills a.proxy-link:hover { filter: brightness(1.1); }
    .xml-link-pills a.physical-link {
      background: var(--surface-hover);
      color: var(--text);
      border: 1px solid var(--border);
    }
    .xml-link-pills a.physical-link:hover { background: var(--border); }
    .proxy-ip-note {
      display: block;
      margin-top: 0.35rem;
      margin-bottom: 1.25rem;
      font-size: 0.95rem;
      color: var(--warning);
    }
    .proxy-ip-note code {
      font-family: ui-monospace, "Cascadia Code", "Source Code Pro", Menlo, monospace;
      font-size: 0.9em;
      background: var(--surface-hover);
      padding: 0.15em 0.4em;
      border-radius: 4px;
      border: 1px solid var(--border);
    }
    .proxy-ip-note .suggest-line { margin-top: 0.25rem; }
  </style>
</head>
<body>
  <div style="display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem;">
    <h1 style="margin: 0;" id="page-title">Denon AVR Proxy</h1>
    <span id="header-proxy-ip" class="muted"></span>
  </div>
  <div id="proxy-ip-note" class="proxy-ip-note" style="display: none;"><span id="proxy-ip-note-msg"></span><div id="proxy-ip-note-suggest" class="suggest-line"></div></div>
  <div class="grid">
    <div class="card">
      <h2>AVR Connection</h2>
      <div class="status-row" id="avr-connection-status">
        <span class="status-dot disconnected" id="avr-status-dot"></span>
        <span id="avr-status-text" class="muted">Loading...</span>
      </div>
      <table class="state-table" id="avr-details"></table>
      <div class="btn-group" id="avr-reconnect-row" style="display: none;">
        <button class="btn btn-danger" onclick="reconnectAvr(this)">Reconnect</button>
      </div>
    </div>
    <div class="card">
      <h2>AVR State</h2>
      <table class="state-table" id="state-table"></table>
      <div class="btn-group">
        <button class="btn btn-primary" onclick="refreshState()">Refresh from AVR</button>
      </div>
    </div>
    <div class="card">
      <h2>Connected Clients</h2>
      <div id="clients">Loading...</div>
      <p id="docker-clients-note" class="muted" style="margin-top: 0.5rem; font-size: 0.8rem; display: none;">When running in Docker, client IPs may all show as the Docker gateway (e.g. 192.168.65.1). Each connection is still separate; only the displayed address is affected.</p>
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
      <div class="btn-group" id="input-buttons"></div>
      <div class="cmd-input">
        <input type="text" id="custom-cmd" placeholder="e.g. SIHDMI1" onkeydown="if(event.key==='Enter')sendCustomCmd()">
        <button class="btn" onclick="sendCustomCmd()">Send</button>
      </div>
    </div>
    <div class="card">
      <h2>Internal State (JSON)</h2>
      <pre class="json-block" id="json-state">{}</pre>
      <div class="last-update"><span id="update-status">Connecting...</span></div>
    </div>
    <div class="card" id="xml-card" style="display: none;">
      <h2>XML Endpoints</h2>
      <ul class="client-list" id="xml-links"></ul>
    </div>
  </div>
  <script>
    // Global UI state for reconnect button
    window._avrReconnectInProgress = false;
    function api(path, opts) {
      return fetch(path, { ...opts, headers: { 'Content-Type': 'application/json', ...opts?.headers } });
    }
    function loadFromData(d) {
      try { render(d); } catch (e) { document.getElementById('avr-details').innerHTML = '<tr><td colspan="2" class="muted">Render error</td></tr>'; document.getElementById('avr-details').style.display = ''; }
    }
    function render(d) {
      const avr = d.avr || {};
      const clients = d.clients || [];
      const state = d.state || {};
      const avrType = avr.type || 'none';
      const connected = !!avr.connected;

      // AVR connection status pill
      (function updateConnectionStatus() {
        const dot = document.getElementById('avr-status-dot');
        const textEl = document.getElementById('avr-status-text');
        if (!dot || !textEl) return;
        let cls = 'status-dot disconnected';
        let text = 'No AVR configured';
        if (avrType === 'virtual') {
          cls = 'status-dot virtual';
          text = 'Virtual AVR (demo mode)';
        } else if (avrType === 'physical') {
          if (connected) {
            cls = 'status-dot connected';
            text = 'Connected to physical AVR';
          } else {
            cls = 'status-dot disconnected';
            text = 'Disconnected from physical AVR';
          }
        }
        dot.className = cls;
        textEl.textContent = text;
      })();
      // Reconnect button only makes sense for a physical AVR.
      // For virtual or no AVR, always hide/reset it.
      if (avrType === 'physical') {
        updateReconnectButtonState(connected);
      } else {
        updateReconnectButtonState(true);
      }
      const avrDetailsEl = document.getElementById('avr-details');
      if (avrType === 'virtual') {
        avrDetailsEl.style.display = 'none';
      } else {
        avrDetailsEl.style.display = '';
        const detailRows = [
          ['Host', avrType === 'physical' ? avr.host : null],
          ['Port', avrType === 'physical' ? avr.port : null],
          ['Brand', avr.manufacturer],
          ['Model', avr.model_name],
          ['Friendly Name', avr.friendly_name],
          ['Serial', avr.serial_number]
        ];
        avrDetailsEl.innerHTML = detailRows
          .map(([label, val]) => {
            const text = val != null && val !== '' ? String(val) : '—';
            const content = (label === 'Host' && val)
              ? '<a href="http://' + escapeHtml(val) + '" target="_blank" rel="noopener">' + escapeHtml(text) + '</a>'
              : escapeHtml(text);
            return '<tr><td>' + escapeHtml(label) + '</td><td>' + content + '</td></tr>';
          })
          .join('');
      }
      const title = d.friendly_name || 'Denon AVR Proxy';
      document.getElementById('page-title').textContent = title;
      document.title = title;
      const headerProxyIp = document.getElementById('header-proxy-ip');
      headerProxyIp.textContent = (d.discovery && d.discovery.proxy_ip) ? ('Proxy IP: ' + d.discovery.proxy_ip) : '';
      const proxyIpNote = document.getElementById('proxy-ip-note');
      const proxyIpNoteMsg = document.getElementById('proxy-ip-note-msg');
      const proxyIpNoteSuggest = document.getElementById('proxy-ip-note-suggest');
      if (proxyIpNote && proxyIpNoteMsg && proxyIpNoteSuggest) {
        if (d.discovery && d.discovery.proxy_ip_is_internal) {
          proxyIpNoteMsg.innerHTML = "The advertised proxy IP (shown above) looks like a Docker/internal address. Set <code>ssdp_advertise_ip</code> in config to your host's LAN IP so clients (e.g. Home Assistant) can reach the proxy.";
          const hostname = window.location.hostname;
          const isLocal = hostname === '127.0.0.1' || hostname.toLowerCase() === 'localhost';
          proxyIpNoteSuggest.innerHTML = isLocal ? '' : "Suggested: <code>" + escapeHtml(hostname) + "</code> (this page's address).";
          proxyIpNote.style.display = 'block';
        } else {
          proxyIpNoteMsg.textContent = '';
          proxyIpNoteSuggest.innerHTML = '';
          proxyIpNote.style.display = 'none';
        }
      }
      document.getElementById('clients').innerHTML = clients.length
        ? '<ul class="client-list">' + clients.map(c => '<li>' + escapeHtml(c) + '</li>').join('') + '</ul>'
        : '<span class="muted">No clients connected</span>';
      const dockerClientsNote = document.getElementById('docker-clients-note');
      if (dockerClientsNote) dockerClientsNote.style.display = (d.discovery && d.discovery.is_docker) ? 'block' : 'none';
      const src = state.input_source;
      let inputLabel = src || '—';
      if (src && avr.sources) {
        const s = avr.sources.find(x => x.func === src);
        inputLabel = s ? s.display_name + ' (' + s.func + ')' : src;
      }
      const rows = [
        ['Power', state.power || '—'],
        ['Volume', state.volume != null ? state.volume + (typeof state.volume === 'number' ? ' (0–' + (avr.volume_max != null ? avr.volume_max : 98) + ')' : '') : '—'],
        ['Input', inputLabel],
        ['Mute', state.mute != null ? (state.mute ? 'On' : 'Off') : '—'],
        ['Sound mode', state.sound_mode || '—'],
        ['Smart Select', state.smart_select || '—']
      ];
      document.getElementById('state-table').innerHTML = rows.map(([k,v]) => '<tr><td>' + escapeHtml(k) + '</td><td>' + escapeHtml(String(v)) + '</td></tr>').join('');
      // Dynamic input buttons: only show sources we actually expose via avr.sources.
      const inputButtonsEl = document.getElementById('input-buttons');
      if (inputButtonsEl) {
        const sources = Array.isArray(avr.sources) ? avr.sources.filter(s => s && s.func) : [];
        if (sources.length) {
          inputButtonsEl.innerHTML = sources
            .map(s => {
              const label = s.display_name || s.func;
              const cmd = 'SI' + s.func;
              // Use a data attribute and a delegated click handler to avoid complex quoting.
              return '<button class="btn" data-cmd="' + escapeHtml(cmd) + '">' + escapeHtml(label) + '</button>';
            })
            .join('');
          if (!inputButtonsEl._handlerAttached) {
            inputButtonsEl.addEventListener('click', (event) => {
              const target = event.target.closest('button[data-cmd]');
              if (!target) return;
              const cmd = target.getAttribute('data-cmd');
              if (cmd) {
                sendCmd(cmd);
              }
            });
            inputButtonsEl._handlerAttached = true;
          }
        } else {
          inputButtonsEl.innerHTML = '<span class="muted">No known inputs for this AVR</span>';
        }
      }
      const discovery = d.discovery;
      const xmlCard = document.getElementById('xml-card');
      const xmlList = document.getElementById('xml-links');
      const xmlEndpoints = [
        ['description.xml', '/description.xml', 'UPnP device description for SSDP discovery'],
        ['goform/Deviceinfo.xml', '/goform/Deviceinfo.xml', 'Device info and input sources (e.g. for Home Assistant setup)'],
        ['MainZone XML status', '/goform/formMainZone_MainZoneXmlStatus.xml', 'Current power, volume, input, mute (status polling)']
      ];
      if (discovery && discovery.http_port) {
        const proxyBase = window.location.protocol + '//' + window.location.hostname + ':' + discovery.http_port;
        const hasPhysical = avrType === 'physical' && avr.host;
        xmlList.innerHTML = xmlEndpoints.map(([label, path, desc]) => {
          const proxyLink = '<a class="proxy-link" href="' + escapeHtml(proxyBase + path) + '" target="_blank" rel="noopener">Proxy</a>';
          const physicalPort = path.indexOf('/goform/') !== -1 ? 80 : 8080;
          const physicalBase = hasPhysical ? ('http://' + avr.host + ':' + physicalPort) : '';
          const physicalLink = hasPhysical ? '<a class="physical-link" href="' + escapeHtml(physicalBase + path) + '" target="_blank" rel="noopener">Physical</a>' : '';
          const pills = '<span class="xml-link-pills">' + proxyLink + (hasPhysical ? physicalLink : '') + '</span>';
          return '<li>' + escapeHtml(label) + '<br><span class="muted">' + escapeHtml(desc) + '</span><br>' + pills + '</li>';
        }).join('');
        xmlCard.style.display = 'block';
      } else {
        xmlCard.style.display = 'none';
      }
      document.getElementById('json-state').textContent = JSON.stringify(d, null, 2);
    }
    function updateReconnectButtonState(connected) {
      const reconnectRow = document.getElementById('avr-reconnect-row');
      const reconnectBtn = reconnectRow ? reconnectRow.querySelector('button') : null;
      if (!reconnectRow || !reconnectBtn) return;
      const shouldShowReconnect = !connected;
      reconnectRow.style.display = shouldShowReconnect ? '' : 'none';
      if (!shouldShowReconnect) {
        // Clear any in-progress state when we hide the row (connected again or no physical AVR)
        window._avrReconnectInProgress = false;
        reconnectBtn.disabled = false;
        reconnectBtn.textContent = 'Reconnect';
      } else if (window._avrReconnectInProgress) {
        reconnectBtn.disabled = true;
        reconnectBtn.textContent = 'Reconnecting...';
      } else {
        reconnectBtn.disabled = false;
        reconnectBtn.textContent = 'Reconnect';
      }
    }
    function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
    async function sendCmd(cmd) { await api('/api/command', { method: 'POST', body: JSON.stringify({ command: cmd }) }); }
    async function reconnectAvr(btn) {
      if (!btn || btn.disabled) return;
      btn.disabled = true;
      btn.textContent = 'Reconnecting...';
      window._avrReconnectInProgress = true;
      const timeoutMs = 5000;
      try {
        await sendCmd('PW?');
      } catch (e) {
        // ignore; UI will be driven by SSE / JSON state
      }
      // If SSE / JSON never tells us we reconnected (row stays visible), fall back after timeout.
      setTimeout(() => {
        if (!window._avrReconnectInProgress) return;
        window._avrReconnectInProgress = false;
        // Use the same logic as render() to reset button state.
        // In the timeout/failure case we're still disconnected.
        updateReconnectButtonState(false);
      }, timeoutMs);
    }
    function sendCustomCmd() { const v = document.getElementById('custom-cmd').value.trim(); if (v) { sendCmd(v); document.getElementById('custom-cmd').value = ''; } }
    async function refreshState() { await api('/api/refresh', { method: 'POST' }); }
    const es = new EventSource('/events');
    es.onopen = () => { document.getElementById('update-status').textContent = 'Live (SSE)'; };
    es.onerror = () => { document.getElementById('update-status').textContent = 'Reconnecting...'; };
    es.onmessage = (e) => { loadFromData(JSON.parse(e.data)); };
    fetch('/status').then(r => r.json()).then(loadFromData).catch(() => { document.getElementById('update-status').textContent = 'Initial load failed'; });
  </script>
</body>
</html>
"""


def parse_http_request(buffer: bytes) -> Optional[tuple[str, str, bytes, bytes]]:
    """
    Parse an HTTP/1.1 request from a byte buffer.

    Returns (method, path, header_bytes, body_bytes), or None if the request
    is incomplete (no header terminator yet). This is pure and easy to unit-test.
    """
    if b"\r\n\r\n" not in buffer:
        return None
    headers_end = buffer.index(b"\r\n\r\n")
    header_bytes = buffer[:headers_end]
    body_bytes = buffer[headers_end + 4 :]
    lines = header_bytes.decode("utf-8", errors="ignore").split("\r\n")
    req_line = lines[0] if lines else ""
    parts = req_line.split()
    method = parts[0].upper() if len(parts) >= 1 else ""
    path = parts[1].split("?")[0] if len(parts) >= 2 else "/"
    return method, path, header_bytes, body_bytes


class WebUIHandler(asyncio.Protocol):
    """HTTP handler: HTML dashboard at GET /, JSON API, and SSE at GET /events."""

    def __init__(
        self,
        get_state: Callable[[], dict[str, Any]],
        logger: logging.Logger,
        sse_subscribers: Set[Any],
        set_state: Optional[Callable[[dict[str, Any]], None]] = None,
        send_command: Optional[Callable[[str], None]] = None,
        request_state: Optional[Callable[[], None]] = None,
    ) -> None:
        self.get_state = get_state
        self.set_state = set_state
        self.send_command = send_command
        self.request_state = request_state
        self.sse_subscribers = sse_subscribers
        self.on_sse_push = lambda: None  # Set by run_web_ui
        self.logger = logger
        self.transport: Optional[asyncio.BaseTransport] = None
        self._buffer = b""
        self._sse_mode = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def connection_lost(self, exc: Optional[BaseException]) -> None:
        if self._sse_mode and self.transport:
            self.sse_subscribers.discard(self.transport)
        self.transport = None

    def data_received(self, data: bytes) -> None:
        self._buffer += data
        parsed = parse_http_request(self._buffer)
        if not parsed:
            return
        method, path, _headers, body_bytes = parsed

        if method == "GET" and path == "/":
            self._handle_dashboard()
        elif method == "GET" and path == "/events":
            self._handle_sse()
        elif method == "GET" and path in ("/status", "/api/status"):
            self._handle_get_status()
        elif method == "POST" and path == "/state":
            self._handle_post_state(body_bytes)
        elif method == "POST" and path == "/api/command":
            self._handle_post_command(body_bytes)
        elif method == "POST" and path == "/api/refresh":
            self._handle_post_refresh()
        else:
            self._send_json(404, {"error": "Not Found"})

    def _handle_dashboard(self) -> None:
        self._send_html(200, DASHBOARD_HTML)

    def _handle_sse(self) -> None:
        """Serve Server-Sent Events stream - push state on changes."""
        self._sse_mode = True
        headers = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Cache-Control: no-cache\r\n"
            b"Connection: keep-alive\r\n\r\n"
        )
        if self.transport:
            self.transport.write(headers)
            self.sse_subscribers.add(self.transport)
            self.on_sse_push()

    def _handle_get_status(self) -> None:
        try:
            body = json.dumps(self.get_state(), indent=2).encode("utf-8")
        except Exception as e:
            self.logger.warning("get_state error: %s", e)
            self._send_json(500, {"error": "Internal Server Error"})
            return
        self._send_body(200, body, content_type="application/json")

    def _handle_post_state(self, body_bytes: bytes) -> None:
        if not self.set_state:
            self._send_json(501, {"error": "set_state not configured (only available in virtual AVR mode)"})
            return
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

    def _handle_post_command(self, body_bytes: bytes) -> None:
        if not self.send_command:
            self._send_json(501, {"error": "send_command not configured"})
            return
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


async def run_web_ui(
    config: dict,
    logger: logging.Logger,
    get_state: Callable[[], dict[str, Any]],
    set_state: Optional[Callable[[dict[str, Any]], None]] = None,
    send_command: Optional[Callable[[str], None]] = None,
    request_state: Optional[Callable[[], None]] = None,
) -> Optional[tuple[asyncio.Server, Callable[[], None]]]:
    """
    Start the Web UI server.

    GET / - HTML dashboard (monitoring, state, commands)
    GET /events - Server-Sent Events stream of state updates
    GET /status - JSON status (avr, clients, state)
    POST /state - Set virtual AVR state (virtual mode only)
    POST /api/command - Send telnet command to AVR (JSON body: {"command": "PWON"})
    POST /api/refresh - Request current state from AVR

    Returns (server, notify_state_changed) if started, None if disabled or failed.
    Call notify_state_changed() when state changes to push to SSE clients.
    """
    if not config.get("enable_web_ui"):
        return None
    port = int(config.get("web_ui_port", 8081))
    sse_subscribers: Set[Any] = set()

    async def _push() -> None:
        try:
            state = get_state()
            msg = ("data: " + json.dumps(state) + "\n\n").encode("utf-8")
            for t in list(sse_subscribers):
                try:
                    t.write(msg)
                except Exception:
                    sse_subscribers.discard(t)
        except Exception as e:
            logger.debug("SSE push error: %s", e)

    def notify_state_changed() -> None:
        try:
            asyncio.get_running_loop().create_task(_push())
        except RuntimeError:
            pass

    try:
        def factory():
            h = WebUIHandler(
                get_state, logger, sse_subscribers,
                set_state=set_state,
                send_command=send_command,
                request_state=request_state,
            )
            h.on_sse_push = notify_state_changed
            return h
        server = await asyncio.get_running_loop().create_server(
            factory, "0.0.0.0", port, reuse_address=True,
        )
        return (server, notify_state_changed)
    except OSError as e:
        logger.warning("Web UI port %d unavailable: %s", port, e)
        return None
