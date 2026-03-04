from denon_proxy import (
    _is_valid_client_command,
    _command_group,
    _should_log_command_info,
    AVRState,
    build_json_state,
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

