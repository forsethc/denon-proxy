"""
Integration tests: proxy with VirtualAVR, Telnet client sends command and receives broadcast.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from avr_connection import create_avr_connection
from denon_proxy import DenonProxyServer, load_config_from_dict


@pytest.fixture
def integration_config():
    """Minimal config for integration: virtual AVR, port 0, no Web UI, no SSDP."""
    return load_config_from_dict({
        "avr_host": "",
        "proxy_host": "127.0.0.1",
        "proxy_port": 0,
        "enable_web_ui": False,
        "enable_ssdp": False,
    })


@pytest.fixture
def integration_logger():
    return logging.getLogger("test.integration")


@pytest.mark.asyncio
async def test_telnet_pwon_receives_broadcast(integration_config, integration_logger):
    """
    Start proxy with VirtualAVR, connect one Telnet client, send PWON.
    Assert client receives PWON and ZMON (and proxy state is ON).
    """
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection)
    await server.start()
    port = server.config["proxy_port"]
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port),
            timeout=2.0,
        )
        try:
            # Receive initial status dump (proxy sends current state on connect; default is ON)
            initial = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            assert b"PW" in initial or b"ZM" in initial

            # Put state in STANDBY so we can verify PWON actually changes it (default is already ON)
            writer.write(b"PWSTANDBY\r")
            await writer.drain()
            await asyncio.wait_for(reader.read(4096), timeout=1.0)
            assert server.state.power == "STANDBY"

            # Send PWON (Denon line terminator is \r)
            writer.write(b"PWON\r")
            await writer.drain()

            # Read broadcast response (PWON + ZMON from VirtualAVR + proxy)
            response = await asyncio.wait_for(reader.read(4096), timeout=2.0)
            response_str = response.decode("utf-8", errors="replace")
            assert "PWON" in response_str, f"Expected PWON in broadcast, got: {response_str!r}"
            assert "ZMON" in response_str, f"Expected ZMON in broadcast, got: {response_str!r}"

            # PWON actually updated proxy state (we started from STANDBY above)
            assert server.state.power == "ON"
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()
