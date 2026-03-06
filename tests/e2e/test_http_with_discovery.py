"""
E2E tests: Full stack with HTTP JSON API and discovery.

Starts DenonProxyServer (VirtualAVR) with enable_http and enable_ssdp both True,
then run_discovery_servers. Exercises discovery endpoints and JSON API in the same run
to ensure they work together (production-like).
"""

from __future__ import annotations

import asyncio
import json

import pytest


async def _http_get(host: str, port: int, path: str) -> tuple[int, bytes]:
    """GET path; return (status_code, body)."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=2.0,
    )
    try:
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        writer.write(request)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=2.0)
    finally:
        writer.close()
        await writer.wait_closed()
    if b"\r\n\r\n" not in raw:
        return 0, raw
    headers, body = raw.split(b"\r\n\r\n", 1)
    status_line = headers.split(b"\r\n", 1)[0]
    if b"200" in status_line:
        return 200, body
    if b"404" in status_line:
        return 404, body
    return 0, body


async def _http_post_json(host: str, port: int, path: str, payload: dict) -> tuple[int, bytes]:
    """POST path with JSON body; return (status_code, body)."""
    body = json.dumps(payload).encode("utf-8")
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=2.0,
    )
    try:
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii") + body
        writer.write(request)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=2.0)
    finally:
        writer.close()
        await writer.wait_closed()
    if b"\r\n\r\n" not in raw:
        return 0, raw
    _, body_bytes = raw.split(b"\r\n\r\n", 1)
    status_line = raw.split(b"\r\n", 1)[0]
    if b"200" in status_line:
        return 200, body_bytes
    return 0, body_bytes


@pytest.mark.asyncio
async def test_discovery_description_xml_while_http_enabled(full_stack_http):
    """Discovery HTTP (description.xml) is reachable when JSON API is also enabled."""
    proxy, _ssdp, discovery_http_servers, config = full_stack_http
    assert discovery_http_servers
    port = proxy.runtime_state.ssdp_http_port or config.get("ssdp_http_port")
    assert port and port != 0

    status, body = await _http_get("127.0.0.1", port, "/description.xml")
    assert status == 200
    text = body.decode("utf-8", errors="replace")
    assert "device" in text.lower()
    assert "Test Full Stack Proxy" in text


@pytest.mark.asyncio
async def test_json_api_status_and_command_while_discovery_running(full_stack_http):
    """
    GET /api/status and POST /api/command work when discovery is also running.
    Command updates proxy state; next GET reflects it.
    """
    proxy, _ssdp, _discovery_servers, config = full_stack_http
    http_port = config.get("http_port") or proxy.config.get("http_port")
    assert http_port is not None and http_port != 0

    status1, body1 = await _http_get("127.0.0.1", http_port, "/api/status")
    assert status1 == 200
    data1 = json.loads(body1.decode("utf-8"))
    assert "state" in data1
    assert data1["avr"]["type"] == "virtual"

    status_post, body_post = await _http_post_json(
        "127.0.0.1", http_port, "/api/command", {"command": "PWSTANDBY"}
    )
    assert status_post == 200
    result = json.loads(body_post.decode("utf-8"))
    assert result.get("ok") is True and result.get("command") == "PWSTANDBY"

    await asyncio.sleep(0.15)
    status2, body2 = await _http_get("127.0.0.1", http_port, "/api/status")
    assert status2 == 200
    data2 = json.loads(body2.decode("utf-8"))
    assert data2["state"]["power"] == "STANDBY"
    assert proxy.avr_state.power == "STANDBY"
