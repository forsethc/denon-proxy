"""Unit tests for denon_proxy config helpers."""

import os
from unittest.mock import patch

from denon_proxy import load_config_from_dict, _apply_env_overrides


def test_load_config_from_dict_empty_returns_defaults():
    config = load_config_from_dict({})
    assert config["avr_port"] == 23
    assert config["proxy_port"] == 23
    assert config["log_level"] == "INFO"
    assert config["enable_ssdp"] is True


def test_load_config_from_dict_partial_merges():
    config = load_config_from_dict({"avr_host": "192.168.1.1"})
    assert config["avr_host"] == "192.168.1.1"
    assert config["avr_port"] == 23
    assert config["proxy_port"] == 23


def test_load_config_from_dict_overwrites_defaults():
    config = load_config_from_dict({"proxy_port": 2323, "log_level": "DEBUG"})
    assert config["proxy_port"] == 2323
    assert config["log_level"] == "DEBUG"


def test_load_config_from_dict_none_treated_as_empty():
    config = load_config_from_dict(None)
    assert config["avr_port"] == 23


def test_apply_env_overrides_sets_values_when_env_set():
    config = {"avr_host": "", "avr_port": 23, "proxy_host": "0.0.0.0", "proxy_port": 23, "log_level": "INFO", "denonavr_log_level": "INFO"}
    with patch.dict(os.environ, {"AVR_HOST": "10.0.0.1", "AVR_PORT": "24", "PROXY_PORT": "2323", "LOG_LEVEL": "DEBUG", "DENONAVR_LOG_LEVEL": "WARNING"}, clear=False):
        _apply_env_overrides(config)
    assert config["avr_host"] == "10.0.0.1"
    assert config["avr_port"] == 24
    assert config["proxy_port"] == 2323
    assert config["log_level"] == "DEBUG"
    assert config["denonavr_log_level"] == "WARNING"


def test_apply_env_overrides_leaves_config_unchanged_when_env_unset():
    config = {"avr_host": "192.168.1.1", "avr_port": 23}
    with patch.dict(os.environ, {}, clear=True):
        _apply_env_overrides(config)
    assert config["avr_host"] == "192.168.1.1"
    assert config["avr_port"] == 23
