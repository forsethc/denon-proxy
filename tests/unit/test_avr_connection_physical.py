"""Unit tests: AVRConnection (physical telnet) with a small TCP server."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

from denon_proxy.avr.connection import AVRConnection
from denon_proxy.avr.state import AVRState
from denon_proxy.runtime.config import Config


def _make_avr(host: str, port: int):
    state = AVRState()
    responses = []
    disconnected = []

    def on_response(msg: str) -> None:
        responses.append(msg)

    def on_disconnect() -> None:
        disconnected.append(True)

    logger = logging.getLogger("test.avr_physical")
    return (
        AVRConnection(
            host=host,
            port=port,
            on_response=on_response,
            on_disconnect=on_disconnect,
            avr_state=state,
            logger=logger,
            on_send_while_disconnected=lambda: disconnected.append(True),
        ),
        state,
        responses,
        disconnected,
    )


def test_avr_connection_is_connected_and_get_details():
    """is_connected is False before connect; get_details returns type and host/port."""
    avr, _state, _r, _d = _make_avr("192.168.1.1", 23)
    assert avr.is_connected() is False
    assert avr.get_details() == {"type": "physical", "host": "192.168.1.1", "port": 23}


def test_avr_connection_send_command_when_not_connected():
    """send_command when not connected calls on_send_while_disconnected and returns False."""
    avr, _state, _r, disconnected = _make_avr("127.0.0.1", 23)

    async def run():
        return await avr.send_command("PWON")

    result = asyncio.run(run())
    assert result is False
    assert len(disconnected) == 1


@pytest.mark.asyncio
async def test_avr_connection_connect_fails_when_nothing_listening():
    """connect() to a port with no server returns False (ConnectionRefusedError)."""
    avr, _state, _r, _d = _make_avr("127.0.0.1", 31999)
    # Cap connect timeout at 1s so test stays fast (production uses 5s)
    real_wait_for = asyncio.wait_for

    def capped_wait_for(aw, timeout=5.0):
        return real_wait_for(aw, timeout=min(timeout, 1.0))

    with patch("denon_proxy.avr.connection.asyncio.wait_for", capped_wait_for):
        result = await avr.connect()
    assert result is False
    assert avr.is_connected() is False


@pytest.mark.asyncio
async def test_avr_connection_connect_and_read_loop():
    """connect() to a server that sends PWON and MV50; _read_loop updates state and on_response."""
    responses = []
    disconnected = []

    def on_response(msg: str) -> None:
        responses.append(msg)

    async def server_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        writer.write(b"PWON\r\nMV50\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(server_cb, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    state = AVRState()
    avr = AVRConnection(
        host="127.0.0.1",
        port=port,
        on_response=on_response,
        on_disconnect=lambda: disconnected.append(True),
        avr_state=state,
        logger=logging.getLogger("test"),
        on_send_while_disconnected=None,
    )
    try:
        ok = await avr.connect()
        assert ok is True
        await asyncio.sleep(0.2)
        assert "PWON" in responses
        assert "MV50" in responses
        assert state.power == "ON"
        assert state.volume == "50"
        await asyncio.sleep(0.1)
        assert len(disconnected) == 1
    finally:
        avr.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_avr_connection_read_loop_skips_payload_with_question_mark():
    """AVR response with '?' in payload is not applied to state or sent to on_response."""
    responses = []

    def on_response(msg: str) -> None:
        responses.append(msg)

    async def server_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        writer.write(b"PWON\r\nMSQUICK ?\r\nMV50\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(server_cb, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    state = AVRState()
    avr = AVRConnection(
        host="127.0.0.1",
        port=port,
        on_response=on_response,
        on_disconnect=lambda: None,
        avr_state=state,
        logger=logging.getLogger("test"),
        on_send_while_disconnected=None,
    )
    try:
        await avr.connect()
        await asyncio.sleep(0.2)
        assert "PWON" in responses
        assert "MV50" in responses
        assert "MSQUICK ?" not in responses
        assert state.power == "ON"
        assert state.volume == "50"
    finally:
        avr.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_avr_connection_read_loop_updates_volume_max_from_mvmax():
    """When AVR sends MVMAX60, state.volume_max is set to 60."""
    responses = []

    def on_response(msg: str) -> None:
        responses.append(msg)

    async def server_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        writer.write(b"MVMAX60\r\nMV50\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(server_cb, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    state = AVRState()
    avr = AVRConnection(
        host="127.0.0.1",
        port=port,
        on_response=on_response,
        on_disconnect=lambda: None,
        avr_state=state,
        logger=logging.getLogger("test"),
        on_send_while_disconnected=None,
    )
    try:
        await avr.connect()
        await asyncio.sleep(0.2)
        assert state.volume_max == 60.0
    finally:
        avr.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_avr_connection_send_command_when_connected():
    """send_command when connected writes to writer and returns True."""

    async def server_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        data = await reader.read(64)
        received.append(data)
        writer.close()
        await writer.wait_closed()

    received = []
    server = await asyncio.start_server(server_cb, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    state = AVRState()
    avr = AVRConnection(
        host="127.0.0.1",
        port=port,
        on_response=lambda _: None,
        on_disconnect=lambda: None,
        avr_state=state,
        logger=logging.getLogger("test"),
        on_send_while_disconnected=None,
    )
    try:
        await avr.connect()
        await asyncio.sleep(0.05)
        result = await avr.send_command("PW?")
        assert result is True
        await asyncio.sleep(0.05)
        assert any(b"PW?" in d for d in received)
    finally:
        avr.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_avr_connection_request_state_sends_commands():
    """request_state sends PW?, MV?, etc. to the AVR."""
    received = []

    async def server_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        while True:
            data = await reader.read(256)
            if not data:
                break
            received.append(data)
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(server_cb, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    state = AVRState()
    avr = AVRConnection(
        host="127.0.0.1",
        port=port,
        on_response=lambda _: None,
        on_disconnect=lambda: None,
        avr_state=state,
        logger=logging.getLogger("test"),
        on_send_while_disconnected=None,
    )
    try:
        await avr.connect()
        await asyncio.sleep(0.05)
        await avr.request_state()
        await asyncio.sleep(0.2)  # allow 8 commands × 0.05s to be sent
        combined = b"".join(received)
        assert b"PW?" in combined
        assert b"MV?" in combined
        assert b"SI?" in combined
    finally:
        avr.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_avr_connection_send_logs_info_when_command_group_configured(caplog: pytest.LogCaptureFixture):
    """Sent to AVR is INFO when log_command_groups_info includes the command's group."""

    async def server_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        await reader.read(64)
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(server_cb, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    state = AVRState()
    logger = logging.getLogger("test.avr_physical.sent_info")
    avr = AVRConnection(
        host="127.0.0.1",
        port=port,
        on_response=lambda _: None,
        on_disconnect=lambda: None,
        avr_state=state,
        logger=logger,
        on_send_while_disconnected=None,
        config=Config.model_validate({"log_command_groups_info": ["power"]}),
    )
    try:
        await avr.connect()
        await asyncio.sleep(0.05)
        with caplog.at_level(logging.INFO, logger=logger.name):
            await avr.send_command("PWSTANDBY")
        assert any(r.levelno == logging.INFO and "Sent to AVR: PWSTANDBY" in r.message for r in caplog.records)
    finally:
        avr.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_avr_connection_connect_idempotent_when_already_connected():
    """connect() when already connected returns True without opening again."""

    async def server_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        await asyncio.sleep(0.5)

    server = await asyncio.start_server(server_cb, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    avr, _s, _r, _d = _make_avr("127.0.0.1", port)
    try:
        r1 = await avr.connect()
        r2 = await avr.connect()
        assert r1 is True and r2 is True
        assert avr.is_connected() is True
    finally:
        avr.close()
        server.close()
        await server.wait_closed()
