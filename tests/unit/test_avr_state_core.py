from avr_state import AVRState, VOLUME_DEFAULT_LEVEL, _normalize_smart_select


def test_update_from_message_updates_all_fields():
    state = AVRState()

    state.update_from_message("PWSTANDBY")
    assert state.power == "STANDBY"

    state.update_from_message("MV55")
    assert state.volume == "55"

    state.update_from_message("SIHDMI1")
    assert state.input_source == "HDMI1"

    state.update_from_message("MUON")
    assert state.mute is True

    state.update_from_message("MSDOLBY")
    assert state.sound_mode == "DOLBY"

    state.update_from_message("MSSMART1")
    assert state.smart_select == "SMART1"


def test_apply_command_and_get_status_dump_and_snapshot_restore():
    state = AVRState()
    # Start from known defaults
    assert state.volume == str(VOLUME_DEFAULT_LEVEL)

    # Apply commands
    changed = state.apply_command("PWON", volume_step=0.5, volume_max=80.0)
    assert changed and state.power == "ON"

    changed = state.apply_command("MVUP", volume_step=1.0, volume_max=80.0)
    assert changed

    changed = state.apply_command("MUON", volume_step=1.0, volume_max=80.0)
    assert changed and state.mute is True

    changed = state.apply_command("SIHDRADIO", volume_step=1.0, volume_max=80.0)
    assert changed and state.input_source == "HDRADIO"

    changed = state.apply_command("MSSMART2", volume_step=1.0, volume_max=80.0)
    assert changed and state.smart_select == "SMART2"

    dump = state.get_status_dump()
    # Status dump should include core lines reflecting updated state
    assert "PWON" in dump
    assert "MV" in dump
    assert "MUON" in dump
    assert "SIHDRADIO" in dump
    assert "MSSMART2" in dump

    snap = state.snapshot()
    # Mutate then restore
    state.power = "OFF"
    state.volume = "10"
    state.restore(snap)
    assert state.power == snap["power"]
    assert state.volume == snap["volume"]


def test_normalize_smart_select():
    assert _normalize_smart_select("SMART0") == "SMART0"
    assert _normalize_smart_select("smart1") == "SMART1"
    assert _normalize_smart_select("2") == "SMART2"
    assert _normalize_smart_select("  SMART3  ") == "SMART3"
    assert _normalize_smart_select(None) is None
    assert _normalize_smart_select("") is None
    assert _normalize_smart_select("  ") is None
    assert _normalize_smart_select("SMART") is None
    assert _normalize_smart_select("SMARTx") is None

