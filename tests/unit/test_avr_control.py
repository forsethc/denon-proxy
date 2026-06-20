"""Unit tests for avr_control.route_to_command and normalize_source_cmd_name."""

from __future__ import annotations

import pytest

from denon_proxy.avr.state import AVRState
from denon_proxy.http.avr_control import (
    _parse_query_param,
    normalize_source_cmd_name,
    route_to_command,
)

SOURCES = [
    ("CD", "CD"),
    ("HDMI1", "HDMI 1"),
    ("HDMI2", "HDMI 2"),
    ("SAT/CBL", "CBL/SAT"),
    ("USB/IPOD", "iPod/USB"),
    ("MPLAY", "Media Player"),
]


def _state(**kwargs: object) -> AVRState:
    s = AVRState()
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action,expected_cmd",
    [
        ("on", "PWON"),
        ("off", "PWSTANDBY"),
    ],
)
def test_power_on_off(action: str, expected_cmd: str) -> None:
    cmd, resp, status = route_to_command(f"/api/avr/power/{action}", "", _state(), SOURCES)
    assert status == 200
    assert cmd == expected_cmd
    assert resp == {"ok": True, "command": expected_cmd}


def test_power_toggle_when_on() -> None:
    cmd, resp, status = route_to_command("/api/avr/power/toggle", "", _state(power="ON"), SOURCES)
    assert status == 200
    assert cmd == "PWSTANDBY"


def test_power_toggle_when_standby() -> None:
    cmd, resp, status = route_to_command("/api/avr/power/toggle", "", _state(power="STANDBY"), SOURCES)
    assert status == 200
    assert cmd == "PWON"


def test_power_toggle_when_unknown() -> None:
    # Any non-ON power state flips to ON
    cmd, resp, status = route_to_command("/api/avr/power/toggle", "", _state(power="OFF"), SOURCES)
    assert status == 200
    assert cmd == "PWON"


def test_power_unknown_action_returns_404() -> None:
    cmd, resp, status = route_to_command("/api/avr/power/reboot", "", _state(), SOURCES)
    assert status == 404
    assert cmd is None
    assert "error" in resp


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


def test_volume_up() -> None:
    cmd, resp, status = route_to_command("/api/avr/volume/up", "", _state(), SOURCES)
    assert status == 200
    assert cmd == "MVUP"


def test_volume_down() -> None:
    cmd, resp, status = route_to_command("/api/avr/volume/down", "", _state(), SOURCES)
    assert status == 200
    assert cmd == "MVDOWN"


@pytest.mark.parametrize(
    "level_param,expected_cmd",
    [
        ("45", "MV45"),
        ("45.5", "MV455"),
        ("50.0", "MV50"),
        ("0", "MV0"),
        ("98", "MV98"),
    ],
)
def test_volume_set_valid(level_param: str, expected_cmd: str) -> None:
    cmd, resp, status = route_to_command("/api/avr/volume/set", f"level={level_param}", _state(), SOURCES)
    assert status == 200
    assert cmd == expected_cmd


def test_volume_set_rounds_to_half_step() -> None:
    # 45.3 should round to 45.5
    cmd, resp, status = route_to_command("/api/avr/volume/set", "level=45.3", _state(), SOURCES)
    assert status == 200
    assert cmd == "MV455"


def test_volume_set_missing_level_returns_400() -> None:
    cmd, resp, status = route_to_command("/api/avr/volume/set", "", _state(), SOURCES)
    assert status == 400
    assert cmd is None


def test_volume_set_non_numeric_level_returns_400() -> None:
    cmd, resp, status = route_to_command("/api/avr/volume/set", "level=abc", _state(), SOURCES)
    assert status == 400
    assert cmd is None


def test_volume_set_out_of_range_returns_400() -> None:
    cmd, resp, status = route_to_command("/api/avr/volume/set", "level=99", _state(volume_max=98.0), SOURCES)
    assert status == 400
    assert cmd is None


def test_volume_set_negative_returns_400() -> None:
    cmd, resp, status = route_to_command("/api/avr/volume/set", "level=-1", _state(), SOURCES)
    assert status == 400
    assert cmd is None


def test_volume_unknown_action_returns_404() -> None:
    cmd, resp, status = route_to_command("/api/avr/volume/mute", "", _state(), SOURCES)
    assert status == 404
    assert cmd is None


# ---------------------------------------------------------------------------
# Mute
# ---------------------------------------------------------------------------


def test_mute_on() -> None:
    cmd, resp, status = route_to_command("/api/avr/mute/on", "", _state(), SOURCES)
    assert status == 200
    assert cmd == "MUON"


def test_mute_off() -> None:
    cmd, resp, status = route_to_command("/api/avr/mute/off", "", _state(), SOURCES)
    assert status == 200
    assert cmd == "MUOFF"


def test_mute_toggle_when_muted() -> None:
    cmd, resp, status = route_to_command("/api/avr/mute/toggle", "", _state(mute=True), SOURCES)
    assert status == 200
    assert cmd == "MUOFF"


def test_mute_toggle_when_unmuted() -> None:
    cmd, resp, status = route_to_command("/api/avr/mute/toggle", "", _state(mute=False), SOURCES)
    assert status == 200
    assert cmd == "MUON"


def test_mute_unknown_action_returns_404() -> None:
    cmd, resp, status = route_to_command("/api/avr/mute/flip", "", _state(), SOURCES)
    assert status == 404
    assert cmd is None


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


def test_source_known_code() -> None:
    cmd, resp, status = route_to_command("/api/avr/source/CD", "", _state(), SOURCES)
    assert status == 200
    assert cmd == "SICD"


def test_source_case_insensitive() -> None:
    cmd, resp, status = route_to_command("/api/avr/source/hdmi1", "", _state(), SOURCES)
    assert status == 200
    assert cmd == "SIHDMI1"


def test_source_with_slash_in_code() -> None:
    cmd, resp, status = route_to_command("/api/avr/source/SAT/CBL", "", _state(), SOURCES)
    # Note: path is split on /; the trailing /CBL becomes the 5th segment
    # This tests that the source validation accepts partial match IF the full
    # code landed in parts[3]. In practice the path parsing produces parts[3]="SAT"
    # which is NOT in SOURCES, so this should 404. The client should URL-encode
    # the slash as %2F to pass it in the URL.
    # Verify we get a 404 for unencoded slash (ambiguous URL).
    assert status == 404


def test_source_unknown_returns_404() -> None:
    cmd, resp, status = route_to_command("/api/avr/source/BLURAY", "", _state(), SOURCES)
    assert status == 404
    assert cmd is None
    assert "BLURAY" in resp["error"]


# ---------------------------------------------------------------------------
# Unknown feature/path
# ---------------------------------------------------------------------------


def test_unknown_feature_returns_404() -> None:
    cmd, resp, status = route_to_command("/api/avr/smartselect/1", "", _state(), SOURCES)
    assert status == 404


def test_too_short_path_returns_404() -> None:
    cmd, resp, status = route_to_command("/api/avr/power", "", _state(), SOURCES)
    assert status == 404


def test_non_avr_prefix_returns_404() -> None:
    cmd, resp, status = route_to_command("/api/other/power/on", "", _state(), SOURCES)
    assert status == 404


# ---------------------------------------------------------------------------
# normalize_source_cmd_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "func_code,expected",
    [
        ("CD", "SRC_CD"),
        ("HDMI1", "SRC_HDMI1"),
        ("SAT/CBL", "SRC_SAT_CBL"),
        ("USB/IPOD", "SRC_USB_IPOD"),
        ("MPLAY", "SRC_MPLAY"),
        ("AUX1", "SRC_AUX1"),
        ("NET", "SRC_NET"),
    ],
)
def test_normalize_source_cmd_name(func_code: str, expected: str) -> None:
    assert normalize_source_cmd_name(func_code) == expected


# ---------------------------------------------------------------------------
# _parse_query_param
# ---------------------------------------------------------------------------


def test_parse_query_param_simple() -> None:
    assert _parse_query_param("level=45", "level") == "45"


def test_parse_query_param_multiple() -> None:
    assert _parse_query_param("foo=1&level=45.5&bar=baz", "level") == "45.5"


def test_parse_query_param_case_insensitive_key() -> None:
    assert _parse_query_param("Level=50", "level") == "50"


def test_parse_query_param_missing_key() -> None:
    assert _parse_query_param("foo=bar", "level") is None


def test_parse_query_param_empty_query() -> None:
    assert _parse_query_param("", "level") is None
