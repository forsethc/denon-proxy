"""
Config validation contract tests.

Goal: we must not reject valid configs (no false negatives) and must reject
invalid configs (no false positives). Each validation rule in runtime.config
is tested with:
  - At least one valid value (including boundaries) that is accepted.
  - At least one invalid value that raises ValidationError with an identifiable message.

Maintenance: when adding or changing a validator in runtime.config.Config, add
(or update) matching entries in INVALID_CONFIGS and/or VALID_BOUNDARY_CONFIGS
so the contract stays complete. Run this module alone to quickly check all
validation rules: pytest tests/unit/test_denon_proxy_config_validation.py -v
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from denon_proxy.runtime.config import Config


# -----------------------------------------------------------------------------
# Invalid configs: each must raise ValidationError; we assert on message content
# so we know the right validator fired (and don't accidentally allow invalid data).
# -----------------------------------------------------------------------------

INVALID_CONFIGS = [
    # (raw_overrides, expected_substrings_in_error)
    pytest.param(
        {"avr_host": "host with spaces"},
        ["hostname", "space"],
        id="avr_host_spaces",
    ),
    pytest.param(
        {"avr_host": "host\twith\ttabs"},
        ["hostname", "space"],
        id="avr_host_tabs",
    ),
    pytest.param(
        {"avr_port": 0},
        ["avr_port", "1"],
        id="avr_port_zero",
    ),
    pytest.param(
        {"avr_port": 65536},
        ["avr_port", "65535"],
        id="avr_port_over_max",
    ),
    pytest.param(
        {"avr_port": -1},
        ["avr_port"],
        id="avr_port_negative",
    ),
    pytest.param(
        {"proxy_host": ""},
        ["proxy_host", "at least 1"],
        id="proxy_host_empty",
    ),
    pytest.param(
        {"proxy_port": -1},
        ["proxy_port"],
        id="proxy_port_negative",
    ),
    pytest.param(
        {"proxy_port": 65536},
        ["proxy_port"],
        id="proxy_port_over_max",
    ),
    pytest.param(
        {"log_level": "TRACE"},
        ["log_level", "DEBUG", "INFO", "WARNING", "ERROR"],
        id="log_level_invalid",
    ),
    pytest.param(
        {"denonavr_log_level": "VERBOSE"},
        ["denonavr_log_level", "DEBUG", "INFO", "WARNING", "ERROR"],
        id="denonavr_log_level_invalid",
    ),
    pytest.param(
        {"ssdp_http_port": -1},
        ["ssdp_http_port"],
        id="ssdp_http_port_negative",
    ),
    pytest.param(
        {"ssdp_advertise_ip": "not.an.ip"},
        ["ssdp_advertise_ip", "ip", "address"],
        id="ssdp_advertise_ip_invalid",
    ),
    pytest.param(
        {"optimistic_broadcast_delay": -0.1},
        ["optimistic_broadcast_delay"],
        id="optimistic_broadcast_delay_negative",
    ),
    pytest.param(
        {"volume_step": 0.25},
        ["volume_step", "0.5"],
        id="volume_step_not_half_step",
    ),
    pytest.param(
        {"volume_step": -0.5},
        ["volume_step"],
        id="volume_step_negative",
    ),
    pytest.param(
        {"volume_query_delay": -0.1},
        ["volume_query_delay"],
        id="volume_query_delay_negative",
    ),
    pytest.param(
        {"http_port": -1},
        ["http_port"],
        id="http_port_negative",
    ),
    pytest.param(
        {"log_command_groups_info": ["power", "invalid_group"]},
        ["invalid", "group"],
        id="log_command_groups_info_invalid_group",
    ),
    pytest.param(
        {"client_activity_log_max_entries": 0},
        ["client_activity_log_max_entries", "1"],
        id="client_activity_log_max_entries_zero",
    ),
    pytest.param(
        {"sources": {"CD": 123}},
        ["sources", "string"],
        id="sources_value_not_string",
    ),
    pytest.param(
        {"sources": {42: "CD"}},
        ["sources", "string"],
        id="sources_key_not_string",
    ),
    pytest.param(
        {"typo_key": "value"},
        ["extra", "forbidden"],
        id="extra_key_forbidden",
    ),
    pytest.param(
        {"avr_port": "not_a_number"},
        ["avr_port", "int"],
        id="avr_port_wrong_type",
    ),
    pytest.param(
        {"log_level": 123},
        ["log_level", "string"],
        id="log_level_wrong_type",
    ),
]


def _error_matches(err: ValidationError, substrings: list[str]) -> bool:
    """True if the validation error (all messages concatenated) contains each substring (case-insensitive)."""
    text = str(err).lower()
    return all(s.lower() in text for s in substrings)


@pytest.mark.parametrize("raw_overrides,expected_substrings", INVALID_CONFIGS)
def test_invalid_config_rejected(raw_overrides: dict, expected_substrings: list[str]) -> None:
    """Invalid configs must raise ValidationError with a message identifying the problem."""
    raw = dict(raw_overrides)
    with pytest.raises(ValidationError) as exc_info:
        # Use empty env mapping so contract tests are independent of process environment.
        Config.load_from_dict(raw, env={})
    assert _error_matches(exc_info.value, expected_substrings), (
        f"Expected error to contain all of {expected_substrings!r}; got: {exc_info.value!s}"
    )


# -----------------------------------------------------------------------------
# Valid configs: boundaries and edge values that must be accepted
# -----------------------------------------------------------------------------

VALID_BOUNDARY_CONFIGS = [
    pytest.param({}, id="empty_dict_defaults"),
    pytest.param({"avr_host": ""}, id="avr_host_empty"),
    pytest.param({"avr_host": "192.168.1.1"}, id="avr_host_ip"),
    pytest.param({"avr_host": "my-avr.local"}, id="avr_host_hostname"),
    pytest.param({"avr_port": 1}, id="avr_port_min"),
    pytest.param({"avr_port": 65535}, id="avr_port_max"),
    pytest.param({"proxy_host": "0.0.0.0"}, id="proxy_host_default"),
    pytest.param({"proxy_host": "127.0.0.1"}, id="proxy_host_localhost"),
    pytest.param({"proxy_port": 0}, id="proxy_port_zero"),
    pytest.param({"proxy_port": 65535}, id="proxy_port_max"),
    pytest.param({"log_level": "debug"}, id="log_level_lowercase"),
    pytest.param({"log_level": "ERROR"}, id="log_level_uppercase"),
    pytest.param({"denonavr_log_level": "warning"}, id="denonavr_log_level_lowercase"),
    pytest.param({"ssdp_http_port": 0}, id="ssdp_http_port_zero"),
    pytest.param({"ssdp_http_port": 65535}, id="ssdp_http_port_max"),
    pytest.param({"ssdp_advertise_ip": ""}, id="ssdp_advertise_ip_empty"),
    pytest.param({"ssdp_advertise_ip": "192.168.1.1"}, id="ssdp_advertise_ip_valid"),
    pytest.param({"ssdp_advertise_ip": "::1"}, id="ssdp_advertise_ip_ipv6"),
    pytest.param({"optimistic_broadcast_delay": 0}, id="optimistic_broadcast_delay_zero"),
    pytest.param({"volume_step": 0}, id="volume_step_zero"),
    pytest.param({"volume_step": 0.5}, id="volume_step_half"),
    pytest.param({"volume_step": 1.0}, id="volume_step_one"),
    pytest.param({"volume_step": 2.5}, id="volume_step_two_half"),
    pytest.param({"volume_query_delay": 0}, id="volume_query_delay_zero"),
    pytest.param({"http_port": 0}, id="http_port_zero"),
    pytest.param({"http_port": 65535}, id="http_port_max"),
    pytest.param(
        {"log_command_groups_info": ["power", "volume", "input", "mute", "sound_mode", "smart_select", "other"]},
        id="log_command_groups_info_all_valid",
    ),
    pytest.param({"log_command_groups_info": []}, id="log_command_groups_info_empty"),
    pytest.param({"client_activity_log_max_entries": 1}, id="client_activity_log_max_entries_min"),
    pytest.param({"sources": None}, id="sources_none"),
    pytest.param({"sources": {}}, id="sources_empty_dict"),
    pytest.param({"sources": {"CD": "CD Player", "HDMI1": "Apple TV"}}, id="sources_valid_dict"),
    pytest.param({"ssdp_friendly_name": None}, id="ssdp_friendly_name_none"),
    pytest.param({"ssdp_friendly_name": "  "}, id="ssdp_friendly_name_whitespace_becomes_none"),
    pytest.param({"ssdp_friendly_name": " My AVR "}, id="ssdp_friendly_name_stripped"),
    pytest.param({"client_aliases": {}}, id="client_aliases_empty"),
    pytest.param({"client_aliases": {"192.168.1.5": "Living Room"}}, id="client_aliases_valid"),
]


@pytest.mark.parametrize("raw_overrides", VALID_BOUNDARY_CONFIGS)
def test_valid_config_accepted(raw_overrides: dict) -> None:
    """Valid configs (including boundary values) must be accepted without raising."""
    # Use empty env mapping so contract tests are independent of process environment.
    config = Config.load_from_dict(raw_overrides, env={})
    assert config is not None
    # Spot-check: overrides we passed are reflected (with normalizations the model applies)
    for key, value in raw_overrides.items():
        if value is None and key == "ssdp_friendly_name":
            assert config.get(key) is None
        elif key == "ssdp_friendly_name" and isinstance(value, str) and not value.strip():
            assert config.get(key) is None
        elif key == "ssdp_friendly_name" and isinstance(value, str):
            assert config[key] == value.strip()  # model strips whitespace
        elif key in ("log_level", "denonavr_log_level") and isinstance(value, str):
            assert config[key].upper() == value.upper()  # model normalizes to upper
        elif key == "avr_host" and value == "":
            assert config.get("avr_host") == ""
        else:
            assert config[key] == value


def test_maximal_valid_config_accepted() -> None:
    """A config with every key set to a valid value must load."""
    maximal = {
        "avr_host": "192.168.1.1",
        "avr_port": 23,
        "proxy_host": "0.0.0.0",
        "proxy_port": 23,
        "log_level": "INFO",
        "denonavr_log_level": "WARNING",
        "enable_ssdp": True,
        "ssdp_http_port": 8080,
        "ssdp_advertise_ip": "192.168.1.1",
        "optimistic_state": True,
        "optimistic_broadcast_delay": 0.2,
        "volume_step": 0.5,
        "volume_query_delay": 0.2,
        "enable_http": True,
        "http_port": 8081,
        "log_command_groups_info": ["power", "volume"],
        "client_activity_log": True,
        "client_activity_log_max_entries": 100,
        "client_activity_log_hide_queries": False,
        "ssdp_friendly_name": "My Denon Proxy",
        "sources": {"CD": "CD", "HDMI1": "Apple TV"},
        "client_aliases": {"192.168.1.5": "HA"},
    }
    config = Config.load_from_dict(maximal, env={})
    assert config["avr_host"] == "192.168.1.1"
    assert config["ssdp_friendly_name"] == "My Denon Proxy"
    assert config["sources"] == {"CD": "CD", "HDMI1": "Apple TV"}


def test_extra_key_forbidden() -> None:
    """Unknown top-level keys are rejected (extra='forbid')."""
    with pytest.raises(ValidationError) as exc_info:
        Config.load_from_dict({"avr_host": "", "unknown_field": "x"}, env={})
    assert "extra" in str(exc_info.value).lower() or "unknown_field" in str(exc_info.value).lower()
