"""Unit tests for telnet_utils."""

from telnet_utils import parse_telnet_lines, telnet_line_to_bytes


def test_telnet_line_to_bytes():
    assert telnet_line_to_bytes("PWON") == b"PWON\r"
    assert telnet_line_to_bytes("  MV50  ") == b"MV50\r"


def test_parse_telnet_lines_incomplete_line_remains_in_buffer():
    buf = b"PW"
    data = b"ON"
    commands, remaining = parse_telnet_lines(buf, data)
    assert commands == []
    assert remaining == b"PWON"


def test_parse_telnet_lines_single_command():
    commands, remaining = parse_telnet_lines(b"", b"PWON\r\n")
    assert commands == ["PWON"]
    assert remaining == b""


def test_parse_telnet_lines_cr_nl_split_across_chunks():
    commands, remaining = parse_telnet_lines(b"", b"PW")
    assert commands == []
    assert remaining == b"PW"
    commands, remaining = parse_telnet_lines(remaining, b"ON\r\n")
    assert commands == ["PWON"]
    assert remaining == b""


def test_parse_telnet_lines_multiple_commands_one_chunk():
    commands, remaining = parse_telnet_lines(b"", b"PWON\r\nMV50\r\n")
    assert commands == ["PWON", "MV50"]
    assert remaining == b""


def test_parse_telnet_lines_empty_data():
    commands, remaining = parse_telnet_lines(b"", b"")
    assert commands == []
    assert remaining == b""


def test_parse_telnet_lines_mixed_r_and_n():
    # Parser treats both \r and \n as line endings; splits on whichever comes first
    commands, remaining = parse_telnet_lines(b"", b"PWON\nMV50\r")
    assert commands == ["PWON", "MV50"]
    assert remaining == b""


def test_parse_telnet_lines_blank_lines_ignored():
    commands, remaining = parse_telnet_lines(b"", b"\r\nPWON\r\n\r\n")
    assert commands == ["PWON"]
    assert remaining == b""


def test_parse_telnet_lines_skips_invalid_utf8_line():
    """Bytes that are not valid UTF-8 are skipped (UnicodeDecodeError); buffer advances."""
    commands, remaining = parse_telnet_lines(b"", b"\xff\xfe\r\nPWON\r\n")
    assert commands == ["PWON"]
    assert remaining == b""
