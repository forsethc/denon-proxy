"""Unit tests for AVRInfo: describe, has_sources, raw_sources coercion, udn_serial."""

from denon_proxy.avr.info import AVRInfo

# --- describe() ---


def test_describe_all_parts():
    info = AVRInfo(
        manufacturer="Denon",
        model_name="AVR-X1600H",
        serial_number=None,
        raw_friendly_name="Living Room",
        raw_sources=[],
    )
    assert info.describe() == 'Denon AVR-X1600H "Living Room"'


def test_describe_manufacturer_and_model_only():
    info = AVRInfo(
        manufacturer="Marantz",
        model_name="SR5015",
        serial_number=None,
        raw_friendly_name=None,
        raw_sources=[],
    )
    assert info.describe() == "Marantz SR5015"


def test_describe_unknown_avr_when_all_none_or_empty():
    info = AVRInfo(
        manufacturer=None,
        model_name=None,
        serial_number=None,
        raw_friendly_name=None,
        raw_sources=[],
    )
    assert info.describe() == "Unknown AVR"


def test_describe_strips_whitespace():
    info = AVRInfo(
        manufacturer="  Denon  ",
        model_name=" X1600 ",
        serial_number=None,
        raw_friendly_name="  Room  ",
        raw_sources=[],
    )
    assert info.describe() == 'Denon X1600 "Room"'


# --- has_sources() ---


def test_has_sources_true_when_non_empty():
    info = AVRInfo(
        manufacturer=None,
        model_name=None,
        serial_number=None,
        raw_friendly_name=None,
        raw_sources=[("CD", "CD Player")],
    )
    assert info.has_sources() is True


def test_has_sources_false_when_empty():
    info = AVRInfo(
        manufacturer=None,
        model_name=None,
        serial_number=None,
        raw_friendly_name=None,
        raw_sources=[],
    )
    assert info.has_sources() is False


# --- raw_sources list coercion (__post_init__) ---


def test_raw_sources_tuple_converted_to_list():
    """AVRInfo accepts tuple for raw_sources and stores as list (frozen-friendly)."""
    sources = (("CD", "CD"), ("HDMI1", "HDMI1"))
    info = AVRInfo(
        manufacturer=None,
        model_name=None,
        serial_number=None,
        raw_friendly_name=None,
        raw_sources=sources,
    )
    assert info.raw_sources == [("CD", "CD"), ("HDMI1", "HDMI1")]
    assert isinstance(info.raw_sources, list)


# --- udn_serial ---


def test_udn_serial_uses_serial_number_when_set():
    info = AVRInfo(
        manufacturer="Denon",
        model_name="X1600",
        serial_number="ABC123",
        raw_friendly_name=None,
        raw_sources=[],
    )
    assert info.udn_serial("192.168.1.1") == "ABC123"


def test_udn_serial_fallback_to_proxy_ip_when_serial_empty():
    info = AVRInfo(
        manufacturer="Denon",
        model_name="X1600",
        serial_number=None,
        raw_friendly_name=None,
        raw_sources=[],
    )
    assert info.udn_serial("192.168.1.1") == "proxy-192-168-1-1"


def test_udn_serial_fallback_when_serial_whitespace_only():
    info = AVRInfo(
        manufacturer="Denon",
        model_name="X1600",
        serial_number="   ",
        raw_friendly_name=None,
        raw_sources=[],
    )
    assert info.udn_serial("10.0.0.5") == "proxy-10-0-0-5"


def test_virtual_returns_canonical_placeholder():
    """Virtual mode gets a consistent AVRInfo (Denon Virtual, no serial, no sources)."""
    info = AVRInfo.virtual()
    assert info.manufacturer == "Denon"
    assert info.model_name == "Virtual"
    assert info.serial_number is None
    assert info.raw_friendly_name is None
    assert info.raw_sources == []
    assert info.has_sources() is False
    assert info.describe() == "Denon Virtual"
    assert info.udn_serial("192.168.1.1") == "proxy-192-168-1-1"


def test_virtual_udn_serial_different_ips():
    """Virtual has no device serial; udn_serial always returns proxy-{ip} for any advertise IP."""
    info = AVRInfo.virtual()
    assert info.udn_serial("192.168.1.1") == "proxy-192-168-1-1"
    assert info.udn_serial("10.0.0.5") == "proxy-10-0-0-5"
    assert info.udn_serial("127.0.0.1") == "proxy-127-0-0-1"


def test_virtual_distinct_from_physical_with_no_serial():
    """Virtual placeholder is not equal to a hand-built AVRInfo with same manufacturer/model but no serial."""
    virtual = AVRInfo.virtual()
    same_fields = AVRInfo(
        manufacturer="Denon",
        model_name="Virtual",
        serial_number=None,
        raw_friendly_name=None,
        raw_sources=[],
    )
    assert virtual == same_fields  # same field values => equal
    # Different model_name => not equal
    other = AVRInfo(
        manufacturer="Denon",
        model_name="AVR-X1600H",
        serial_number=None,
        raw_friendly_name=None,
        raw_sources=[],
    )
    assert virtual != other


# --- unknown() (physical AVR configured but sync failed) ---


def test_unknown_returns_placeholder_for_failed_sync():
    """Unknown = physical AVR but identity not discovered (e.g. HTTP sync failed)."""
    info = AVRInfo.unknown()
    assert info.manufacturer == "Denon"
    assert info.model_name is None
    assert info.serial_number is None
    assert info.raw_friendly_name is None
    assert info.raw_sources == []
    assert info.has_sources() is False
    assert info.describe() == "Denon"
    assert info.udn_serial("192.168.1.1") == "proxy-192-168-1-1"


def test_unknown_equality():
    assert AVRInfo.unknown() == AVRInfo.unknown()


def test_unknown_distinct_from_virtual():
    """Unknown (sync failed) is not the same as virtual (no physical AVR)."""
    assert AVRInfo.unknown() != AVRInfo.virtual()


