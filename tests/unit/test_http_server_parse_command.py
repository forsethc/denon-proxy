"""Unit tests for parse_command_request (POST /api/command body validation)."""

from __future__ import annotations

import pytest

from http_server import parse_command_request


def test_parse_command_request_valid_returns_command_and_none():
    """Valid body with command string returns (command, None)."""
    cmd, err = parse_command_request(b'{"command": "PWON"}')
    assert cmd == "PWON"
    assert err is None

    cmd, err = parse_command_request(b'{"command": "MV50"}')
    assert cmd == "MV50"
    assert err is None

    cmd, err = parse_command_request(b'{"command": "  SIHDMI1  "}')
    assert cmd == "SIHDMI1"
    assert err is None


def test_parse_command_request_invalid_json_returns_none_and_error_dict():
    """Invalid JSON returns (None, {"error": "Invalid JSON: ..."})."""
    cmd, err = parse_command_request(b"not json")
    assert cmd is None
    assert err is not None
    assert "error" in err
    assert "Invalid JSON" in err["error"]

    cmd, err = parse_command_request(b"{")
    assert cmd is None
    assert err is not None
    assert "Invalid JSON" in err["error"]


def test_parse_command_request_missing_command_returns_error():
    """Missing or non-string command returns (None, error_dict)."""
    for body in (b"{}", b'{"other": "x"}', b'{"command": null}', b'{"command": 123}'):
        cmd, err = parse_command_request(body)
        assert cmd is None
        assert err == {"error": "Body must be JSON object with 'command' string"}


def test_parse_command_request_empty_string_command_returns_error():
    """Empty string command returns (None, error_dict)."""
    cmd, err = parse_command_request(b'{"command": ""}')
    assert cmd is None
    assert err == {"error": "Body must be JSON object with 'command' string"}


def test_parse_command_request_too_short_returns_error():
    """Command with length < 2 after strip returns (None, error_dict)."""
    cmd, err = parse_command_request(b'{"command": "P"}')
    assert cmd is None
    assert err == {"error": "Command too short"}

    cmd, err = parse_command_request(b'{"command": " "}')
    assert cmd is None
    assert err == {"error": "Command too short"}


def test_parse_command_request_empty_body_treated_as_empty_object():
    """Empty or whitespace body is treated as {} -> missing command."""
    cmd, err = parse_command_request(b"")
    assert cmd is None
    assert err == {"error": "Body must be JSON object with 'command' string"}

    cmd, err = parse_command_request(b"   ")
    assert cmd is None
    assert err == {"error": "Body must be JSON object with 'command' string"}


def test_parse_command_request_non_dict_payload_returns_error():
    """Payload that is not a JSON object (e.g. array) returns error."""
    cmd, err = parse_command_request(b'["command", "PWON"]')
    assert cmd is None
    assert err == {"error": "Body must be JSON object with 'command' string"}
