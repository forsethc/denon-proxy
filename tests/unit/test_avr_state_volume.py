import math

from avr_state import volume_to_level, volume_to_db


def test_volume_to_level_basic_and_half_steps():
    # Integer volume string
    assert volume_to_level("50") == 50.0
    # Half-step encoded as three digits
    assert volume_to_level("535") == 53.5


def test_volume_to_level_empty_and_max_clamp():
    # Empty/None should fall back to default level
    default_level = volume_to_level(None)
    assert default_level > 0
    # MAX with explicit value should clamp to that value (or max_volume)
    assert volume_to_level("MAX 60", max_volume=80.0) == 60.0
    # Values above max_volume are clamped (e.g. 990 -> 99.0, then clamped to 80.0)
    assert volume_to_level("990", max_volume=80.0) == 80.0


def test_volume_to_db_roundtrip_shape():
    # 80 is the reference level (≈ 0 dB)
    assert volume_to_db("80") == "0.0"
    # Lower level should be negative dB, higher positive
    low_db = float(volume_to_db("50"))
    high_db = float(volume_to_db("90"))
    assert low_db < 0.0
    assert high_db > 0.0
