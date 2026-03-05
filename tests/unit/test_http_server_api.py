"""Unit/handler tests for HTTP JSON/SSE API (/api/status, /api/command, /api/refresh, /events)
using run_http_server with mocked callbacks (no full proxy)."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from avr_state import AVRState
from denon_proxy import build_json_state, load_config_from_dict
from http_server import run_http_server


async def _open_connection(port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", port),
        timeout=2.0,
    )


def _parse_http_response(raw: bytes) -> tuple[bytes, bytes]:
    """Split raw HTTP response into (status_line, body_bytes)."""
    headers, _, body = raw.partition(b"\r\n\r\n")
    status_line = headers.split(b"\r\n", 1)[0]
    return status_line, body


@pytest.mark.asyncio
async def test_http_get_status_json_shape():
    """
    Start HTTP JSON API with a minimal config and AVRState, GET /api/status, and assert JSON shape.
    """
    # Minimal config: HTTP interface enabled on port 0 so OS picks a free port.
    config = load_config_from_dict(
        {
            "enable_http": True,
            "http_port": 0,
            "enable_ssdp": False,
            "ssdp_friendly_name": "My AVR Proxy",
            "_resolved_sources": [("CD", "CD"), ("HDMI1", "Game Console")],
        }
    )

    logger = logging.getLogger("test.http.status")
    state = AVRState()

    class _FakeAvr:
        volume_max = 80.0

        def is_connected(self) -> bool:
            return True

        def get_details(self) -> dict:
            return {"type": "virtual", "host": "127.0.0.1", "port": 23}

    def get_state() -> dict:
        return build_json_state(state, _FakeAvr(), [], config)

    result = await run_http_server(config, logger, get_state)
    assert result is not None, "HTTP server should start when HTTP is enabled"
    server, _notify_state_changed = result

    try:
        # http_port may be 0 in config; read the real port from the server socket.
        sock = server.sockets[0]
        port = sock.getsockname()[1]

        reader, writer = await _open_connection(port)
        try:
            request = (
                "GET /api/status HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            writer.write(request)
            await writer.drain()

            response = await asyncio.wait_for(reader.read(65536), timeout=2.0)
            status_line, body = _parse_http_response(response)
            assert b"200" in status_line, f"Expected HTTP 200, got: {status_line!r}"

            data = json.loads(body.decode("utf-8"))

            # Top-level keys and basic structure
            assert set(data.keys()) == {
                "friendly_name",
                "avr",
                "clients",
                "client_count",
                "state",
                "discovery",
            }
            assert data["friendly_name"] == "My AVR Proxy"
            assert isinstance(data["clients"], list)
            assert isinstance(data["client_count"], int)

            avr = data["avr"]
            assert isinstance(avr, dict)
            assert avr["type"] == "virtual"
            assert avr["connected"] is True
            assert isinstance(avr.get("volume_max"), (int, float))
            assert "sources" in avr
            assert avr["sources"][0]["func"] == "CD"
            assert avr["sources"][0]["display_name"] == "CD"

            state_json = data["state"]
            assert isinstance(state_json, dict)
            # AVRState defaults: power/on, volume numeric, input_source, mute, sound_mode
            assert state_json["power"] == "ON"
            assert isinstance(state_json["volume"], (int, float))
            assert state_json["input_source"] == "CD"
            assert state_json["mute"] is False
            assert state_json["sound_mode"] == "STEREO"
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_http_post_command_requires_send_command():
    """POST /api/command returns 501 when send_command is not configured."""
    config = load_config_from_dict(
        {
            "enable_http": True,
            "http_port": 0,
        }
    )
    logger = logging.getLogger("test.http.command.501")

    def get_state() -> dict:
        return {}

    result = await run_http_server(config, logger, get_state)
    assert result is not None
    server, _ = result

    try:
        port = server.sockets[0].getsockname()[1]
        reader, writer = await _open_connection(port)
        try:
            body = b'{"command":"PWON"}'
            request = (
                "POST /api/command HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii") + body
            writer.write(request)
            await writer.drain()

            response = await asyncio.wait_for(reader.read(65536), timeout=2.0)
            status_line, body_bytes = _parse_http_response(response)
            assert b"501" in status_line
            data = json.loads(body_bytes.decode("utf-8"))
            assert data["error"].startswith("send_command not configured")
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_http_post_command_calls_send_command():
    """POST /api/command returns 200 and calls send_command when configured."""
    config = load_config_from_dict(
        {
            "enable_http": True,
            "http_port": 0,
        }
    )
    logger = logging.getLogger("test.http.command.ok")

    commands: list[str] = []

    def get_state() -> dict:
        return {}

    def send_command(cmd: str) -> None:
        commands.append(cmd)

    result = await run_http_server(config, logger, get_state, send_command=send_command)
    assert result is not None
    server, _ = result

    try:
        port = server.sockets[0].getsockname()[1]
        reader, writer = await _open_connection(port)
        try:
            body = b'{"command":"PWON"}'
            request = (
                "POST /api/command HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii") + body
            writer.write(request)
            await writer.drain()

            response = await asyncio.wait_for(reader.read(65536), timeout=2.0)
            status_line, body_bytes = _parse_http_response(response)
            assert b"200" in status_line
            data = json.loads(body_bytes.decode("utf-8"))
            assert data == {"ok": True, "command": "PWON"}
            assert commands == ["PWON"]
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,expected_error_substring",
    [
        (b"not json", "Invalid JSON"),
        (b"{}", "Body must be JSON object with 'command' string"),
        (b'{"command": 123}', "Body must be JSON object with 'command' string"),
        (b'{"command": null}', "Body must be JSON object with 'command' string"),
        (b'{"command": ""}', "Body must be JSON object with 'command' string"),
        (b'{"command": "P"}', "Command too short"),
    ],
)
async def test_http_post_command_bad_body_returns_400(body, expected_error_substring):
    """POST /api/command returns 400 with error JSON for invalid or missing command body."""
    config = load_config_from_dict(
        {
            "enable_http": True,
            "http_port": 0,
        }
    )
    logger = logging.getLogger("test.http.command.badbody")

    def get_state() -> dict:
        return {}

    def send_command(cmd: str) -> None:
        pass

    result = await run_http_server(config, logger, get_state, send_command=send_command)
    assert result is not None
    server, _ = result

    try:
        port = server.sockets[0].getsockname()[1]
        reader, writer = await _open_connection(port)
        try:
            request = (
                "POST /api/command HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii") + body
            writer.write(request)
            await writer.drain()

            response = await asyncio.wait_for(reader.read(65536), timeout=2.0)
            status_line, body_bytes = _parse_http_response(response)
            assert b"400" in status_line
            data = json.loads(body_bytes.decode("utf-8"))
            assert "error" in data
            assert expected_error_substring in data["error"]
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_http_post_refresh_requires_request_state():
    """POST /api/refresh returns 501 when request_state is not configured."""
    config = load_config_from_dict(
        {
            "enable_http": True,
            "http_port": 0,
        }
    )
    logger = logging.getLogger("test.http.refresh.501")

    def get_state() -> dict:
        return {}

    result = await run_http_server(config, logger, get_state)
    assert result is not None
    server, _ = result

    try:
        port = server.sockets[0].getsockname()[1]
        reader, writer = await _open_connection(port)
        try:
            request = (
                "POST /api/refresh HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            writer.write(request)
            await writer.drain()

            response = await asyncio.wait_for(reader.read(65536), timeout=2.0)
            status_line, body_bytes = _parse_http_response(response)
            assert b"501" in status_line
            data = json.loads(body_bytes.decode("utf-8"))
            assert data["error"].startswith("request_state not configured")
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_http_post_refresh_calls_request_state():
    """POST /api/refresh returns 200 and calls request_state when configured."""
    config = load_config_from_dict(
        {
            "enable_http": True,
            "http_port": 0,
        }
    )
    logger = logging.getLogger("test.http.refresh.ok")

    called: list[bool] = []

    def get_state() -> dict:
        return {}

    def request_state() -> None:
        called.append(True)

    result = await run_http_server(config, logger, get_state, request_state=request_state)
    assert result is not None
    server, _ = result

    try:
        port = server.sockets[0].getsockname()[1]
        reader, writer = await _open_connection(port)
        try:
            request = (
                "POST /api/refresh HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            writer.write(request)
            await writer.drain()

            response = await asyncio.wait_for(reader.read(65536), timeout=2.0)
            status_line, body_bytes = _parse_http_response(response)
            assert b"200" in status_line
            data = json.loads(body_bytes.decode("utf-8"))
            assert data == {"ok": True}
            assert called == [True]
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_http_events_sse_streams_state():
    """GET /events returns an SSE stream with JSON state when notify_state_changed is called."""
    config = load_config_from_dict(
        {
            "enable_http": True,
            "http_port": 0,
        }
    )
    logger = logging.getLogger("test.http.sse")

    def get_state() -> dict:
        return {"state": {"power": "ON"}}

    result = await run_http_server(config, logger, get_state)
    assert result is not None
    server, notify_state_changed = result

    try:
        port = server.sockets[0].getsockname()[1]
        reader, writer = await _open_connection(port)
        try:
            request = (
                "GET /events HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Connection: keep-alive\r\n"
                "\r\n"
            ).encode("ascii")
            writer.write(request)
            await writer.drain()

            # First push from on_sse_push in handler factory
            data_chunk = await asyncio.wait_for(reader.read(4096), timeout=2.0)
            assert b"200 OK" in data_chunk.split(b"\r\n", 1)[0]
            assert b"Content-Type: text/event-stream" in data_chunk

            # Trigger another push and ensure we see at least one data: line with our JSON
            notify_state_changed()
            data_chunk2 = await asyncio.wait_for(reader.read(4096), timeout=2.0)
            combined = (data_chunk + data_chunk2).decode("utf-8", errors="replace")
            assert "data: " in combined
            # We don't parse full SSE framing here, just ensure our JSON appears
            assert '"power": "ON"' in combined
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_http_server_disabled():
    """When HTTP is disabled, run_http_server should not start a server."""
    config = load_config_from_dict(
        {
            "enable_http": False,
            "http_port": 0,
            "enable_ssdp": False,
        }
    )

    logger = logging.getLogger("test.http.disabled")

    def get_state() -> dict:
        return {}

    result = await run_http_server(config, logger, get_state)
    assert result is None

