"""
Integration tests: DenonProxyServer with VirtualAVR and HTTP JSON API.

These tests start the full proxy stack (DenonProxyServer + VirtualAVR + HTTP),
exercise the JSON API, and assert that HTTP commands update the real proxy state.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from runtime_state import RuntimeState
from avr_connection import create_avr_connection
from denon_proxy import DenonProxyServer, load_config_from_dict


@pytest.fixture
def http_integration_config():
    """Minimal config for full-stack HTTP integration: virtual AVR, port 0, HTTP enabled, no SSDP."""
    return load_config_from_dict(
        {
            "avr_host": "",
            "proxy_host": "127.0.0.1",
            "proxy_port": 0,
            "enable_http": True,
            "http_port": 0,
            "enable_ssdp": False,
        }
    )


@pytest.fixture
def http_integration_logger():
    return logging.getLogger("test.http.integration")


async def _open_http_connection(port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
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
async def test_http_status_and_command_full_stack(http_integration_config, http_integration_logger):
    """
    Start DenonProxyServer with VirtualAVR and HTTP enabled, hit /api/status and /api/command,
    and assert that HTTP commands update the real proxy state.
    """
    server = DenonProxyServer(http_integration_config, http_integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    try:
        http_port = server.runtime_state.http_port or server.config.get("http_port")
        assert http_port and http_port != 0

        # Initial status reflects default AVRState and virtual AVR details
        reader, writer = await _open_http_connection(http_port)
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
            assert b"200" in status_line

            data = json.loads(body.decode("utf-8"))
            assert data["avr"]["type"] == "virtual"
            assert data["avr"]["connected"] is True
            # Default state comes from AVRState defaults + VirtualAVR
            assert data["state"]["power"] in ("ON", "STANDBY")
        finally:
            writer.close()
            await writer.wait_closed()

        # Send a power command via HTTP and ensure it propagates through VirtualAVR to proxy state
        reader2, writer2 = await _open_http_connection(http_port)
        try:
            body2 = b'{"command":"PWSTANDBY"}'
            request2 = (
                "POST /api/command HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body2)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii") + body2
            writer2.write(request2)
            await writer2.drain()

            response2 = await asyncio.wait_for(reader2.read(65536), timeout=2.0)
            status_line2, body_bytes2 = _parse_http_response(response2)
            assert b"200" in status_line2
            result = json.loads(body_bytes2.decode("utf-8"))
            assert result == {"ok": True, "command": "PWSTANDBY"}
        finally:
            writer2.close()
            await writer2.wait_closed()

        # Give the proxy a moment to process the VirtualAVR response
        await asyncio.sleep(0.1)

        # Status should now reflect the new power state, and proxy.avr_state should be updated
        reader3, writer3 = await _open_http_connection(http_port)
        try:
            request3 = (
                "GET /api/status HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            writer3.write(request3)
            await writer3.drain()

            response3 = await asyncio.wait_for(reader3.read(65536), timeout=2.0)
            status_line3, body_bytes3 = _parse_http_response(response3)
            assert b"200" in status_line3
            data3 = json.loads(body_bytes3.decode("utf-8"))
            assert data3["state"]["power"] == "STANDBY"
            assert server.avr_state.power == "STANDBY"
        finally:
            writer3.close()
            await writer3.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_http_status_reflects_telnet_clients(http_integration_config, http_integration_logger):
    """
    Telnet clients connected to the proxy are reflected in /api/status (clients and client_count).
    """
    server = DenonProxyServer(http_integration_config, http_integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    try:
        proxy_port = server.runtime_state.proxy_port or server.config["proxy_port"]
        http_port = server.runtime_state.http_port or server.config.get("http_port")
        assert proxy_port and proxy_port != 0, "Proxy port should be set"
        assert http_port and http_port != 0, "HTTP port should be set"

        # Connect a Telnet client to the proxy and drain the initial status dump
        reader_telnet, writer_telnet = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", proxy_port),
            timeout=2.0,
        )
        try:
            # Initial dump may contain multiple lines; just ensure connection is fully established.
            try:
                await asyncio.wait_for(reader_telnet.read(1024), timeout=1.0)
            except asyncio.TimeoutError:
                # It's fine if nothing arrives yet; connection is still valid.
                pass

            # Now GET /api/status and assert the Telnet client is visible
            reader_http, writer_http = await _open_http_connection(http_port)
            try:
                request = (
                    "GET /api/status HTTP/1.1\r\n"
                    "Host: 127.0.0.1\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                ).encode("ascii")
                writer_http.write(request)
                await writer_http.drain()

                response = await asyncio.wait_for(reader_http.read(65536), timeout=2.0)
                status_line, body = _parse_http_response(response)
                assert b"200" in status_line

                data = json.loads(body.decode("utf-8"))
                assert data["client_count"] >= 1
                assert "127.0.0.1" in data["clients"]
            finally:
                writer_http.close()
                await writer_http.wait_closed()
        finally:
            writer_telnet.close()
            await writer_telnet.wait_closed()

        # After Telnet client disconnects, status should show zero clients
        await asyncio.sleep(0.05)
        reader_http2, writer_http2 = await _open_http_connection(http_port)
        try:
            request2 = (
                "GET /api/status HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            writer_http2.write(request2)
            await writer_http2.drain()

            response2 = await asyncio.wait_for(reader_http2.read(65536), timeout=2.0)
            status_line2, body2 = _parse_http_response(response2)
            assert b"200" in status_line2

            data2 = json.loads(body2.decode("utf-8"))
            assert data2["client_count"] == 0
        finally:
            writer_http2.close()
            await writer_http2.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_http_events_sse_streams_updates_on_command(http_integration_config, http_integration_logger):
    """
    /events SSE stream receives updated JSON state when an HTTP command changes AVR state.
    """
    server = DenonProxyServer(http_integration_config, http_integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    try:
        http_port = server.runtime_state.http_port or server.config.get("http_port")
        assert http_port and http_port != 0

        # Open SSE connection
        reader_sse, writer_sse = await _open_http_connection(http_port)
        try:
            request_sse = (
                "GET /events HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Connection: keep-alive\r\n"
                "\r\n"
            ).encode("ascii")
            writer_sse.write(request_sse)
            await writer_sse.drain()

            # First chunk should contain headers + initial state push
            chunk1 = await asyncio.wait_for(reader_sse.read(4096), timeout=2.0)
            assert b"200 OK" in chunk1.split(b"\r\n", 1)[0]
            assert b"Content-Type: text/event-stream" in chunk1

            # Send a power command via HTTP to change state
            reader_cmd, writer_cmd = await _open_http_connection(http_port)
            try:
                body_cmd = b'{"command":"PWSTANDBY"}'
                request_cmd = (
                    "POST /api/command HTTP/1.1\r\n"
                    "Host: 127.0.0.1\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body_cmd)}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                ).encode("ascii") + body_cmd
                writer_cmd.write(request_cmd)
                await writer_cmd.drain()

                response_cmd = await asyncio.wait_for(reader_cmd.read(65536), timeout=2.0)
                status_line_cmd, body_cmd_bytes = _parse_http_response(response_cmd)
                assert b"200" in status_line_cmd
                result_cmd = json.loads(body_cmd_bytes.decode("utf-8"))
                assert result_cmd == {"ok": True, "command": "PWSTANDBY"}
            finally:
                writer_cmd.close()
                await writer_cmd.wait_closed()

            # Read more from SSE stream and ensure updated state (power=STANDBY) appears
            chunk2 = await asyncio.wait_for(reader_sse.read(4096), timeout=2.0)
            combined = (chunk1 + chunk2).decode("utf-8", errors="replace")
            assert "data: " in combined
            assert '"power": "STANDBY"' in combined
        finally:
            writer_sse.close()
            await writer_sse.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_http_refresh_request_state_broadcasts_to_telnet_clients(
    http_integration_config, http_integration_logger
):
    """
    POST /api/refresh triggers request_state() on the AVR; VirtualAVR pushes
    get_status_dump() lines via on_response; proxy broadcasts to Telnet clients.
    Assert two connected Telnet clients both receive the status dump after refresh.
    """
    server = DenonProxyServer(http_integration_config, http_integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    proxy_port = server.runtime_state.proxy_port or server.config["proxy_port"]
    http_port = server.runtime_state.http_port or server.config.get("http_port")
    assert proxy_port and proxy_port != 0, "Proxy port should be set"
    assert http_port and http_port != 0, "HTTP port should be set"
    try:
        # Connect two Telnet clients and drain initial status dumps
        reader_a, writer_a = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", proxy_port),
            timeout=2.0,
        )
        reader_b, writer_b = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", proxy_port),
            timeout=2.0,
        )
        try:
            await asyncio.wait_for(reader_a.read(4096), timeout=1.0)
            await asyncio.wait_for(reader_b.read(4096), timeout=1.0)

            # Trigger request_state via HTTP (proxy calls avr.request_state())
            reader_http, writer_http = await _open_http_connection(http_port)
            try:
                request = (
                    "POST /api/refresh HTTP/1.1\r\n"
                    "Host: 127.0.0.1\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                ).encode("ascii")
                writer_http.write(request)
                await writer_http.drain()
                response = await asyncio.wait_for(reader_http.read(4096), timeout=2.0)
                status_line, _ = _parse_http_response(response)
                assert b"200" in status_line
            finally:
                writer_http.close()
                await writer_http.wait_closed()

            # Both Telnet clients should receive the broadcast (status dump lines from VirtualAVR)
            burst_a = await asyncio.wait_for(reader_a.read(4096), timeout=2.0)
            burst_b = await asyncio.wait_for(reader_b.read(4096), timeout=2.0)
            text_a = burst_a.decode("utf-8", errors="replace")
            text_b = burst_b.decode("utf-8", errors="replace")
            # request_state pushes PW, ZM, MV, SI, MU, MS (and optionally MSSMART)
            for label, text in (("A", text_a), ("B", text_b)):
                assert "PW" in text or "ZM" in text, f"[{label}] Expected power lines in broadcast, got: {text!r}"
                assert "MV" in text, f"[{label}] Expected volume line in broadcast, got: {text!r}"
                assert "SI" in text, f"[{label}] Expected input line in broadcast, got: {text!r}"
                assert "MU" in text, f"[{label}] Expected mute line in broadcast, got: {text!r}"
        finally:
            writer_a.close()
            writer_b.close()
            await writer_a.wait_closed()
            await writer_b.wait_closed()
    finally:
        await server.stop()
