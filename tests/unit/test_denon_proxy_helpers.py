from avr_info import AVRInfo
from runtime_state import RuntimeState
from avr_state import AVRState
from denon_proxy import (
    _client_ip_for_display,
    _command_group,
    _is_valid_client_command,
    _should_log_command_info,
    load_config_from_dict,
    avr_response_broadcast_lines,
    build_json_state,
    state_and_config_updates_from_denonavr,
)


def test_client_ip_for_display_returns_ip_or_question_mark():
    """_client_ip_for_display returns peername[0] when set, else '?'."""
    client_with_peername = type("C", (), {"_peername": ("192.168.1.1", 12345)})()
    assert _client_ip_for_display(client_with_peername) == "192.168.1.1"
    client_peername_none = type("C", (), {"_peername": None})()
    assert _client_ip_for_display(client_peername_none) == "?"
    client_no_peername = type("C", (), {})()
    assert _client_ip_for_display(client_no_peername) == "?"


def test_is_valid_client_command_filters_short_and_control_bytes():
    assert _is_valid_client_command("PWON") is True
    assert _is_valid_client_command("SI") is True
    assert _is_valid_client_command("") is False
    assert _is_valid_client_command("X") is False
    # Include a control character (other than CR/LF/TAB) to ensure it is rejected
    bad = "PWON" + chr(1)
    assert _is_valid_client_command(bad) is False


def test_command_group_and_should_log_command_info():
    assert _command_group("PWON") == "power"
    assert _command_group("ZMON") == "power"
    assert _command_group("MV50") == "volume"
    assert _command_group("MVMAX 60") == "other"
    assert _command_group("MSSMART1") == "smart_select"
    assert _command_group("UNKNOWN") == "other"
    # Empty or single-char command -> other
    assert _command_group("") == "other"
    assert _command_group("P") == "other"

    cfg = {"log_command_groups_info": ["power", "volume"]}
    assert _should_log_command_info(cfg, "PWON") is True
    assert _should_log_command_info(cfg, "MV50") is True
    assert _should_log_command_info(cfg, "SIHDMI1") is False


class _FakeClient:
    def __init__(self, ip: str) -> None:
        self._peername = (ip, 12345)


class _FakeAvr:
    def __init__(self) -> None:
        pass
    def is_connected(self) -> bool:
        return True

    def get_details(self) -> dict:
        return {"type": "physical", "host": "127.0.0.1", "port": 23}


def test_build_json_state_with_no_avr():
    """build_json_state with avr=None reports type 'none', connected False, default volume_max."""
    state = AVRState()
    state.power = "ON"
    state.volume = "50"
    config = load_config_from_dict({})
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    result = build_json_state(state, None, [], config, runtime_state)
    assert result["avr"]["type"] == "none"
    assert result["avr"]["connected"] is False
    assert result["avr"]["volume_max"] == 98.0
    assert result["state"]["power"] == "ON"


def test_build_json_state_structure_and_volume_conversion():
    state = AVRState()
    state.power = "ON"
    state.volume = "50"
    state.volume_max = 80.0
    avr = _FakeAvr()
    clients = [_FakeClient("10.0.0.1"), _FakeClient("10.0.0.2")]
    config = {
        "ssdp_friendly_name": "My AVR Proxy",
        "enable_ssdp": True,
        "ssdp_http_port": 8080,
    }
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo(
        manufacturer="Denon",
        model_name="TestModel",
        serial_number="12345",
        raw_friendly_name="Proxy AVR",
        raw_sources=[],
    )
    runtime_state.resolved_sources = [("CD", "CD"), ("HDMI1", "Game Console")]

    result = build_json_state(state, avr, clients, config, runtime_state)

    assert set(result.keys()) == {"friendly_name", "avr", "clients", "client_count", "state", "discovery"}
    assert result["friendly_name"] == "My AVR Proxy"
    assert result["client_count"] == 2
    assert result["clients"] == ["10.0.0.1", "10.0.0.2"]

    avr_dict = result["avr"]
    assert avr_dict["type"] == "physical"
    assert avr_dict["connected"] is True
    assert avr_dict["manufacturer"] == "Denon"
    assert avr_dict["model_name"] == "TestModel"
    assert avr_dict["friendly_name"] == "Proxy AVR"
    assert avr_dict["sources"] == [
        {"func": "CD", "display_name": "CD"},
        {"func": "HDMI1", "display_name": "Game Console"},
    ]

    state_dict = result["state"]
    # Volume should have been converted to numeric level
    assert isinstance(state_dict["volume"], (int, float))
    assert state_dict["power"] == "ON"


def test_build_json_state_with_virtual_avr_info():
    """When avr_info is AVRInfo.virtual(), avr dict has Denon / Virtual and no serial."""
    state = AVRState()
    state.power = "ON"
    config = load_config_from_dict({"enable_ssdp": False})
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    result = build_json_state(state, None, [], config, runtime_state)
    avr_dict = result["avr"]
    assert avr_dict["manufacturer"] == "Denon"
    assert avr_dict["model_name"] == "Virtual"
    assert avr_dict["serial_number"] is None
    assert avr_dict["friendly_name"] is None


def test_build_json_state_includes_discovery_info():
    """build_json_state includes discovery section with enabled, http_port, proxy_ip."""
    state = AVRState()
    config = load_config_from_dict({"enable_ssdp": True, "ssdp_http_port": 9090})
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    result = build_json_state(state, None, [], config, runtime_state)
    assert "discovery" in result
    assert result["discovery"]["enabled"] is True
    assert result["discovery"]["http_port"] == 9090


def test_state_and_config_updates_from_denonavr_basic():
    class MockVol:
        def __init__(self, volume: float):
            self.volume = volume

    class MockD:
        power = "ON"
        vol = MockVol(-20.0)  # 80 + (-20)*2 = 40
        input_func = "HDMI1"
        muted = False
        sound_mode = "STEREO"
        smart_select = "SMART1"
        manufacturer = "Denon"
        model_name = "AVR-X1600H"
        serial_number = "123"
        name = "Living Room"
        input = None

    state_updates, avr_info = state_and_config_updates_from_denonavr(MockD())
    assert state_updates["power"] == "ON"
    assert state_updates["volume"] == "40"
    assert state_updates["input_source"] == "HDMI1"
    assert state_updates["mute"] is False
    assert state_updates["sound_mode"] == "STEREO"
    assert state_updates["smart_select"] == "SMART1"
    assert avr_info.manufacturer == "Denon"
    assert avr_info.model_name == "AVR-X1600H"
    assert avr_info.raw_friendly_name == "Living Room"
    assert avr_info.raw_sources == []


def test_state_and_config_updates_from_denonavr_smart_select_and_sources():
    class MockVol:
        volume = 0.0

    class MockInput:
        _input_func_map_rev = {"CD": "CD Player", "HDMI1": "Game"}

    class MockD:
        power = "STANDBY"
        vol = MockVol()
        input_func = None
        muted = True
        sound_mode = "SMART1"
        smart_select = None
        manufacturer = None
        model_name = None
        serial_number = None
        name = None
        input = MockInput()

    state_updates, avr_info = state_and_config_updates_from_denonavr(MockD())
    assert state_updates["power"] == "STANDBY"
    assert state_updates["mute"] is True
    assert state_updates["smart_select"] == "SMART1"
    assert state_updates.get("sound_mode") is None
    assert avr_info.raw_sources == [("CD", "CD Player"), ("HDMI1", "Game")]


def test_state_and_config_updates_from_denonavr_sound_mode_smart_treated_as_smart_select():
    """When denonavr returns sound_mode like SMART0, it is stored as smart_select and sound_mode cleared."""
    class MockD:
        power = "ON"
        vol = None
        input_func = None
        muted = None
        sound_mode = "SMART0"
        smart_select = None
        manufacturer = None
        model_name = None
        serial_number = None
        name = None
        input = None

    state_updates, _ = state_and_config_updates_from_denonavr(MockD())
    assert state_updates.get("smart_select") == "SMART0"
    assert state_updates.get("sound_mode") is None


def test_state_and_config_updates_from_denonavr_minimal_attributes():
    """When denonavr has few attributes set, only those are in state_updates."""
    class MockD:
        power = None
        vol = None
        input_func = None
        muted = None
        sound_mode = None
        smart_select = None
        manufacturer = None
        model_name = None
        serial_number = None
        name = None
        input = None

    state_updates, avr_info = state_and_config_updates_from_denonavr(MockD())
    assert state_updates == {}
    assert avr_info.manufacturer is None
    assert avr_info.raw_sources == []


def test_state_and_config_updates_from_denonavr_volume_conversion():
    class MockVol:
        def __init__(self, vol_db: float):
            self.volume = vol_db

    class MockD:
        power = "ON"
        vol = MockVol(0.0)
        input_func = None
        muted = None
        sound_mode = None
        smart_select = None
        manufacturer = None
        model_name = None
        serial_number = None
        name = None
        input = None

    state_updates, _ = state_and_config_updates_from_denonavr(MockD())
    assert state_updates["volume"] == "80"


def test_avr_response_broadcast_lines_pwon():
    assert avr_response_broadcast_lines("PWON") == ["PWON", "ZMON"]


def test_avr_response_broadcast_lines_standby():
    assert avr_response_broadcast_lines("PWSTANDBY") == ["PWSTANDBY", "ZMSTANDBY", "ZMOFF"]
    assert avr_response_broadcast_lines("PWSTANDBY ") == ["PWSTANDBY ", "ZMSTANDBY", "ZMOFF"]
    assert avr_response_broadcast_lines("PWstandby") == ["PWstandby", "ZMSTANDBY", "ZMOFF"]


def test_avr_response_broadcast_lines_other():
    assert avr_response_broadcast_lines("MV50") == ["MV50"]
    assert avr_response_broadcast_lines("SIHDMI1") == ["SIHDMI1"]
    assert avr_response_broadcast_lines("MUON") == ["MUON"]


def test_apply_payload_updates_present_fields():
    state = AVRState()
    state.power = "STANDBY"
    state.volume = "40"
    state.apply_payload({"power": "ON", "volume": "55"})
    assert state.power == "ON"
    assert state.volume == "55"
    assert state.input_source == "CD"  # unchanged default


def test_apply_payload_power_uppercased():
    state = AVRState()
    state.apply_payload({"power": "on"})
    assert state.power == "ON"
    state.apply_payload({"power": "standby"})
    assert state.power == "STANDBY"


def test_apply_payload_falsy_values():
    state = AVRState()
    state.power = "ON"
    state.apply_payload({"power": None})
    assert state.power is None
    state.apply_payload({"volume": ""})
    assert state.volume == ""


def test_apply_payload_mute_and_sources():
    state = AVRState()
    state.apply_payload({"mute": True, "input_source": "HDMI1", "sound_mode": "DOLBY"})
    assert state.mute is True
    assert state.input_source == "HDMI1"
    assert state.sound_mode == "DOLBY"


def test_apply_payload_smart_select_normalized():
    state = AVRState()
    state.apply_payload({"smart_select": "smart1"})
    assert state.smart_select == "SMART1"
    state.apply_payload({"smart_select": "2"})
    assert state.smart_select == "SMART2"
    state.apply_payload({"smart_select": None})
    assert state.smart_select is None


def test_apply_payload_partial_leaves_others_unchanged():
    state = AVRState()
    state.input_source = "TUNER"
    state.sound_mode = "STEREO"
    state.apply_payload({"volume": "60"})
    assert state.volume == "60"
    assert state.input_source == "TUNER"
    assert state.sound_mode == "STEREO"

