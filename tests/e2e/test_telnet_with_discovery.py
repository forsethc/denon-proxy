"""
E2E tests: Telnet path with discovery stack running.

Uses the same full stack as test_discovery (DenonProxyServer + run_discovery_servers).
Connects a Telnet client to the proxy, sends commands, and asserts broadcasts and state.
Ensures telnet works when discovery is also running (production-like).
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_telnet_command_and_broadcast_on_discovery_stack(discovery_stack):
    """
    With proxy + discovery running, connect Telnet client, send PWSTANDBY then PWON;
    assert client receives PWON and ZMON and proxy state becomes ON.
    """
    proxy, _ssdp, _http_servers = discovery_stack
    port = proxy.runtime_state.proxy_port or proxy.config["proxy_port"]
    assert port != 0

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", port),
        timeout=2.0,
    )
    try:
        await asyncio.wait_for(reader.read(4096), timeout=1.0)

        writer.write(b"PWSTANDBY\r")
        await writer.drain()
        await asyncio.wait_for(reader.read(4096), timeout=2.0)
        assert proxy.avr_state.power == "STANDBY"

        writer.write(b"PWON\r")
        await writer.drain()
        await asyncio.sleep(0.15)  # allow broadcast (PWON, ZMON) to be written
        response = await asyncio.wait_for(reader.read(4096), timeout=2.0)
        text = response.decode("utf-8", errors="replace")
        assert "PWON" in text
        assert "ZMON" in text
        assert proxy.avr_state.power == "ON"
    finally:
        writer.close()
        await writer.wait_closed()
