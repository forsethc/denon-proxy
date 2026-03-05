"""Unit tests: _parse_mvmax (avr_connection)."""
from avr_connection import _parse_mvmax


def test_parse_mvmax_valid_and_invalid_inputs():
    assert _parse_mvmax("MAX 60") == 60.0
    assert _parse_mvmax("MAX60") == 60.0
    # No number after MAX -> None
    assert _parse_mvmax("MAX") is None
    # Not starting with MAX -> None
    assert _parse_mvmax("XYZ") is None
    # Lower bound should never be negative
    val = _parse_mvmax("MAX -10")
    assert val is None or val >= 0.0

