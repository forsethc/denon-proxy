"""Unit tests: virtual sources (a real input + an input mode, e.g. Apple TV with analog audio).

Everything here runs against fakes or the in-process virtual AVR; no network I/O.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from pydantic import ValidationError

from denon_proxy.avr.connection import VirtualAVRConnection
from denon_proxy.avr.discovery import resolve_sources
from denon_proxy.avr.info import AVRInfo
from denon_proxy.avr.state import AVRState, InputVariant
from denon_proxy.proxy.core import ClientHandler, DenonProxyServer, avr_response_broadcast_lines, send_expanded
from denon_proxy.runtime.config import Config
from denon_proxy.runtime.state import RuntimeState

ATV = "MPLAY_ANALOG"
VARIANTS = {ATV: InputVariant("MPLAY", "ANALOG", "AUTO")}
VIRTUAL_SOURCES_CONFIG = {ATV: {"input": "MPLAY", "input_mode": "ANALOG"}}


@pytest.fixture(autouse=True)
def _no_expanded_command_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("denon_proxy.proxy.core.EXPANDED_COMMAND_DELAY", 0)


def make_state(input_source: str = "MPLAY", input_mode: str | None = "AUTO") -> AVRState:
    """State with the Apple TV virtual source, on input_source in input_mode (None = not reported yet)."""
    state = AVRState(input_source=input_source)
    state.set_variants(VARIANTS)
    if input_mode is not None:
        state.update_from_message(f"SD{input_mode}")
    return state


def _avr_info(*raw_sources: tuple[str, str]) -> AVRInfo:
    return AVRInfo(
        manufacturer=None, model_name=None, serial_number=None, raw_friendly_name=None, raw_sources=list(raw_sources)
    )


class RecordingAVR:
    """Fake AVR connection: records sent commands; sends fail once `fail_on` is reached."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.sent: list[str] = []
        self.fail_on = fail_on

    def is_connected(self) -> bool:
        return True

    async def send_command(self, command: str) -> bool:
        self.sent.append(command)
        return command != self.fail_on


# --- config -------------------------------------------------------------------------------------


def test_config_normalizes_virtual_sources():
    cfg = Config.model_validate(
        {"virtual_sources": {"mplay_analog": {"input": " mplay ", "input_mode": "analog", "name": " ATV "}}}
    )
    vs = cfg.virtual_sources[ATV]
    assert (vs.input, vs.input_mode, vs.default_input_mode, vs.name) == ("MPLAY", "ANALOG", "AUTO", "ATV")


def test_config_virtual_sources_default_empty():
    assert Config.model_validate({}).virtual_sources == {}


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param({"BAD CODE": {"input": "MPLAY", "input_mode": "ANALOG"}}, id="space-in-code"),
        pytest.param({"X?": {"input": "MPLAY", "input_mode": "ANALOG"}}, id="query-in-code"),
        pytest.param({"X": {"input": "MPLAY", "input_mode": "AUTO"}}, id="mode-equals-default-mode"),
        pytest.param({"X": {"input": "", "input_mode": "ANALOG"}}, id="empty-input"),
        pytest.param({"X": {"input": "MPLAY", "input_mode": "AN ALOG"}}, id="space-in-mode"),
        pytest.param({"X": {"input": "MPLAY", "input_mode": "ANALOG", "bogus": 1}}, id="unknown-key"),
        pytest.param(
            {"x": {"input": "MPLAY", "input_mode": "ANALOG"}, "X": {"input": "BD", "input_mode": "ANALOG"}},
            id="duplicate-code-after-upper-casing",
        ),
        pytest.param(
            {"A": {"input": "MPLAY", "input_mode": "ANALOG"}, "B": {"input": "mplay", "input_mode": "analog"}},
            id="same-input-and-mode-twice",
        ),
        pytest.param(
            {
                "A": {"input": "MPLAY", "input_mode": "ANALOG"},
                "B": {"input": "MPLAY", "input_mode": "DIGITAL", "default_input_mode": "HDMI"},
            },
            id="same-input-different-default-mode",
        ),
    ],
)
def test_config_rejects_invalid_virtual_sources(raw):
    with pytest.raises(ValidationError):
        Config.model_validate({"virtual_sources": raw})


@pytest.mark.parametrize(
    ("virtual_sources", "message"),
    [
        pytest.param({"X": {"input": "GAME", "input_mode": "ANALOG"}}, "is not in sources", id="input-not-in-sources"),
        pytest.param({"bd": {"input": "MPLAY", "input_mode": "ANALOG"}}, "same code", id="code-clashes-with-source"),
    ],
)
def test_config_checks_virtual_sources_against_configured_sources(virtual_sources, message):
    with pytest.raises(ValidationError, match=message):
        Config.model_validate({"sources": {"MPLAY": "Apple TV", "BD": "PS5"}, "virtual_sources": virtual_sources})


def test_config_accepts_several_virtual_sources_on_one_input():
    cfg = Config.model_validate(
        {
            "virtual_sources": {
                ATV: {"input": "MPLAY", "input_mode": "ANALOG"},
                "MPLAY_DIGITAL": {"input": "MPLAY", "input_mode": "DIGITAL"},
            }
        }
    )
    assert set(cfg.virtual_sources) == {ATV, "MPLAY_DIGITAL"}


def test_config_accepts_virtual_source_on_configured_source_case_insensitively():
    cfg = Config.model_validate({"sources": {"mplay": "Apple TV"}, "virtual_sources": VIRTUAL_SOURCES_CONFIG})
    assert ATV in cfg.virtual_sources


# --- source list --------------------------------------------------------------------------------


def test_resolve_sources_appends_virtual_sources_after_real_ones():
    cfg = Config.model_validate(
        {
            "sources": {"MPLAY": "Apple TV", "BD": "Playstation"},
            "virtual_sources": {
                ATV: {"input": "MPLAY", "input_mode": "ANALOG"},
                "BD_DIGITAL": {"input": "BD", "input_mode": "DIGITAL", "name": "PS5 Optical"},
            },
        }
    )
    assert resolve_sources(cfg, RuntimeState()) == [
        ("MPLAY", "Apple TV"),
        ("BD", "Playstation"),
        (ATV, "Apple TV (Analog)"),
        ("BD_DIGITAL", "PS5 Optical"),
    ]


def test_resolve_sources_matches_input_case_insensitively():
    cfg = Config.model_validate({"virtual_sources": {"NET_ANALOG": {"input": "Net", "input_mode": "ANALOG"}}})
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo(
        manufacturer=None,
        model_name=None,
        serial_number=None,
        raw_friendly_name=None,
        raw_sources=[("Net", "Streaming")],
    )
    assert ("NET_ANALOG", "Streaming (Analog)") in resolve_sources(cfg, runtime_state)


def test_resolve_sources_skips_virtual_sources_that_dont_fit_the_avr_source_list(caplog):
    """Without a sources mapping the list comes from the AVR, so mismatches are only known at runtime."""
    cfg = Config.model_validate(
        {
            "virtual_sources": {
                "GONE": {"input": "GAME", "input_mode": "ANALOG"},
                "MPLAY": {"input": "MPLAY", "input_mode": "ANALOG"},
            },
        }
    )
    runtime_state = RuntimeState()
    runtime_state.avr_info = _avr_info(("MPLAY", "Apple TV"))
    with caplog.at_level(logging.WARNING):
        assert resolve_sources(cfg, runtime_state) == [("MPLAY", "Apple TV")]
    assert "'GONE'" in caplog.text
    assert "same code as a real input" in caplog.text


def test_resolve_sources_skips_virtual_source_when_configured_input_is_missing_on_avr(caplog):
    cfg = Config.model_validate(
        {"sources": {"MPLAY": "Apple TV", "BD": "Playstation"}, "virtual_sources": VIRTUAL_SOURCES_CONFIG}
    )
    runtime_state = RuntimeState()
    runtime_state.avr_info = _avr_info(("BD", "Blu-ray"))
    with caplog.at_level(logging.WARNING):
        assert resolve_sources(cfg, runtime_state) == [("BD", "Playstation")]
    assert f"'{ATV}'" in caplog.text


# --- state: presenting the virtual source -------------------------------------------------------


def test_input_and_mode_responses_select_virtual_source():
    state = make_state(input_source="BD", input_mode=None)
    state.update_from_message("SIMPLAY")
    assert state.input_source == "MPLAY"
    state.update_from_message("SDANALOG")
    assert state.input_source == ATV
    assert "SIMPLAY_ANALOG" in state.get_status_dump()
    assert "SIMPLAY\r\n" in state.get_status_dump(as_avr=True)
    state.update_from_message("SDAUTO")
    assert state.input_source == "MPLAY"


def test_input_change_forgets_mode_of_previous_input():
    state = make_state(input_mode="ANALOG")
    assert state.input_source == ATV
    state.update_from_message("SIBD")
    assert (state.input_source, state.input_mode) == ("BD", None)
    state.update_from_message("SDANALOG")
    assert state.input_source == "BD"  # BD has no virtual source


def test_same_input_response_keeps_known_mode():
    state = make_state(input_mode="ANALOG")
    state.update_from_message("SIMPLAY")
    assert state.input_source == ATV


def test_apply_command_virtual_code_then_plain_input():
    state = make_state(input_source="BD")
    assert state.apply_command("SIMPLAY_ANALOG", volume_step=0.5)
    assert (state.input_source, state.input_mode) == (ATV, "ANALOG")
    assert state.apply_command("SIMPLAY", volume_step=0.5)
    assert (state.input_source, state.input_mode) == ("MPLAY", "AUTO")
    assert state.apply_command("SDANALOG", volume_step=0.5)
    assert state.input_source == ATV
    assert state.apply_command("SIBD", volume_step=0.5)
    assert state.apply_command("SIMPLAY", volume_step=0.5)
    assert (state.input_source, state.input_mode) == ("MPLAY", "AUTO")


def test_snapshot_restore_roundtrip():
    state = make_state(input_mode="ANALOG")
    snap = state.snapshot()
    state.apply_command("SIBD", volume_step=0.5)
    state.restore(snap)
    assert (state.input_source, state.input_mode) == (ATV, "ANALOG")
    state.update_from_message("SIMPLAY")  # real input was restored too, so the mode is kept
    assert state.input_source == ATV


def test_apply_payload_input_source_sets_real_input():
    state = make_state(input_mode="ANALOG")
    state.apply_payload({"input_source": "BD"})
    assert (state.input_source, state.input_mode) == ("BD", None)


def test_without_virtual_sources_behaviour_is_unchanged():
    state = AVRState()
    state.update_from_message("SIHDMI1")
    state.update_from_message("SDANALOG")
    assert state.input_source == "HDMI1"
    assert not state.has_variants()
    assert not state.needs_input_mode_query()
    assert state.expand_command("SIHDMI1") == ["SIHDMI1"]


def test_needs_input_mode_query_only_for_inputs_with_virtual_sources():
    state = make_state(input_source="BD", input_mode=None)
    assert not state.needs_input_mode_query()
    state.update_from_message("SIMPLAY")
    assert state.needs_input_mode_query()
    state.update_from_message("SDANALOG")
    assert not state.needs_input_mode_query()


# --- command expansion --------------------------------------------------------------------------


def test_expand_virtual_code_selects_input_then_mode():
    assert make_state(input_source="BD").expand_command("SIMPLAY_ANALOG") == ["SIMPLAY", "SDANALOG"]
    assert make_state().expand_command("siMPLAY_analog") == ["SIMPLAY", "SDANALOG"]


def test_expand_plain_input_sets_default_mode_unless_already_in_it():
    assert make_state(input_mode="ANALOG").expand_command("SIMPLAY") == ["SIMPLAY", "SDAUTO"]
    assert make_state(input_mode=None).expand_command("SIMPLAY") == ["SIMPLAY", "SDAUTO"]
    assert make_state(input_mode="AUTO").expand_command("SIMPLAY") == ["SIMPLAY"]


def test_expand_plain_input_from_another_input_sets_default_mode():
    """The AVR remembers each input's mode: coming back via another input must not bring analog back."""
    state = make_state(input_mode="ANALOG")
    assert state.expand_command("SIBD") == ["SIBD"]  # inputs without virtual sources are left alone
    state.apply_command("SIBD", volume_step=0.5)
    state.update_from_message("SDHDMI")
    assert state.expand_command("SIMPLAY") == ["SIMPLAY", "SDAUTO"]


def test_expand_plain_input_uses_default_of_the_input_being_selected():
    """Leaving one virtual source for another input's plain source applies that input's default."""
    state = AVRState(input_source="MPLAY")
    state.set_variants({**VARIANTS, "BD_ANALOG": InputVariant("BD", "ANALOG", "HDMI")})
    state.update_from_message("SDANALOG")
    assert state.input_source == ATV
    assert state.expand_command("SIBD") == ["SIBD", "SDHDMI"]
    state.apply_command("SIBD", volume_step=0.5)
    assert (state.input_source, state.input_mode) == ("BD", "HDMI")


@pytest.mark.parametrize("cmd", ["SI?", "SD?", "SDHDMI", "MV50", "PWON", "SI"])
def test_expand_passes_other_commands_through(cmd):
    assert make_state(input_mode="ANALOG").expand_command(cmd) == [cmd]


@pytest.mark.asyncio
async def test_send_expanded_sends_in_order():
    avr = RecordingAVR()
    assert await send_expanded(avr, ["SIMPLAY", "SDANALOG"]) is True
    assert avr.sent == ["SIMPLAY", "SDANALOG"]


@pytest.mark.asyncio
async def test_send_expanded_stops_after_failed_input_switch():
    """SD after a failed SI would change the mode of whatever input the AVR is still on."""
    avr = RecordingAVR(fail_on="SIMPLAY")
    assert await send_expanded(avr, ["SIMPLAY", "SDANALOG"]) is False
    assert avr.sent == ["SIMPLAY"]


# --- broadcast to clients -----------------------------------------------------------------------


def test_broadcast_presents_virtual_source():
    state = make_state(input_mode="ANALOG")
    assert avr_response_broadcast_lines("SIMPLAY", state) == ["SIMPLAY_ANALOG"]
    state.update_from_message("SDAUTO")
    assert avr_response_broadcast_lines("SDAUTO", state) == ["SDAUTO", "SIMPLAY"]


def test_broadcast_is_faithful_otherwise():
    plain = AVRState()
    assert avr_response_broadcast_lines("SIHDMI1", plain) == ["SIHDMI1"]
    assert avr_response_broadcast_lines("SDANALOG", plain) == ["SDANALOG"]
    state = make_state(input_source="BD", input_mode="ANALOG")
    assert avr_response_broadcast_lines("SIBD", state) == ["SIBD"]
    assert avr_response_broadcast_lines("SDANALOG", state) == ["SDANALOG"]
    assert avr_response_broadcast_lines("MV50", state) == ["MV50"]


# --- ClientHandler / DenonProxyServer wiring ----------------------------------------------------


def _client(avr: RecordingAVR, state: AVRState, broadcasts: list[str]) -> ClientHandler:
    config = {"optimistic_state": True, "volume_step": 0.5, "optimistic_broadcast_delay": 0}
    return ClientHandler(
        avr,  # type: ignore[arg-type]
        state,
        set(),
        logging.getLogger("test.virtual_sources"),
        RuntimeState(),
        config,  # type: ignore[arg-type]
        broadcast_to_all=broadcasts.append,
    )


@pytest.mark.asyncio
async def test_client_selecting_virtual_source_sends_both_commands_and_broadcasts_it():
    avr, state, broadcasts = RecordingAVR(), make_state(input_source="BD"), []
    await _client(avr, state, broadcasts)._handle_command_async("SIMPLAY_ANALOG")
    assert avr.sent == ["SIMPLAY", "SDANALOG"]
    assert state.input_source == ATV
    assert "SIMPLAY_ANALOG" in broadcasts


@pytest.mark.asyncio
async def test_client_optimistic_state_reverts_when_input_switch_fails():
    avr, state = RecordingAVR(fail_on="SIMPLAY"), make_state(input_source="BD")
    await _client(avr, state, [])._handle_command_async("SIMPLAY_ANALOG")
    assert avr.sent == ["SIMPLAY"]
    assert state.input_source == "BD"


def _server() -> DenonProxyServer:
    config = Config.model_validate({"virtual_sources": VIRTUAL_SOURCES_CONFIG})

    def unused_factory(*_args, **_kwargs):
        raise NotImplementedError

    return DenonProxyServer(config, logging.getLogger("test.virtual_sources"), unused_factory, RuntimeState())


@pytest.mark.asyncio
async def test_server_queries_input_mode_once_after_switching_to_input_with_virtual_source():
    server = _server()
    avr = RecordingAVR()
    server.avr = avr  # type: ignore[assignment]

    server.avr_state.update_from_message("SIMPLAY")  # the connection updates state before on_response
    server._on_avr_response("SIMPLAY")
    server._on_avr_response("MV50")  # other lines never trigger the query
    await asyncio.sleep(0)
    assert avr.sent == ["SD?"]

    server.avr_state.update_from_message("SIBD")
    server._on_avr_response("SIBD")
    await asyncio.sleep(0)
    assert avr.sent == ["SD?"]  # BD has no virtual source


# --- end to end against the in-process virtual AVR ----------------------------------------------


@pytest.mark.asyncio
async def test_virtual_avr_round_trip_reports_virtual_source():
    state = make_state(input_source="BD", input_mode=None)
    lines: list[str] = []

    def on_response(msg: str) -> None:
        lines.extend(avr_response_broadcast_lines(msg, state))

    avr = VirtualAVRConnection(state, on_response, lambda: None, logging.getLogger("test"), 0.5)
    await avr.connect()

    await send_expanded(avr, state.expand_command("SIMPLAY_ANALOG"))
    assert state.input_source == ATV
    assert lines[-1] == "SIMPLAY_ANALOG"

    await send_expanded(avr, state.expand_command("SIMPLAY"))
    assert (state.input_source, state.input_mode) == ("MPLAY", "AUTO")
    assert lines[-1] == "SIMPLAY"
