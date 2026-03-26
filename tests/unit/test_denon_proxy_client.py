"""Unit tests: ClientHandler behavior (e.g. optimistic state revert on send failure)."""

from __future__ import annotations

import logging

import pytest

from denon_proxy.avr.state import AVRState
from denon_proxy.proxy.core import ClientHandler
from denon_proxy.runtime.state import RuntimeState


@pytest.mark.asyncio
async def test_optimistic_revert_when_send_fails():
    """
    When optimistic_state is True and send_command returns False (and AVR was connected),
    state is reverted to the snapshot so the UI does not stay out of sync.
    """
    state = AVRState()
    state.power = "ON"
    state.volume = "50"

    class FailingAVR:
        def is_connected(self) -> bool:
            return True

        async def send_command(self, _command: str) -> bool:
            return False

    config = {
        "optimistic_state": True,
        "volume_step": 1.0,
        "optimistic_broadcast_delay": 0,
    }
    logger = logging.getLogger("test.client")
    handler = ClientHandler(FailingAVR(), state, set(), logger, RuntimeState(), config)

    await handler._handle_command_async("PWSTANDBY")

    # Optimistic apply would set STANDBY; failed send should revert to snapshot
    assert state.power == "ON"
    assert state.volume == "50"


@pytest.mark.asyncio
async def test_optimistic_broadcast_routes_through_broadcast_to_all():
    """
    When broadcast_to_all is set (production: partial(server._broadcast, source='optimistic')),
    optimistic state updates call it once per status line so logging matches AVR-driven paths.
    """
    state = AVRState()
    state.power = "ON"

    class OkAVR:
        def is_connected(self) -> bool:
            return True

        async def send_command(self, _command: str) -> bool:
            return True

    recorded: list[str] = []

    config = {
        "optimistic_state": True,
        "volume_step": 1.0,
        "optimistic_broadcast_delay": 0,
    }
    logger = logging.getLogger("test.client.broadcast_cb")
    handler = ClientHandler(
        OkAVR(),
        state,
        set(),
        logger,
        RuntimeState(),
        config,
        broadcast_to_all=recorded.append,
    )

    await handler._handle_command_async("PWSTANDBY")

    assert state.power == "STANDBY"
    # Same lines as get_status_dump() after standby
    dump_lines = [ln.strip() for ln in state.get_status_dump().strip().splitlines() if ln.strip()]
    assert recorded == dump_lines
