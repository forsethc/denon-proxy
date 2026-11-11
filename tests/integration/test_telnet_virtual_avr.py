"""Integration tests: proxy + VirtualAVR, Telnet clients send commands and receive broadcasts."""

from __future__ import annotations

import asyncio
import logging

import pytest

from denon_proxy.avr.connection import create_avr_connection
from denon_proxy.proxy.core import DenonProxyServer
from denon_proxy.runtime.config import Config
from denon_proxy.runtime.state import RuntimeState


@pytest.fixture
def integration_config():
    """Minimal config for integration: virtual AVR, port 0, no Web UI, no SSDP."""
    return Config.model_validate(
        {
            "avr_host": "",
            "proxy_host": "127.0.0.1",
            "proxy_port": 0,
            "enable_http": False,
            "enable_ssdp": False,
        }
    )


@pytest.fixture
def integration_logger():
    return logging.getLogger("test.integration")


@pytest.mark.asyncio
async def test_telnet_pwon_receives_broadcast(integration_config, integration_logger):
    """
    Put state in STANDBY then send PWON; assert client receives PWON and state becomes ON.
    (Default state is already ON, so we establish STANDBY first to verify PWON actually changes state.)
    """
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    port = server.runtime_state.proxy_port or server.config["proxy_port"]
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
            ["PWON"],
        )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_telnet_volume_command(integration_config, integration_logger):
    """MV50 sets volume; client receives MV in broadcast."""
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    try:
        await _telnet_send_and_assert(
            server,
            server.runtime_state.proxy_port or server.config["proxy_port"],
            [b"MV50"],
            {"volume": "50"},
            ["MV50"],
        )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_telnet_mute_commands(integration_config, integration_logger):
    """MUON / MUOFF update mute state and are broadcast."""
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    port = server.runtime_state.proxy_port or server.config["proxy_port"]
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
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    try:
        await _telnet_send_and_assert(
            server,
            server.runtime_state.proxy_port or server.config["proxy_port"],
            [b"SIHDMI1"],
            {"input_source": "HDMI1"},
            ["SIHDMI1"],
        )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_telnet_sound_mode(integration_config, integration_logger):
    """MS<mode> sets sound mode and is broadcast."""
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    try:
        await _telnet_send_and_assert(
            server,
            server.runtime_state.proxy_port or server.config["proxy_port"],
            [b"MSSTEREO"],
            {"sound_mode": "STEREO"},
            ["MSSTEREO"],
        )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_telnet_mvup_updates_volume(integration_config, integration_logger):
    """MVUP increases volume; client receives MV in broadcast."""
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    try:
        await _telnet_send_and_assert(
            server,
            server.runtime_state.proxy_port or server.config["proxy_port"],
            [b"MV50", b"MVUP"],
            {"volume": "505"},
            ["MV505"],
        )
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_telnet_invalid_command_ignored(integration_config, integration_logger):
    """
    Invalid telnet input (too short or control bytes) is ignored: proxy stays up and state unchanged.
    """
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    port = server.runtime_state.proxy_port or server.config["proxy_port"]
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port),
            timeout=2.0,
        )
        try:
            await asyncio.wait_for(reader.read(4096), timeout=1.0)
            initial_volume = server.avr_state.volume
            # Send invalid commands: single byte, empty line
            writer.write(b"X\r")
            await writer.drain()
            writer.write(b"\r\n")
            await writer.drain()
            await asyncio.sleep(0.1)
            # State should be unchanged (invalid commands not applied)
            assert server.avr_state.volume == initial_volume
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_telnet_two_clients_both_receive_broadcast(integration_config, integration_logger):
    """
    Connect two Telnet clients; one sends a command. Assert both clients receive
    the broadcast and proxy state updates (core multi-client guarantee).
    """
    server = DenonProxyServer(integration_config, integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    port = server.runtime_state.proxy_port or server.config["proxy_port"]
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
            assert server.avr_state.volume == "50"
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
            await asyncio.sleep(0.1)  # allow command to be written
            await writer.drain()
        response = await asyncio.wait_for(reader.read(4096), timeout=timeout)
        response_str = response.decode("utf-8", errors="replace")
        for sub in response_contains:
            assert sub in response_str, f"Expected {sub!r} in response, got: {response_str!r}"
        for attr, expected in state_assertions.items():
            actual = getattr(server.avr_state, attr)
            assert actual == expected, f"state.{attr}: got {actual!r}, expected {expected!r}"
    finally:
        writer.close()
        await writer.wait_closed()


async def _read_until(reader: asyncio.StreamReader, needle: str, timeout: float = 2.0) -> str:
    """Read from reader until needle appears (commands can expand into several AVR round trips)."""
    received = ""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while needle not in received:
        chunk = await asyncio.wait_for(reader.read(4096), timeout=max(0.01, deadline - loop.time()))
        if not chunk:
            break
        received += chunk.decode("utf-8", errors="replace")
    return received


def _virtual_sources_config(virtual_sources: dict) -> Config:
    """Integration config (virtual AVR, ephemeral port, no HTTP/SSDP) with the given virtual_sources."""
    return Config.model_validate(
        {
            "avr_host": "",
            "proxy_host": "127.0.0.1",
            "proxy_port": 0,
            "enable_http": False,
            "enable_ssdp": False,
            "virtual_sources": virtual_sources,
        }
    )


def _record_avr_commands(server: DenonProxyServer) -> list[str]:
    """Wrap the server's AVR connection so every command it is sent is recorded (and still delivered)."""
    assert server.avr is not None
    sent: list[str] = []
    send = server.avr.send_command

    async def recording_send(command: str) -> bool:
        sent.append(command)
        return await send(command)

    server.avr.send_command = recording_send  # type: ignore[method-assign]
    return sent


@pytest.mark.asyncio
async def test_telnet_virtual_source_selects_input_and_mode(integration_logger):
    """
    A client selecting a virtual source (as Home Assistant would) gets the input switched and the
    input mode applied, and every client sees the virtual source; selecting the plain input restores it.
    """
    config = _virtual_sources_config({"MPLAY_ANALOG": {"input": "MPLAY", "input_mode": "ANALOG"}})
    server = DenonProxyServer(config, integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    port = server.runtime_state.proxy_port or server.config["proxy_port"]
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=2.0)
        try:
            await asyncio.wait_for(reader.read(4096), timeout=1.0)

            writer.write(b"SIMPLAY_ANALOG\r")
            await writer.drain()
            assert "SIMPLAY_ANALOG\r" in await _read_until(reader, "SIMPLAY_ANALOG\r")
            assert (server.avr_state.input_source, server.avr_state.input_mode) == ("MPLAY_ANALOG", "ANALOG")

            writer.write(b"SIMPLAY\r")
            await writer.drain()
            await _read_until(reader, "SIMPLAY\r")
            await asyncio.sleep(0.4)  # let the follow-up SDAUTO land
            assert (server.avr_state.input_source, server.avr_state.input_mode) == ("MPLAY", "AUTO")
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_telnet_plain_source_uses_default_of_input_being_selected(integration_logger):
    """
    Two inputs, each with an analog virtual source. Going from "Apple TV (Analog)" to plain Playstation
    must send Playstation's default_input_mode, so the client lands on the plain source and not on
    "Playstation (Analog)" (a real receiver would otherwise restore Playstation's remembered mode).
    """
    config = _virtual_sources_config(
        {
            "MPLAY_ANALOG": {"input": "MPLAY", "input_mode": "ANALOG"},
            "BD_ANALOG": {"input": "BD", "input_mode": "ANALOG", "default_input_mode": "HDMI"},
        }
    )
    server = DenonProxyServer(config, integration_logger, create_avr_connection, RuntimeState())
    await server.start()
    sent = _record_avr_commands(server)
    port = server.runtime_state.proxy_port or server.config["proxy_port"]
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=2.0)
        try:
            await asyncio.wait_for(reader.read(4096), timeout=1.0)

            writer.write(b"SIMPLAY_ANALOG\r")
            await writer.drain()
            await _read_until(reader, "SIMPLAY_ANALOG\r")

            writer.write(b"SIBD\r")
            await writer.drain()
            received = await _read_until(reader, "SIBD\r")
            await asyncio.sleep(0.4)  # let the follow-up SDHDMI land
            received += await _read_available(reader)

            assert sent == ["SIMPLAY", "SDANALOG", "SIBD", "SDHDMI"]
            assert (server.avr_state.input_source, server.avr_state.input_mode) == ("BD", "HDMI")
            assert "SIBD_ANALOG" not in received
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()


async def _read_available(reader: asyncio.StreamReader, timeout: float = 0.2) -> str:
    """Read whatever arrives within timeout (empty string if nothing does)."""
    try:
        chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
    except asyncio.TimeoutError:
        return ""
    return chunk.decode("utf-8", errors="replace")
