from denon_proxy.avr.info import AVRInfo
from denon_proxy.avr.state import AVRState
from denon_proxy.proxy.core import state_and_config_updates_from_denonavr


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

