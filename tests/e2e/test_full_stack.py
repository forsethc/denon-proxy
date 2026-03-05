"""
E2E test: Full stack in one run (proxy + HTTP API + discovery + Telnet).

Single test that starts the production-like stack (enable_http, enable_ssdp),
hits the JSON API, connects a Telnet client, sends a command, and asserts
both API and Telnet paths see consistent state and client visibility.
"""

from __future__ import annotations

import asyncio
import json

import pytest


@pytest.mark.asyncio
async def test_full_stack_api_and_telnet_together(full_stack_http):
    """
    With proxy + HTTP + discovery running: GET /api/status, POST /api/command,
    GET /api/status again, connect Telnet client, send MV50, then GET /api/status
    and assert client_count >= 1 and state.volume updated.
    """
    proxy, _ssdp, _discovery_servers, config = full_stack_http
    http_port = config.get("http_port") or proxy.config.get("http_port")
    proxy_port = proxy.config["proxy_port"]
    assert http_port and http_port != 0
    assert proxy_port != 0

    async def get_status() -> dict:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", http_port),
            timeout=2.0,
        )
        try:
            writer.write(
                b"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(65536), timeout=2.0)
        finally:
            writer.close()
            await writer.wait_closed()
        _, body = raw.split(b"\r\n\r\n", 1)
        return json.loads(body.decode("utf-8"))

    async def post_command(command: str):
        body = json.dumps({"command": command}).encode("utf-8")
        header = (
            f"POST /api/command HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", http_port),
            timeout=2.0,
        )
        try:
            writer.write(header + body)
            await writer.drain()
            await asyncio.wait_for(reader.read(8192), timeout=2.0)
        finally:
            writer.close()
            await writer.wait_closed()

    data0 = await get_status()
    assert "state" in data0
    assert data0["client_count"] == 0

    await post_command("MV50")
    await asyncio.sleep(0.15)
    data1 = await get_status()
    assert data1["state"]["volume"] in (50, 50.0, "50")
    assert proxy.state.volume == "50"

    telnet_reader, telnet_writer = await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", proxy_port),
        timeout=2.0,
    )
    try:
        await asyncio.wait_for(telnet_reader.read(4096), timeout=1.0)
        data2 = await get_status()
        assert data2["client_count"] >= 1
        assert "127.0.0.1" in data2["clients"]

        telnet_writer.write(b"MUON\r")
        await telnet_writer.drain()
        await asyncio.wait_for(telnet_reader.read(4096), timeout=2.0)
        await asyncio.sleep(0.1)
        data3 = await get_status()
        assert data3["state"]["mute"] is True
        assert proxy.state.mute is True
    finally:
        telnet_writer.close()
        await telnet_writer.wait_closed()
