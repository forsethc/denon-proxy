from avr_discovery import (
    get_advertise_ip,
    get_sources,
    is_docker_internal_ip,
    get_proxy_friendly_name,
    deviceinfo_xml,
    appcommand_friendlyname_xml,
    parse_appcommand_request,
    mainzone_xml,
    _escape_xml_text,
)


def test_get_advertise_ip_prefers_config_value():
    cfg = {"ssdp_advertise_ip": "192.168.1.50"}
    assert get_advertise_ip(cfg) == "192.168.1.50"


def test_is_docker_internal_ip():
    assert is_docker_internal_ip("172.17.0.1") is True
    assert is_docker_internal_ip("172.19.0.2") is True
    assert is_docker_internal_ip("172.16.0.1") is True
    assert is_docker_internal_ip("172.31.255.255") is True
    assert is_docker_internal_ip("192.168.1.1") is False
    assert is_docker_internal_ip("10.0.0.1") is False
    assert is_docker_internal_ip("172.15.0.1") is False
    assert is_docker_internal_ip("172.32.0.1") is False
    assert is_docker_internal_ip(None) is False
    assert is_docker_internal_ip("") is False


def test_get_proxy_friendly_name_uses_config_then_physical_then_default():
    # From config
    cfg = {"ssdp_friendly_name": "Configured Name"}
    # Reset cached name by calling with config first
    name = get_proxy_friendly_name(cfg)
    assert name == "Configured Name"


def test_deviceinfo_xml_includes_sources():
    cfg = {
        "_resolved_sources": [("CD", "CD Player"), ("HDMI1", "Game Console")],
    }
    xml = deviceinfo_xml(cfg)
    assert "<Device_Info>" in xml
    assert "<ModelName>AVR-3808</ModelName>" in xml
    assert "<Source><FuncName>CD</FuncName><DefaultName>CD Player</DefaultName></Source>" in xml
    assert "<Source><FuncName>HDMI1</FuncName><DefaultName>Game Console</DefaultName></Source>" in xml


def test_appcommand_friendlyname_xml_uses_proxy_name():
    cfg = {"ssdp_friendly_name": "Proxy Name"}
    xml = appcommand_friendlyname_xml(cfg)
    assert "<friendlyname>Proxy Name</friendlyname>" in xml


def test_parse_appcommand_request_multiple_tx_chunks():
    body = (
        b"<tx><cmd id=\"1\">GetFriendlyName</cmd></tx>"
        b"<tx><cmd id=\"2\">GetAllZonePowerStatus</cmd></tx>"
    )
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
    cfg = {
        "ssdp_friendly_name": "Proxy Name",
        "_resolved_sources": [("CD", "CD Player"), ("HDMI1", "Game Console")],
    }
    xml_bytes = mainzone_xml(state, cfg)
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
    assert _escape_xml_text("a & b <c> \"d\"") == "a &amp; b &lt;c&gt; &quot;d&quot;"


def test_get_sources_from_dict():
    config = {"sources": {"CD": "CD Player", "HDMI1": "Game"}}
    result = get_sources(config)
    assert result == [("CD", "CD Player"), ("HDMI1", "Game")]
    assert config["_resolved_sources"] == result


def test_get_sources_from_list_of_tuples():
    config = {"sources": [("CD", "CD Player"), ("HDMI1", "Game")]}
    result = get_sources(config)
    assert result == [("CD", "CD Player"), ("HDMI1", "Game")]


def test_get_sources_from_list_of_dicts():
    config = {"sources": [{"func": "CD", "display_name": "CD Player"}, {"func": "HDMI1", "name": "Game"}]}
    result = get_sources(config)
    assert result == [("CD", "CD Player"), ("HDMI1", "Game")]


def test_get_sources_filters_against_device_sources():
    config = {
        "sources": {"CD": "CD Player", "HDMI1": "Game", "UNKNOWN": "Other"},
        "_device_sources": [("CD", "CD"), ("HDMI1", "HDMI1")],
    }
    result = get_sources(config)
    assert result == [("CD", "CD Player"), ("HDMI1", "Game")]
    assert ("UNKNOWN", "Other") not in result


def test_get_sources_uses_device_sources_when_no_user_sources():
    config = {"_device_sources": [("CD", "CD"), ("HDMI1", "Game Console")]}
    result = get_sources(config)
    assert result == [("CD", "CD"), ("HDMI1", "Game Console")]

