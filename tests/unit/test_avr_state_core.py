from denon_proxy.avr.state import AVRState
from denon_proxy.constants import VOLUME_DEFAULT_LEVEL


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


def test_apply_command_empty_or_short_returns_false():
    """Empty or single-char command does not change state and returns False."""
    state = AVRState()
    assert state.apply_command("", volume_step=0.5) is False
    assert state.apply_command("P", volume_step=0.5) is False
    assert state.power == "ON"


def test_apply_command_pw_with_param():
    """PW with extra param (e.g. PWSTANDBY) sets power."""
    state = AVRState()
    state.apply_command("PWSTANDBY", volume_step=0.5)
    assert state.power == "STANDBY"
    state.apply_command("PWOTHER", volume_step=0.5)
    assert state.power == "OTHER"


def test_apply_command_zm_sets_power():
    """ZM (zone main) commands set power: ZMON -> ON, ZMOFF/ZMSTANDBY -> STANDBY."""
    state = AVRState()
    state.power = "STANDBY"
    changed = state.apply_command("ZMON", volume_step=0.5)
    assert changed and state.power == "ON"
    changed = state.apply_command("ZMSTANDBY", volume_step=0.5)
    assert changed and state.power == "STANDBY"
    state.power = "ON"
    changed = state.apply_command("ZMOFF", volume_step=0.5)
    assert changed and state.power == "STANDBY"


def test_apply_command_ms_sets_sound_mode():
    """MS (sound mode) command sets sound_mode; distinct from MSSMART (smart select)."""
    state = AVRState()
    changed = state.apply_command("MSDOLBY", volume_step=0.5)
    assert changed and state.sound_mode == "DOLBY"
    changed = state.apply_command("MSNEO6", volume_step=0.5)
    assert changed and state.sound_mode == "NEO6"


def test_apply_command_mv_query_does_not_change_volume():
    """MV? is a query; apply_command does not change volume and returns False."""
    state = AVRState()
    state.volume = "50"
    changed = state.apply_command("MV?", volume_step=0.5)
    assert changed is False and state.volume == "50"


def test_update_from_message_query_does_not_change_state():
    """PW? and MV? are queries; state is unchanged (pass branches)."""
    state = AVRState()
    state.power = "ON"
    state.volume = "50"
    state.update_from_message("PW?")
    state.update_from_message("MV?")
    assert state.power == "ON"
    assert state.volume == "50"


def test_update_from_message_mvmax_updates_volume_max_and_not_volume():
    """MVMAX is AVR config, not current volume; updates volume_max only."""
    state = AVRState()
    state.volume = "50"
    state.update_from_message("MVMAX60")
    assert state.volume == "50"
    assert state.volume_max == 60.0
    # Other values still unchanged when MVMAX received
    state.update_from_message("MVMAX ?")
    assert state.volume_max == 60.0


def test_apply_command_and_get_status_dump_and_snapshot_restore():
    state = AVRState()
    # Start from known defaults
    assert state.volume == str(VOLUME_DEFAULT_LEVEL)

    # Apply commands
    changed = state.apply_command("PWON", volume_step=0.5)
    assert changed and state.power == "ON"

    changed = state.apply_command("MVUP", volume_step=1.0)
    assert changed

    changed = state.apply_command("MUON", volume_step=1.0)
    assert changed and state.mute is True

    changed = state.apply_command("SIHDRADIO", volume_step=1.0)
    assert changed and state.input_source == "HDRADIO"

    changed = state.apply_command("MSSMART2", volume_step=1.0)
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


def test_get_status_dump_power_standby_pw_only():
    """When power is STANDBY/OFF, status dump is PW only (no synthetic ZM lines)."""
    state = AVRState()
    state.power = "STANDBY"
    dump = state.get_status_dump()
    lines = [ln.strip() for ln in dump.strip().splitlines() if ln.strip()]
    assert lines == ["PWSTANDBY"]


def test_get_status_dump_standby_omits_volume_and_rest():
    """When power is STANDBY, dump is PW line only even if internal state still has volume/input/etc."""
    state = AVRState()
    state.power = "STANDBY"
    state.volume = "45"
    state.input_source = "HDMI1"
    state.sound_mode = "STEREO"
    dump = state.get_status_dump()
    lines = [ln.strip() for ln in dump.strip().splitlines() if ln.strip()]
    assert lines == ["PWSTANDBY"]


def test_apply_payload_normalizes_smart_select():
    """apply_payload normalizes smart_select to SMART{n} (raw values accepted)."""
    state = AVRState()
    state.apply_payload({"smart_select": "SMART0"})
    assert state.smart_select == "SMART0"
    state.apply_payload({"smart_select": "smart1"})
    assert state.smart_select == "SMART1"
    state.apply_payload({"smart_select": "2"})
    assert state.smart_select == "SMART2"
    state.apply_payload({"smart_select": "  SMART3  "})
    assert state.smart_select == "SMART3"
    state.apply_payload({"smart_select": None})
    assert state.smart_select is None
    state.apply_payload({"smart_select": ""})
    assert state.smart_select is None
    state.apply_payload({"smart_select": "  "})
    assert state.smart_select is None
    state.apply_payload({"smart_select": "SMART"})
    assert state.smart_select is None
    state.apply_payload({"smart_select": "SMARTx"})
    assert state.smart_select is None
