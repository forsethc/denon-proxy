"""Unit tests: create_avr_connection factory (avr_connection)."""
from avr_connection import AVRConnection, VirtualAVRConnection, create_avr_connection
from avr_state import AVRState
import pytest


def _make_callbacks():
    state = AVRState()
    recorded = []

    def on_response(msg: str):
        recorded.append(msg)

    def on_disconnect():
        pass

    return state, on_response, on_disconnect, recorded


def test_create_avr_connection_returns_physical_when_avr_host_set():
    """When avr_host is set, returns AVRConnection."""
    pytest.fail("test branch protection")
    state, on_resp, on_disc, _ = _make_callbacks()
    config = {"avr_host": "192.168.1.100", "avr_port": 23}
    import logging
    conn = create_avr_connection(config, state, on_resp, on_disc, logging.getLogger("test"))
    assert isinstance(conn, AVRConnection)
    assert conn.host == "192.168.1.100"
    assert conn.port == 23
    assert conn.is_connected() is False
    assert conn.get_details() == {"type": "physical", "host": "192.168.1.100", "port": 23}


def test_create_avr_connection_returns_virtual_when_avr_host_empty():
    """When avr_host is empty, returns VirtualAVRConnection."""
    state, on_resp, on_disc, _ = _make_callbacks()
    config = {"avr_host": "", "volume_step": 0.5}
    import logging
    conn = create_avr_connection(config, state, on_resp, on_disc, logging.getLogger("test"))
    assert isinstance(conn, VirtualAVRConnection)
    assert conn.get_details() == {"type": "virtual"}
