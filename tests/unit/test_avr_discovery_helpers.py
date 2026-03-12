"""Unit tests: avr_discovery helpers (get_sources, deviceinfo_xml, description_xml, etc.)."""
# ruff:noqa: I001 # ruff doesn't like the discovery imports

import logging
from denon_proxy.avr.discovery import (
    _appcommand_friendlyname_xml as appcommand_friendlyname_xml,
    _appcommand_response_xml as appcommand_response_xml,
    _description_xml as description_xml,
    _deviceinfo_xml as deviceinfo_xml,
    _escape_xml_text,
    _get_proxy_friendly_name as get_proxy_friendly_name,
    _get_sources as get_sources,
    _mainzone_xml as mainzone_xml,
    _parse_appcommand_request as parse_appcommand_request,
    _rewrite_avr_description,
    get_advertise_ip,
)
from denon_proxy.avr.info import AVRInfo
from denon_proxy.runtime.state import RuntimeState


def test_get_advertise_ip_prefers_config_value():
    cfg = {"ssdp_advertise_ip": "192.168.1.50"}
    assert get_advertise_ip(cfg) == "192.168.1.50"


def test_get_proxy_friendly_name_uses_config_then_physical_then_default():
    # From config
    cfg = {"ssdp_friendly_name": "Configured Name"}
    runtime_state = RuntimeState()
    name = get_proxy_friendly_name(cfg, runtime_state)
    assert name == "Configured Name"


def test_deviceinfo_xml_includes_sources():
    cfg = {}
    runtime_state = RuntimeState()
    runtime_state.resolved_sources = [("CD", "CD Player"), ("HDMI1", "Game Console")]
    xml = deviceinfo_xml(cfg, runtime_state)
    assert "<Device_Info>" in xml
    assert "<ModelName>AVR-3808</ModelName>" in xml
    assert "<Source><FuncName>CD</FuncName><DefaultName>CD Player</DefaultName></Source>" in xml
    assert "<Source><FuncName>HDMI1</FuncName><DefaultName>Game Console</DefaultName></Source>" in xml


def test_appcommand_friendlyname_xml_uses_proxy_name():
    cfg = {"ssdp_friendly_name": "Proxy Name"}
    runtime_state = RuntimeState()
    xml = appcommand_friendlyname_xml(cfg, runtime_state)
    assert "<friendlyname>Proxy Name</friendlyname>" in xml


def test_description_xml_structure_and_sources():
    cfg = {
        "ssdp_friendly_name": "My Proxy",
        "ssdp_http_port": 9000,
    }
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    xml = description_xml(cfg, "192.168.1.1", runtime_state)
    assert 'xmlns="urn:schemas-upnp-org:device-1-0"' in xml
    assert "<friendlyName>My Proxy</friendlyName>" in xml
    assert "http://192.168.1.1:9000/description.xml" in xml
    assert "<serialNumber>proxy-192-168-1-1</serialNumber>" in xml
    assert "urn:schemas-upnp-org:device:MediaRenderer:1" in xml
    runtime_state2 = RuntimeState()
    runtime_state2.avr_info = AVRInfo(
        manufacturer="Denon",
        model_name="AVR-X1600H",
        serial_number=None,
        raw_friendly_name=None,
        raw_sources=[],
    )
    xml2 = description_xml(cfg, "10.0.0.5", runtime_state2)
    assert "AVR-X1600H Proxy" in xml2
    assert "Denon" in xml2


def test_description_xml_uses_avr_serial_when_set():
    """When avr_info has serial_number, description XML uses it for serialNumber and UDN."""
    cfg = {"ssdp_http_port": 8080}
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo(
        manufacturer="Denon",
        model_name="X1600",
        serial_number="DEVICE-SERIAL-99",
        raw_friendly_name=None,
        raw_sources=[],
    )
    xml = description_xml(cfg, "192.168.1.1", runtime_state)
    assert "<serialNumber>DEVICE-SERIAL-99</serialNumber>" in xml
    assert "uuid:denon-proxy-DEVICE-SERIAL-99" in xml


def test_description_xml_with_virtual_avr_info():
    """When avr_info is AVRInfo.virtual(), description XML shows Virtual Proxy and proxy-ip serial."""
    cfg = {"ssdp_http_port": 8080}
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    xml = description_xml(cfg, "192.168.1.1", runtime_state)
    assert "Virtual Proxy" in xml
    assert "<manufacturer>Denon</manufacturer>" in xml
    assert "<serialNumber>proxy-192-168-1-1</serialNumber>" in xml
    assert "uuid:denon-proxy-proxy-192-168-1-1" in xml


def test_appcommand_response_xml_get_friendly_name():
    cfg = {"ssdp_friendly_name": "Test AVR"}
    state = _FakeState()
    runtime_state = RuntimeState()
    body = b'<tx><cmd id="1">GetFriendlyName</cmd></tx>'
    out = appcommand_response_xml(cfg, state, body, logging.getLogger("tests.avr_discovery_helpers"), runtime_state)
    text = out.decode("utf-8")
    assert "<rx>" in text
    assert 'cmd_text="GetFriendlyName"' in text
    assert "<friendlyname>Test AVR</friendlyname>" in text


def test_appcommand_response_xml_zone_power_and_volume():
    cfg = {}
    state = _FakeState()
    state.power = "STANDBY"
    state.volume = "45"
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    body = b'<tx><cmd id="1">GetAllZonePowerStatus</cmd></tx><tx><cmd id="2">GetAllZoneVolume</cmd></tx>'
    out = appcommand_response_xml(cfg, state, body, logging.getLogger("tests.avr_discovery_helpers"), runtime_state)
    text = out.decode("utf-8")
    assert "<zone1>STANDBY</zone1>" in text
    assert "<volume>" in text


def test_appcommand_response_xml_empty_body_defaults_to_get_friendly_name():
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    out = appcommand_response_xml({}, None, b"", logging.getLogger("tests.avr_discovery_helpers"), runtime_state)
    text = out.decode("utf-8")
    assert "GetFriendlyName" in text
    assert "<friendlyname>" in text


def test_rewrite_avr_description_replaces_host_and_appends_proxy():
    raw = "<root><friendlyName>Living Room AVR</friendlyName><url>http://192.168.1.100:80/</url></root>"
    result = _rewrite_avr_description(raw, "192.168.1.100", "10.0.0.1", None)
    assert "10.0.0.1" in result
    assert "192.168.1.100" not in result
    assert "Living Room AVR Proxy" in result


def test_rewrite_avr_description_skips_proxy_suffix_if_present():
    raw = "<root><friendlyName>Living Room Proxy</friendlyName></root>"
    result = _rewrite_avr_description(raw, "192.168.1.100", "10.0.0.1", None)
    assert "Living Room Proxy</friendlyName>" in result
    assert "Proxy Proxy" not in result


def test_parse_appcommand_request_multiple_tx_chunks():
    body = b'<tx><cmd id="1">GetFriendlyName</cmd></tx><tx><cmd id="2">GetAllZonePowerStatus</cmd></tx>'
    cmds = parse_appcommand_request(body)
    assert cmds == [
        ("1", "GetFriendlyName"),
        ("2", "GetAllZonePowerStatus"),
    ]


class _FakeState:
    def __init__(self) -> None:
        self.power = "ON"
        self.volume = "50"
        self.mute = False
        self.input_source = "CD"
        self.sound_mode = "STEREO"


def test_mainzone_xml_matches_state_and_sources():
    state = _FakeState()
    cfg = {"ssdp_friendly_name": "Proxy Name"}
    runtime_state = RuntimeState()
    runtime_state.resolved_sources = [("CD", "CD Player"), ("HDMI1", "Game Console")]
    xml_bytes = mainzone_xml(state, cfg, runtime_state)
    xml = xml_bytes.decode("utf-8")

    assert "<FriendlyName><value>Proxy Name</value></FriendlyName>" in xml
    assert "<Power><value>ON</value></Power>" in xml
    assert "<Mute><value>off</value></Mute>" in xml
    assert "<InputFuncSelect><value>CD</value></InputFuncSelect>" in xml
    assert "<SurrMode><value>STEREO</value></SurrMode>" in xml
    # Input source lists
    assert "<InputFuncList>" in xml
    assert "<RenameSource>" in xml
    assert "<SourceDelete>" in xml


def test_escape_xml_text():
    assert _escape_xml_text("a & b") == "a &amp; b"
    assert _escape_xml_text("<x>") == "&lt;x&gt;"
    assert _escape_xml_text('"') == "&quot;"
    assert _escape_xml_text("no special chars") == "no special chars"
    assert _escape_xml_text('a & b <c> "d"') == "a &amp; b &lt;c&gt; &quot;d&quot;"


def test_get_sources_from_dict():
    config = {"sources": {"CD": "CD Player", "HDMI1": "Game"}}
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    result = get_sources(config, runtime_state)
    assert result == [("CD", "CD Player"), ("HDMI1", "Game")]
    assert runtime_state.resolved_sources == result


def test_get_sources_filters_against_raw_sources():
    config = {
        "sources": {"CD": "CD Player", "HDMI1": "Game", "UNKNOWN": "Other"},
    }
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo(
        manufacturer=None,
        model_name=None,
        serial_number=None,
        raw_friendly_name=None,
        raw_sources=[("CD", "CD"), ("HDMI1", "HDMI1")],
    )
    result = get_sources(config, runtime_state)
    assert result == [("CD", "CD Player"), ("HDMI1", "Game")]
    assert ("UNKNOWN", "Other") not in result


def test_get_sources_uses_raw_sources_when_no_user_sources():
    config = {}
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo(
        manufacturer=None,
        model_name=None,
        serial_number=None,
        raw_friendly_name=None,
        raw_sources=[("CD", "CD"), ("HDMI1", "Game Console")],
    )
    result = get_sources(config, runtime_state)
    assert result == [("CD", "CD"), ("HDMI1", "Game Console")]
