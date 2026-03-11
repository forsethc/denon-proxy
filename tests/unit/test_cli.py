"""Unit tests for denon_proxy.cli."""

from __future__ import annotations

from io import StringIO

import pytest

from denon_proxy.cli import build_parser, main as cli_main
from denon_proxy.runtime.config import Config


def test_cli_shows_help_when_no_command(capsys: pytest.CaptureFixture[str]) -> None:
    """Invoking CLI with no subcommand prints help and exits with code 1."""
    rc = cli_main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert "usage:" in captured.out


def test_cli_check_config_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """check-config exits 0 and prints success for a valid config."""
    import denon_proxy.main as main_mod
    import denon_proxy.cli as cli_mod

    monkeypatch.setattr(main_mod, "_load_config_and_report_errors", lambda path: Config(log_level="INFO"))
    stdout = StringIO()
    with pytest.MonkeyPatch().context() as m:
        m.setattr("sys.stdout", stdout, raising=True)
        rc = cli_main(["check-config"])
    assert rc == 0
    assert "Configuration is valid." in stdout.getvalue()


def test_cli_check_config_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """check-config exits 1 and prints validation errors for an invalid config."""
    import denon_proxy.main as main_mod
    from denon_proxy.runtime.config import Config as RuntimeConfig

    # Patch load_config so the real helper runs and sees a ValidationError.
    def bad_load_config(_path):
        return RuntimeConfig.load_from_dict({"avr_port": 0}, env={})

    monkeypatch.setattr(main_mod, "load_config", bad_load_config)
    stderr = StringIO()
    with pytest.MonkeyPatch().context() as m:
        m.setattr("sys.stderr", stderr, raising=True)
        rc = cli_main(["check-config"])
    assert rc == 1
    out = stderr.getvalue()
    assert "Config validation failed:" in out
    assert "avr_port" in out

