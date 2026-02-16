"""
JSON status API for denon-proxy.

Serves GET / for status and POST /state to set virtual AVR state (simulates
AVR changes and broadcasts to clients for testing).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional


class JsonApiHandler(asyncio.Protocol):
    """HTTP handler that serves JSON status for GET and POST /state."""

    def __init__(
        self,
        get_state: Callable[[], dict[str, Any]],
        logger: logging.Logger,
        set_state: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self.get_state = get_state
        self.set_state = set_state
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

        if method == "GET" and path in ("/", "/status"):
            self._handle_get()
        elif method == "POST" and path == "/state":
            self._handle_post_state()
        else:
            self._send(404, {"error": "Not Found"})

    def _handle_get(self) -> None:
        try:
            body = json.dumps(self.get_state(), indent=2).encode("utf-8")
        except Exception as e:
            self.logger.warning("JSON API get_state error: %s", e)
            self._send(500, {"error": "Internal Server Error"})
            return
        self._send_body(200, body)

    def _handle_post_state(self) -> None:
        if not self.set_state:
            self._send(501, {"error": "set_state not configured"})
            return
        headers_end = self._buffer.index(b"\r\n\r\n")
        body_bytes = self._buffer[headers_end + 4:]
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send(400, {"error": f"Invalid JSON: {e}"})
            return
        if not isinstance(payload, dict):
            self._send(400, {"error": "Body must be a JSON object"})
            return
        try:
            self.set_state(payload)
            self._send(200, {"ok": True, "state": self.get_state().get("state", {})})
        except Exception as e:
            self.logger.warning("JSON API set_state error: %s", e)
            self._send(500, {"error": str(e)})

    def _send(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self._send_body(status, body)

    def _send_body(self, status: int, body: bytes) -> None:
        reasons = {
            200: "OK", 400: "Bad Request", 404: "Not Found",
            405: "Method Not Allowed", 500: "Internal Server Error", 501: "Not Implemented",
        }
        reason = reasons.get(status, "Error")
        resp = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: application/json\r\n"
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
) -> Optional[asyncio.Server]:
    """
    Start the JSON status API server.

    GET / or /status returns current state.
    POST /state with JSON body {power, volume, input_source, mute, sound_mode}
    sets virtual AVR state and broadcasts to clients (for testing).

    Returns the server if started, None if disabled or failed.
    """
    if not config.get("enable_web_ui"):
        return None
    port = int(config.get("web_ui_port", 8081))
    try:
        server = await asyncio.get_running_loop().create_server(
            lambda: JsonApiHandler(get_state, logger, set_state),
            "0.0.0.0",
            port,
            reuse_address=True,
        )
        return server
    except OSError as e:
        logger.warning("JSON API port %d unavailable: %s", port, e)
        return None
