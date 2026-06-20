"""Unit tests for uc_yaml.build_capabilities and build_custom_entities_yaml."""

from __future__ import annotations

from denon_proxy.http.uc_yaml import build_capabilities, build_custom_entities_yaml

BASE = "http://192.168.1.10:8081"

SOURCES = [
    ("CD", "CD"),
    ("HDMI1", "HDMI 1"),
    ("SAT/CBL", "CBL/SAT"),
]


# ---------------------------------------------------------------------------
# build_capabilities
# ---------------------------------------------------------------------------


def test_capabilities_base_url() -> None:
    caps = build_capabilities(BASE, SOURCES)
    assert caps["base_url"] == BASE


def test_capabilities_power_endpoints() -> None:
    caps = build_capabilities(BASE, SOURCES)
    power = caps["features"]["power"]["endpoints"]
    assert power["on"] == f"{BASE}/api/avr/power/on"
    assert power["off"] == f"{BASE}/api/avr/power/off"
    assert power["toggle"] == f"{BASE}/api/avr/power/toggle"


def test_capabilities_volume_endpoints() -> None:
    caps = build_capabilities(BASE, SOURCES)
    vol = caps["features"]["volume"]["endpoints"]
    assert vol["up"] == f"{BASE}/api/avr/volume/up"
    assert vol["down"] == f"{BASE}/api/avr/volume/down"
    assert "level" in vol["set"]


def test_capabilities_mute_endpoints() -> None:
    caps = build_capabilities(BASE, SOURCES)
    mute = caps["features"]["mute"]["endpoints"]
    assert mute["on"] == f"{BASE}/api/avr/mute/on"
    assert mute["off"] == f"{BASE}/api/avr/mute/off"
    assert mute["toggle"] == f"{BASE}/api/avr/mute/toggle"


def test_capabilities_source_options_length() -> None:
    caps = build_capabilities(BASE, SOURCES)
    opts = caps["features"]["source"]["options"]
    assert len(opts) == len(SOURCES)


def test_capabilities_source_option_shape() -> None:
    caps = build_capabilities(BASE, SOURCES)
    cd = caps["features"]["source"]["options"][0]
    assert cd["code"] == "CD"
    assert cd["display_name"] == "CD"
    assert cd["url"] == f"{BASE}/api/avr/source/CD"
    assert cd["cmd_name"] == "SRC_CD"


def test_capabilities_source_slash_normalization() -> None:
    caps = build_capabilities(BASE, SOURCES)
    sat = next(o for o in caps["features"]["source"]["options"] if o["code"] == "SAT/CBL")
    assert sat["cmd_name"] == "SRC_SAT_CBL"


def test_capabilities_endpoints_list() -> None:
    caps = build_capabilities(BASE, SOURCES)
    paths = [e["path"] for e in caps["endpoints"]]
    assert "/api/avr/power/on" in paths
    assert "/api/uc/custom_entities.yaml" in paths


# ---------------------------------------------------------------------------
# build_custom_entities_yaml
# ---------------------------------------------------------------------------


def test_yaml_has_vars_block() -> None:
    yaml = build_custom_entities_yaml(BASE, SOURCES)
    assert "_vars:" in yaml
    assert f"  denon_proxy_url: {BASE}" in yaml


def test_yaml_has_entity_header() -> None:
    yaml = build_custom_entities_yaml(BASE, SOURCES)
    assert "Denon AVR Proxy:" in yaml


def test_yaml_has_on_off_toggle_features() -> None:
    yaml = build_custom_entities_yaml(BASE, SOURCES)
    assert "    'On':" in yaml
    assert "    'Off':" in yaml
    assert "    Toggle:" in yaml
    assert "${denon_proxy_url}/api/avr/power/on" in yaml
    assert "${denon_proxy_url}/api/avr/power/off" in yaml
    assert "${denon_proxy_url}/api/avr/power/toggle" in yaml


def test_yaml_has_volume_and_mute_commands() -> None:
    yaml = build_custom_entities_yaml(BASE, SOURCES)
    assert "    VOLUME_UP:" in yaml
    assert "    VOLUME_DOWN:" in yaml
    assert "    MUTE_ON:" in yaml
    assert "    MUTE_OFF:" in yaml
    assert "    MUTE_TOGGLE:" in yaml


def test_yaml_has_source_simple_commands() -> None:
    yaml = build_custom_entities_yaml(BASE, SOURCES)
    assert "    SRC_CD:" in yaml
    assert "    SRC_HDMI1:" in yaml
    # SAT/CBL should be normalized to SRC_SAT_CBL
    assert "    SRC_SAT_CBL:" in yaml


def test_yaml_source_parameter_uses_raw_func_code() -> None:
    yaml = build_custom_entities_yaml(BASE, SOURCES)
    # The SI command target must use the original func code, not the cmd name
    assert "/api/avr/source/SAT/CBL" in yaml


def test_yaml_has_selects_section() -> None:
    yaml = build_custom_entities_yaml(BASE, SOURCES)
    assert "  Selects:" in yaml
    assert "    Source:" in yaml


def test_yaml_selects_entries() -> None:
    yaml = build_custom_entities_yaml(BASE, SOURCES)
    assert "    - SRC_CD: CD" in yaml
    assert "    - SRC_HDMI1: HDMI 1" in yaml
    assert "    - SRC_SAT_CBL: CBL/SAT" in yaml


def test_yaml_empty_sources_no_selects() -> None:
    yaml = build_custom_entities_yaml(BASE, [])
    assert "Selects" not in yaml


def test_yaml_all_lines_use_two_space_indent() -> None:
    """Ensure no TAB characters appear; UC requires spaces."""
    yaml = build_custom_entities_yaml(BASE, SOURCES)
    assert "\t" not in yaml
