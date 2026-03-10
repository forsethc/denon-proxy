"""Unit tests for SSDP helpers: parse_ssdp_search_target, ssdp_response."""

from denon_proxy.avr.info import AVRInfo
from denon_proxy.runtime.state import RuntimeState
from denon_proxy.avr.discovery import _parse_ssdp_search_target as parse_ssdp_search_target, _ssdp_response as ssdp_response


# --- parse_ssdp_search_target ---


def test_parse_ssdp_search_target_denon_urn():
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        "ST: urn:schemas-denon-com:device:AiosDevice:1\r\n"
        "\r\n"
    )
    assert parse_ssdp_search_target(msg) == "urn:schemas-denon-com:device:AiosDevice:1"


def test_parse_ssdp_search_target_ssdp_all():
    msg = "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nST: ssdp:all\r\n\r\n"
    assert parse_ssdp_search_target(msg) == "ssdp:all"


def test_parse_ssdp_search_target_upnp_rootdevice():
    msg = "M-SEARCH * HTTP/1.1\r\nST: upnp:rootdevice\r\n\r\n"
    assert parse_ssdp_search_target(msg) == "upnp:rootdevice"


def test_parse_ssdp_search_target_st_with_spaces():
    msg = "M-SEARCH * HTTP/1.1\r\nST:   urn:schemas-upnp-org:device:MediaRenderer:1  \r\n\r\n"
    assert parse_ssdp_search_target(msg) == "urn:schemas-upnp-org:device:MediaRenderer:1"


def test_parse_ssdp_search_target_case_insensitive():
    msg = "M-SEARCH * HTTP/1.1\r\nst: foo:bar\r\n\r\n"
    assert parse_ssdp_search_target(msg) == "foo:bar"


def test_parse_ssdp_search_target_no_st_returns_none():
    msg = "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n\r\n"
    assert parse_ssdp_search_target(msg) is None


def test_parse_ssdp_search_target_empty_returns_none():
    assert parse_ssdp_search_target("") is None


def test_parse_ssdp_search_target_first_st_wins():
    msg = "M-SEARCH * HTTP/1.1\r\nST: first\r\nST: second\r\n\r\n"
    assert parse_ssdp_search_target(msg) == "first"


# --- ssdp_response ---


def test_ssdp_response_http_200():
    config = {"ssdp_http_port": 8080}
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    body = ssdp_response(config, "192.168.1.1", "urn:schemas-denon-com:device:AiosDevice:1", runtime_state)
    text = body.decode("utf-8")
    assert text.startswith("HTTP/1.1 200 OK\r\n")


def test_ssdp_response_contains_location_with_description_xml():
    config = {"ssdp_http_port": 8080}
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    body = ssdp_response(config, "192.168.1.1", "urn:test:device:1", runtime_state)
    text = body.decode("utf-8")
    assert "LOCATION: http://192.168.1.1:8080/description.xml" in text


def test_ssdp_response_uses_config_port():
    config = {"ssdp_http_port": 9000}
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    body = ssdp_response(config, "10.0.0.5", "ssdp:all", runtime_state)
    text = body.decode("utf-8")
    assert "http://10.0.0.5:9000/description.xml" in text


def test_ssdp_response_default_port():
    config = {}
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    body = ssdp_response(config, "127.0.0.1", "upnp:rootdevice", runtime_state)
    text = body.decode("utf-8")
    assert "http://127.0.0.1:8080/description.xml" in text


def test_ssdp_response_contains_st():
    config = {"ssdp_http_port": 8080}
    st = "urn:schemas-denon-com:device:AiosDevice:1"
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    body = ssdp_response(config, "192.168.1.1", st, runtime_state)
    text = body.decode("utf-8")
    assert f"ST: {st}" in text


def test_ssdp_response_contains_usn_format():
    config = {"ssdp_http_port": 8080}
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    body = ssdp_response(config, "192.168.1.1", "urn:test:1", runtime_state)
    text = body.decode("utf-8")
    # USN is uuid:denon-proxy-{serial}::{st}, serial = proxy-{ip with dashes}
    assert "USN: uuid:denon-proxy-proxy-192-168-1-1::urn:test:1" in text


def test_ssdp_response_usn_serial_escapes_dots():
    config = {"ssdp_http_port": 80}
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    body = ssdp_response(config, "10.0.0.1", "ssdp:all", runtime_state)
    text = body.decode("utf-8")
    assert "denon-proxy-proxy-10-0-0-1" in text


def test_ssdp_response_usn_uses_avr_serial_when_set():
    """When avr_info has serial_number, SSDP UDN/USN use it instead of proxy-ip."""
    config = {"ssdp_http_port": 8080}
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo(
        manufacturer="Denon",
        model_name="AVR-X1600H",
        serial_number="DEVICE-123",
        raw_friendly_name=None,
        raw_sources=[],
    )
    body = ssdp_response(config, "192.168.1.1", "urn:test:1", runtime_state)
    text = body.decode("utf-8")
    assert "USN: uuid:denon-proxy-DEVICE-123::urn:test:1" in text


def test_ssdp_response_with_virtual_avr_info_uses_proxy_ip_in_usn():
    """When avr_info is AVRInfo.virtual(), SSDP USN uses proxy-ip (no device serial)."""
    config = {"ssdp_http_port": 8080}
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    body = ssdp_response(config, "10.0.0.5", "urn:schemas-denon-com:device:AiosDevice:1", runtime_state)
    text = body.decode("utf-8")
    assert "USN: uuid:denon-proxy-proxy-10-0-0-5::urn:schemas-denon-com:device:AiosDevice:1" in text
