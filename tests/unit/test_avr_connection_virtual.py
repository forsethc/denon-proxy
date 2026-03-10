"""Unit tests: VirtualAVRConnection (avr_connection) with mock callback."""

from __future__ import annotations

import logging
import pytest

from denon_proxy.avr.connection import VirtualAVRConnection
from denon_proxy.avr.state import AVRState


@pytest.mark.asyncio
async def test_virtual_avr_request_state_pushes_status_dump_lines():
    """
    request_state() pushes each line from AVRState.get_status_dump() via on_response,
    so the proxy receives the full status (PW, ZM, MV, SI, MU, MS, MSSMART) for sync.
    """
    state = AVRState()
    state.power = "STANDBY"
    state.volume = "45"
    state.input_source = "HDMI1"
    state.mute = True
    state.sound_mode = "DOLBY DIGITAL"
    state.smart_select = "SMART1"

    recorded: list[str] = []

    def on_response(msg: str) -> None:
        recorded.append(msg)

    def on_disconnect() -> None:
        pass

    logger = logging.getLogger("test.avr_virtual")
    avr = VirtualAVRConnection(
        avr_state=state,
        on_response=on_response,
        on_disconnect=on_disconnect,
        logger=logger,
        volume_step=0.5,
    )
    await avr.connect()
    await avr.request_state()

    expected = [
        line.strip()
        for line in state.get_status_dump().strip().splitlines()
        if line.strip()
    ]
    assert recorded == expected, (
        f"on_response should be called with lines from get_status_dump(); "
        f"got {recorded!r}, expected {expected!r}"
    )
    # Sanity: we expect PW, ZM, MV, SI, MU, MS, and optionally MSSMART
    assert any(line.startswith("PW") for line in recorded)
    assert any(line.startswith("ZM") for line in recorded)
    assert any(line.startswith("MV") for line in recorded)
    assert any(line.startswith("SI") for line in recorded)
    assert any(line.startswith("MU") for line in recorded)
    assert any(line.startswith("MS") for line in recorded)
    assert any(line.startswith("MSSMART") for line in recorded)


@pytest.mark.asyncio
async def test_virtual_avr_send_command_when_not_connected_returns_false():
    """send_command returns False when _connected is False."""
    state = AVRState()
    recorded = []

    def on_response(msg: str) -> None:
        recorded.append(msg)

    logger = logging.getLogger("test.avr_virtual")
    avr = VirtualAVRConnection(
        avr_state=state,
        on_response=on_response,
        on_disconnect=lambda: None,
        logger=logger,
        volume_step=0.5,
    )
    result = await avr.send_command("PWON")
    assert result is False
    assert not recorded


@pytest.mark.asyncio
async def test_virtual_avr_send_command_empty_or_short_returns_true():
    """send_command with empty or len<2 returns True without updating state."""
    state = AVRState()
    state.power = "STANDBY"
    recorded = []

    def on_response(msg: str) -> None:
        recorded.append(msg)

    logger = logging.getLogger("test.avr_virtual")
    avr = VirtualAVRConnection(
        avr_state=state,
        on_response=on_response,
        on_disconnect=lambda: None,
        logger=logger,
        volume_step=0.5,
    )
    await avr.connect()
    r1 = await avr.send_command("")
    r2 = await avr.send_command("P")
    assert r1 is True and r2 is True
    assert state.power == "STANDBY"
    assert not recorded


@pytest.mark.asyncio
async def test_virtual_avr_send_command_pwon_emits_pw_and_zm():
    """send_command('PWON') emits both PW and ZM lines (PW/ZM cross-match branch)."""
    state = AVRState()
    state.power = "STANDBY"
    recorded = []

    def on_response(msg: str) -> None:
        recorded.append(msg)

    logger = logging.getLogger("test.avr_virtual")
    avr = VirtualAVRConnection(
        avr_state=state,
        on_response=on_response,
        on_disconnect=lambda: None,
        logger=logger,
        volume_step=0.5,
    )
    await avr.connect()
    await avr.send_command("PWON")
    assert "PWON" in recorded
    assert "ZMON" in recorded
    assert state.power == "ON"


@pytest.mark.asyncio
async def test_virtual_avr_request_state_when_not_connected_does_nothing():
    """request_state when not connected returns without calling on_response."""
    state = AVRState()
    recorded = []

    def on_response(msg: str) -> None:
        recorded.append(msg)

    logger = logging.getLogger("test.avr_virtual")
    avr = VirtualAVRConnection(
        avr_state=state,
        on_response=on_response,
        on_disconnect=lambda: None,
        logger=logger,
        volume_step=0.5,
    )
    await avr.request_state()
    assert not recorded
