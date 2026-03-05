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


@pytest.mark.asyncio
async def test_telnet_two_clients_both_receive_broadcast(integration_config, integration_logger):
    """
    Connect two Telnet clients; one sends a command. Assert both clients receive
    the broadcast and proxy state updates (core multi-client guarantee).
    """
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection)
    await server.start()
    port = server.config["proxy_port"]
    try:
        # Connect both clients and drain initial status dump
        reader_a, writer_a = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port),
            timeout=2.0,
        )
        reader_b, writer_b = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port),
            timeout=2.0,
        )
        try:
            await asyncio.wait_for(reader_a.read(4096), timeout=1.0)
            await asyncio.wait_for(reader_b.read(4096), timeout=1.0)

            # Client A sends volume command
            writer_a.write(b"MV50\r")
            await writer_a.drain()

            # Both clients should receive the broadcast (MV50)
            response_a = await asyncio.wait_for(reader_a.read(4096), timeout=2.0)
            response_b = await asyncio.wait_for(reader_b.read(4096), timeout=2.0)
            text_a = response_a.decode("utf-8", errors="replace")
            text_b = response_b.decode("utf-8", errors="replace")

            assert "MV50" in text_a, f"Sender (A) should receive broadcast, got: {text_a!r}"
            assert "MV50" in text_b, f"Other client (B) should receive broadcast, got: {text_b!r}"
            assert server.state.volume == "50"
        finally:
            writer_a.close()
            writer_b.close()
            await writer_a.wait_closed()
            await writer_b.wait_closed()
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
