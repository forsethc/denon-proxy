"""
JSON status API for denon-proxy.

Serves a single GET endpoint that returns proxy state, connected clients,
and AVR state as JSON. No external dependencies.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional


class JsonApiHandler(asyncio.Protocol):
    """HTTP handler that serves JSON status for GET requests."""

    def __init__(self, get_state: Callable[[], dict[str, Any]], logger: logging.Logger) -> None:
        self.get_state = get_state
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
        if not req_line.upper().startswith("GET "):
            self._send(405, {"error": "Method Not Allowed"})
            return
        try:
            body = json.dumps(self.get_state(), indent=2).encode("utf-8")
        except Exception as e:
            self.logger.warning("JSON API get_state error: %s", e)
            self._send(500, {"error": "Internal Server Error"})
            return
        self._send_body(200, body)

    def _send(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self._send_body(status, body)

    def _send_body(self, status: int, body: bytes) -> None:
        reasons = {200: "OK", 405: "Method Not Allowed", 500: "Internal Server Error"}
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
) -> Optional[asyncio.Server]:
    """
    Start the JSON status API server.

    Returns the server if started, None if disabled or failed.
    """
    if not config.get("enable_web_ui"):
        return None
    port = int(config.get("web_ui_port", 8081))
    try:
        server = await asyncio.get_running_loop().create_server(
            lambda: JsonApiHandler(get_state, logger),
            "0.0.0.0",
            port,
            reuse_address=True,
        )
        return server
    except OSError as e:
        logger.warning("JSON API port %d unavailable: %s", port, e)
        return None
