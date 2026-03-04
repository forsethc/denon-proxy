from denon_proxy import (
    _is_valid_client_command,
    _command_group,
    _should_log_command_info,
    AVRState,
    apply_payload_to_state,
    avr_response_broadcast_lines,
    build_json_state,
    state_and_config_updates_from_denonavr,
)


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

    cfg = {"log_command_groups_info": ["power", "volume"]}
    assert _should_log_command_info(cfg, "PWON") is True
    assert _should_log_command_info(cfg, "MV50") is True
    assert _should_log_command_info(cfg, "SIHDMI1") is False


class _FakeClient:
    def __init__(self, ip: str) -> None:
        self._peername = (ip, 12345)


class _FakeAvr:
    def __init__(self) -> None:
        self.volume_max = 80.0

    def is_connected(self) -> bool:
        return True

    def get_details(self) -> dict:
        return {"type": "physical", "host": "127.0.0.1", "port": 23}


def test_build_json_state_structure_and_volume_conversion():
    state = AVRState()
    state.power = "ON"
    state.volume = "50"
    avr = _FakeAvr()
    clients = [_FakeClient("10.0.0.1"), _FakeClient("10.0.0.2")]
    config = {
        "ssdp_friendly_name": "My AVR Proxy",
        "enable_ssdp": True,
        "ssdp_http_port": 8080,
        "_avr_info": {
            "manufacturer": "Denon",
            "model_name": "TestModel",
            "serial_number": "12345",
            "friendly_name": "Proxy AVR",
        },
        "_resolved_sources": [("CD", "CD"), ("HDMI1", "Game Console")],
    }

    result = build_json_state(state, avr, clients, config)

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

    state_updates, avr_info, device_sources = state_and_config_updates_from_denonavr(MockD())
    assert state_updates["power"] == "ON"
    assert state_updates["volume"] == "40"
    assert state_updates["input_source"] == "HDMI1"
    assert state_updates["mute"] is False
    assert state_updates["sound_mode"] == "STEREO"
    assert state_updates["smart_select"] == "SMART1"
    assert avr_info["manufacturer"] == "Denon"
    assert avr_info["model_name"] == "AVR-X1600H"
    assert avr_info["friendly_name"] == "Living Room"
    assert device_sources == []


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

    state_updates, avr_info, device_sources = state_and_config_updates_from_denonavr(MockD())
    assert state_updates["power"] == "STANDBY"
    assert state_updates["mute"] is True
    assert state_updates["smart_select"] == "SMART1"
    assert state_updates.get("sound_mode") is None
    assert device_sources == [("CD", "CD Player"), ("HDMI1", "Game")]


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

    state_updates, _, _ = state_and_config_updates_from_denonavr(MockD())
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


def test_apply_payload_to_state_updates_present_fields():
    state = AVRState()
    state.power = "STANDBY"
    state.volume = "40"
    apply_payload_to_state(state, {"power": "ON", "volume": "55"})
    assert state.power == "ON"
    assert state.volume == "55"
    assert state.input_source == "CD"  # unchanged default


def test_apply_payload_to_state_power_uppercased():
    state = AVRState()
    apply_payload_to_state(state, {"power": "on"})
    assert state.power == "ON"
    apply_payload_to_state(state, {"power": "standby"})
    assert state.power == "STANDBY"


def test_apply_payload_to_state_falsy_values():
    state = AVRState()
    state.power = "ON"
    apply_payload_to_state(state, {"power": None})
    assert state.power is None
    apply_payload_to_state(state, {"volume": ""})
    assert state.volume == ""


def test_apply_payload_to_state_mute_and_sources():
    state = AVRState()
    apply_payload_to_state(state, {"mute": True, "input_source": "HDMI1", "sound_mode": "DOLBY"})
    assert state.mute is True
    assert state.input_source == "HDMI1"
    assert state.sound_mode == "DOLBY"


def test_apply_payload_to_state_smart_select_normalized():
    state = AVRState()
    apply_payload_to_state(state, {"smart_select": "smart1"})
    assert state.smart_select == "SMART1"
    apply_payload_to_state(state, {"smart_select": "2"})
    assert state.smart_select == "SMART2"
    apply_payload_to_state(state, {"smart_select": None})
    assert state.smart_select is None


def test_apply_payload_to_state_partial_leaves_others_unchanged():
    state = AVRState()
    state.input_source = "TUNER"
    state.sound_mode = "STEREO"
    apply_payload_to_state(state, {"volume": "60"})
    assert state.volume == "60"
    assert state.input_source == "TUNER"
    assert state.sound_mode == "STEREO"

