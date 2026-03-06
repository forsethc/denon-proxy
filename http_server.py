"""
HTTP server for denon-proxy: JSON API, SSE, and optional HTML dashboard.

This module serves:
- GET /api/status  -> JSON status (avr, clients, state, discovery)
- POST /api/command -> send telnet command to AVR
- POST /api/refresh -> request state refresh from AVR
- GET /events       -> Server-Sent Events (JSON state stream)

When configured with a dashboard_html string, it also serves:
- GET /            -> HTML dashboard (Web UI)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Set


def parse_http_request(buffer: bytes) -> tuple[str, str, bytes, bytes] | None:
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


def parse_command_request(body_bytes: bytes) -> tuple[str | None, dict | None]:
    """
    Parse and validate POST /api/command body.

    Returns (command, None) on success, or (None, error_dict) on validation failure.
    error_dict is suitable for 400 JSON response, e.g. {"error": "..."}.
    """
    try:
        payload = json.loads(body_bytes.decode("utf-8")) if body_bytes.strip() else {}
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, {"error": f"Invalid JSON: {e}"}
    cmd = payload.get("command") if isinstance(payload, dict) else None
    if not cmd or not isinstance(cmd, str):
        return None, {"error": "Body must be JSON object with 'command' string"}
    cmd = cmd.strip()
    if len(cmd) < 2:
        return None, {"error": "Command too short"}
    return cmd, None


class HttpServerHandler(asyncio.Protocol):
    """HTTP handler: JSON API and SSE; optional HTML dashboard at GET /."""

    def __init__(
        self,
        get_state: Callable[[], dict[str, Any]],
        logger: logging.Logger,
        sse_subscribers: Set[asyncio.WriteTransport],
        send_command: Callable[[str], None] | None = None,
        request_state: Callable[[], None] | None = None,
        dashboard_html: str | None = None,
    ) -> None:
        self.get_state = get_state
        self.send_command = send_command
        self.request_state = request_state
        self.sse_subscribers = sse_subscribers
        self.on_sse_push: Callable[[], None] = lambda: None
        self.logger = logger
        self.transport: asyncio.BaseTransport | None = None
        self._buffer = b""
        self._sse_mode = False
        self._dashboard_html = dashboard_html

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def connection_lost(self, exc: BaseException | None) -> None:
        if self._sse_mode and self.transport:
            self.sse_subscribers.discard(self.transport)
        self.transport = None

    def data_received(self, data: bytes) -> None:
        self._buffer += data
        parsed = parse_http_request(self._buffer)
        if not parsed:
            return
        method, path, _headers, body_bytes = parsed
        self._buffer = b""  # Clear so next request on same connection starts clean (e.g. keep-alive)

        if method == "GET" and path == "/":
            self._handle_root()
        elif method == "GET" and path == "/events":
            self._handle_sse()
        elif method == "GET" and path == "/api/status":
            self._handle_get_status()
        elif method == "POST" and path == "/api/command":
            self._handle_post_command(body_bytes)
        elif method == "POST" and path == "/api/refresh":
            self._handle_post_refresh()
        else:
            self._send_json(404, {"error": "Not Found"})

    # ------------------------------------------------------------------
    # Route handlers
    # ------------------------------------------------------------------

    def _handle_root(self) -> None:
        """Serve the HTML dashboard when configured, else 404."""
        if self._dashboard_html is None:
            self._send_json(404, {"error": "Not Found"})
        else:
            self._send_html(200, self._dashboard_html)

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

    def _handle_post_command(self, body_bytes: bytes) -> None:
        if not self.send_command:
            self._send_json(501, {"error": "send_command not configured"})
            return
        command, error = parse_command_request(body_bytes)
        if error is not None:
            self._send_json(400, error)
            return
        try:
            self.send_command(command)
            self._send_json(200, {"ok": True, "command": command})
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

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self._send_body(status, body, content_type="application/json")

    def _send_html(self, status: int, html_content: str) -> None:
        body = html_content.encode("utf-8")
        self._send_body(status, body, content_type="text/html; charset=utf-8")

    def _send_body(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        reasons = {
            200: "OK",
            400: "Bad Request",
            404: "Not Found",
            405: "Method Not Allowed",
            500: "Internal Server Error",
            501: "Not Implemented",
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


async def run_http_server(
    config: dict,
    logger: logging.Logger,
    get_state: Callable[[], dict[str, Any]],
    send_command: Callable[[str], None] | None = None,
    request_state: Callable[[], None] | None = None,
    *,
    dashboard_html: str | None = None,
) -> tuple[asyncio.Server, Callable[[], None]] | None:
    """
    Start the HTTP server (JSON API, SSE, and optional Web UI).

    Returns (server, notify_state_changed) if started, None if disabled or failed.
    Call notify_state_changed() when state changes to push to SSE clients.
    """
    if not bool(config.get("enable_http", True)):
        return None

    port = int(config.get("http_port", 8081))
    sse_subscribers: Set[asyncio.WriteTransport] = set()

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
            # No running loop; ignore (e.g. during shutdown)
            pass

    try:
        def factory():
            h = HttpServerHandler(
                get_state,
                logger,
                sse_subscribers,
                send_command=send_command,
                request_state=request_state,
                dashboard_html=dashboard_html,
            )
            h.on_sse_push = notify_state_changed
            return h

        server = await asyncio.get_running_loop().create_server(
            factory,
            "0.0.0.0",
            port,
            reuse_address=True,
        )
        # port=0 means "let the OS pick a free port"; store the chosen port so callers (e.g. tests) can connect
        if port == 0 and server.sockets:
            config["http_port"] = server.sockets[0].getsockname()[1]
        return server, notify_state_changed
    except OSError as e:
        logger.warning("HTTP server port %d unavailable: %s", port, e)
        return None

