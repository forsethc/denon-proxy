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
        "enable_http": False,
        "enable_ssdp": False,
    })


@pytest.fixture
def integration_logger():
    return logging.getLogger("test.integration")


@pytest.mark.asyncio
async def test_telnet_pwon_receives_broadcast(integration_config, integration_logger):
    """
    Put state in STANDBY then send PWON; assert client receives PWON and ZMON and state becomes ON.
    (Default state is already ON, so we establish STANDBY first to verify PWON actually changes state.)
    """
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection)
    await server.start()
    port = server.config["proxy_port"]
    try:
        await _telnet_send_and_assert(
            server,
            port,
            [b"PWSTANDBY"],
            {"power": "STANDBY"},
            ["PWSTANDBY"],
        )
        await _telnet_send_and_assert(
            server,
            port,
            [b"PWON"],
            {"power": "ON"},
            ["PWON", "ZMON"],
        )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_telnet_volume_command(integration_config, integration_logger):
    """MV50 sets volume; client receives MV in broadcast."""
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection)
    await server.start()
    try:
        await _telnet_send_and_assert(
            server,
            server.config["proxy_port"],
            [b"MV50"],
            {"volume": "50"},
            ["MV50"],
        )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_telnet_mute_commands(integration_config, integration_logger):
    """MUON / MUOFF update mute state and are broadcast."""
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection)
    await server.start()
    port = server.config["proxy_port"]
    try:
        await _telnet_send_and_assert(
            server,
            port,
            [b"MUON"],
            {"mute": True},
            ["MUON"],
        )
        await _telnet_send_and_assert(
            server,
            port,
            [b"MUOFF"],
            {"mute": False},
            ["MUOFF"],
        )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_telnet_input_source(integration_config, integration_logger):
    """SI<name> sets input source and is broadcast."""
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection)
    await server.start()
    try:
        await _telnet_send_and_assert(
            server,
            server.config["proxy_port"],
            [b"SIHDMI1"],
            {"input_source": "HDMI1"},
            ["SIHDMI1"],
        )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_telnet_sound_mode(integration_config, integration_logger):
    """MS<mode> sets sound mode and is broadcast."""
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection)
    await server.start()
    try:
        await _telnet_send_and_assert(
            server,
            server.config["proxy_port"],
            [b"MSSTEREO"],
            {"sound_mode": "STEREO"},
            ["MSSTEREO"],
        )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_telnet_mvup_updates_volume(integration_config, integration_logger):
    """MVUP increases volume; client receives MV in broadcast."""
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection)
    await server.start()
    try:
        await _telnet_send_and_assert(
            server,
            server.config["proxy_port"],
            [b"MV50", b"MVUP"],
            {"volume": "505"},
            ["MV505"],
        )
    finally:
        await server.stop()


async def _telnet_send_and_assert(
    server: DenonProxyServer,
    port: int,
    commands: list[bytes],
    state_assertions: dict,
    response_contains: list[str],
    timeout: float = 2.0,
) -> None:
    """Connect, drain initial dump, send commands, read response, assert state and response content."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", port),
        timeout=timeout,
    )
    try:
        await asyncio.wait_for(reader.read(4096), timeout=1.0)
        for cmd in commands:
            writer.write(cmd + b"\r")
            await writer.drain()
        response = await asyncio.wait_for(reader.read(4096), timeout=timeout)
        response_str = response.decode("utf-8", errors="replace")
        for sub in response_contains:
            assert sub in response_str, f"Expected {sub!r} in response, got: {response_str!r}"
        for attr, expected in state_assertions.items():
            assert getattr(server.state, attr) == expected, f"state.{attr}: got {getattr(server.state, attr)!r}, expected {expected!r}"
    finally:
        writer.close()
        await writer.wait_closed()
