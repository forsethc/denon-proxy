"""Unit tests for denon_proxy config helpers."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from denon_proxy.main import _load_dashboard_html, load_config_from_dict, main
from denon_proxy.runtime.config import Config
from denon_proxy.runtime.config_io import _load_config_dict_from_file, load_config


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


def test_load_from_dict_applies_env_overrides_when_env_set():
    """Config.load_from_dict merges env vars into raw dict before validation."""
    with patch.dict(
        os.environ,
        {
            "AVR_HOST": "10.0.0.1",
            "AVR_PORT": "24",
            "PROXY_PORT": "2323",
            "LOG_LEVEL": "DEBUG",
            "DENONAVR_LOG_LEVEL": "WARNING",
        },
        clear=False,
    ):
        config = Config.load_from_dict({"avr_host": "", "avr_port": 23})
    assert config["avr_host"] == "10.0.0.1"
    assert config["avr_port"] == 24
    assert config["proxy_port"] == 2323
    assert config["log_level"] == "DEBUG"
    assert config["denonavr_log_level"] == "WARNING"


def test_load_from_dict_leaves_config_unchanged_when_env_unset():
    with patch.dict(os.environ, {}, clear=True):
        config = Config.load_from_dict({"avr_host": "192.168.1.1", "avr_port": 23})
    assert config["avr_host"] == "192.168.1.1"
    assert config["avr_port"] == 23


def test_client_display_for_log_uses_alias_when_configured():
    """Config.client_display_for_log returns 'alias (ip)' when client_aliases set, else ip."""
    config = load_config_from_dict({"client_aliases": {"192.168.1.5": "Living Room HA"}})
    assert config.client_display_for_log("192.168.1.5") == "Living Room HA (192.168.1.5)"
    assert config.client_display_for_log("10.0.0.1") == "10.0.0.1"
    assert config.client_display_for_log("?") == "?"
    config_empty = load_config_from_dict({})
    assert config_empty.client_display_for_log("192.168.1.5") == "192.168.1.5"


def test_load_config_dict_from_file_raises_file_not_found_when_path_missing():
    """When config_path is given and that file does not exist, raises FileNotFoundError."""
    pytest.importorskip("yaml")
    with pytest.raises(FileNotFoundError) as exc_info:
        _load_config_dict_from_file(Path("/nonexistent/config.yaml"))
    assert "Config not found" in str(exc_info.value)
    assert "nonexistent" in str(exc_info.value)


def test_load_config_dict_from_file_raises_import_error_when_yaml_unavailable():
    """When yaml module is not available, _load_config_dict_from_file raises ImportError with PyYAML hint."""
    with patch("denon_proxy.runtime.config_io.yaml", None):
        with pytest.raises(ImportError) as exc_info:
            _load_config_dict_from_file(Path("/any/path.yaml"))
        assert "PyYAML" in str(exc_info.value) or "pyyaml" in str(exc_info.value).lower()


def test_load_config_dict_from_file_raises_file_not_found_when_default_missing(monkeypatch):
    """When config_path is None and config.yaml is missing in cwd, raises FileNotFoundError."""
    pytest.importorskip("yaml")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        assert not (tmp_path / "config.yaml").exists()
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError) as exc_info:
            _load_config_dict_from_file(None)
    assert "Config not found" in str(exc_info.value)
    assert "config.yaml" in str(exc_info.value)


def test_load_config_dict_from_file_raises_value_error_when_yaml_not_dict():
    """YAML that is not a mapping (e.g. a list) raises ValueError."""
    pytest.importorskip("yaml")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("- a\n- b\n")
        path = Path(f.name)
    try:
        with pytest.raises(ValueError) as exc_info:
            _load_config_dict_from_file(path)
        msg = str(exc_info.value).lower()
        assert "mapping" in msg or "list" in msg
    finally:
        path.unlink(missing_ok=True)


def test_load_config_merges_file_and_applies_env_overrides():
    """load_config merges file content with defaults and applies env overrides."""
    pytest.importorskip("yaml")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("avr_host: 192.168.1.1\nproxy_port: 2323\n")
        path = Path(f.name)
    try:
        with patch.dict(os.environ, {"AVR_HOST": "10.0.0.2", "LOG_LEVEL": "DEBUG"}, clear=False):
            config = load_config(path)
        assert config["avr_host"] == "10.0.0.2"
        assert config["proxy_port"] == 2323
        assert config["log_level"] == "DEBUG"
        assert config["avr_port"] == 23
    finally:
        path.unlink(missing_ok=True)


def test_load_config_sets_optimistic_state_false_when_no_avr_host():
    """When no avr_host is specified (file + env), optimistic_state is set to False at parse time."""
    pytest.importorskip("yaml")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("proxy_port: 2323\noptimistic_state: true\n")
        path = Path(f.name)
    try:
        with patch.dict(os.environ, {}, clear=True):
            config = load_config(path)
        assert (config.get("avr_host") or "").strip() == ""
        assert config["optimistic_state"] is False
    finally:
        path.unlink(missing_ok=True)


def test_load_config_leaves_optimistic_state_when_avr_host_specified():
    """When avr_host is set (file or env), optimistic_state is left as configured."""
    pytest.importorskip("yaml")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("avr_host: 192.168.1.1\noptimistic_state: true\n")
        path = Path(f.name)
    try:
        with patch.dict(os.environ, {}, clear=True):
            config = load_config(path)
        assert (config.get("avr_host") or "").strip() != ""
        # With avr_host set, model_validator leaves optimistic_state as configured (True)
        assert config["optimistic_state"] is True
    finally:
        path.unlink(missing_ok=True)


def test_load_dashboard_html_returns_none_when_file_unreadable():
    """When the path is missing or unreadable, _load_dashboard_html returns None."""
    path = Path(__file__).parent / "does_not_exist_dashboard.html"
    result = _load_dashboard_html(path=path)
    assert result is None


def test_main_returns_1_when_config_not_found():
    """When config file is missing, main() returns 1 (and prints error to stderr)."""
    with (
        patch(
            "denon_proxy.runtime.config_io.load_config",
            side_effect=FileNotFoundError("Config not found: /missing.yaml"),
        ),
        patch("sys.argv", ["denon_proxy"]),
    ):
        assert main() == 1


def test_main_returns_0_on_keyboard_interrupt():
    """When the user hits Ctrl-C (KeyboardInterrupt), main() returns 0."""

    def raise_keyboard_interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    with (
        patch(
            "denon_proxy.runtime.config_io.load_config",
            return_value=Config(log_level="INFO"),
        ),
        patch(
            "denon_proxy.main.main_async",
            side_effect=raise_keyboard_interrupt,
        ),
        patch("sys.argv", ["denon_proxy"]),
    ):
        assert main() == 0


def test_main_returns_0_on_successful_run():
    """When the proxy runs and exits normally, main() returns 0."""

    async def noop(*_args, **_kwargs):
        pass

    with (
        patch(
            "denon_proxy.runtime.config_io.load_config",
            return_value=Config(log_level="INFO"),
        ),
        patch("denon_proxy.main.main_async", noop),
        patch(
            "sys.argv",
            ["denon_proxy"],
        ),
    ):
        assert main() == 0
