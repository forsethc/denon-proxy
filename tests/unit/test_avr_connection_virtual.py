"""Unit tests: VirtualAVRConnection (avr_connection) with mock callback."""

from __future__ import annotations

import logging

import pytest

from avr_connection import VirtualAVRConnection
from avr_state import AVRState


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
        state=state,
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
