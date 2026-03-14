"""Unit tests for denon_proxy.cli."""

from __future__ import annotations

from io import StringIO

import pytest

from denon_proxy.cli import main as cli_main
from denon_proxy.runtime.config import Config


def test_cli_shows_help_when_no_command(capsys: pytest.CaptureFixture[str]) -> None:
    """Invoking CLI with no subcommand prints help and exits with code 1."""
    rc = cli_main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert "usage:" in captured.out


def test_cli_check_config_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """check-config exits 0 and prints success for a valid config."""
    import denon_proxy.cli as cli_mod

    monkeypatch.setattr(cli_mod, "load_config_and_report_errors", lambda path: Config(log_level="INFO"))
    stdout = StringIO()
    with pytest.MonkeyPatch().context() as m:
        m.setattr("sys.stdout", stdout, raising=True)
        rc = cli_main(["check-config"])
    assert rc == 0
    assert "Configuration is valid." in stdout.getvalue()


def test_cli_check_config_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """check-config exits 1 and prints validation errors for an invalid config."""
    import denon_proxy.runtime.config_io as config_io_mod
    from denon_proxy.runtime.config import Config as RuntimeConfig

    # Patch load_config so the real helper runs and sees a ValidationError.
    def bad_load_config(_path):
        return RuntimeConfig.load_from_dict({"avr_port": 0}, env={})

    monkeypatch.setattr(config_io_mod, "load_config", bad_load_config)
    stderr = StringIO()
    with pytest.MonkeyPatch().context() as m:
        m.setattr("sys.stderr", stderr, raising=True)
        rc = cli_main(["check-config"])
    assert rc == 1
    out = stderr.getvalue()
    assert "Config validation failed:" in out
    assert "avr_port" in out


def test_cli_run_uses_helper_and_runs_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """run subcommand delegates to main.run_proxy and returns its code."""
    import denon_proxy.cli as cli_mod

    monkeypatch.setattr(cli_mod, "run_proxy", lambda path: 7)

    rc = cli_main(["run"])
    assert rc == 7


def test_cli_version_subcommand_prints_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """version subcommand prints version and exits 0."""
    import denon_proxy.cli as cli_mod

    monkeypatch.setattr(cli_mod, "get_version", lambda: "1.2.3-test")
    stdout = StringIO()
    with pytest.MonkeyPatch().context() as m:
        m.setattr("sys.stdout", stdout, raising=True)
        rc = cli_main(["version"])
    assert rc == 0
    assert stdout.getvalue().strip() == "1.2.3-test"


def test_cli_discover_subcommand_prints_found_avrs(monkeypatch: pytest.MonkeyPatch) -> None:
    """discover subcommand prints host:port lines when AVRs are found."""
    from denon_proxy.avr.discover import DiscoveredAVR

    async def fake_discover(*, method: str, timeout: float, **kwargs):
        return [
            DiscoveredAVR("192.168.1.100", 80, "Denon AVR-X", "http://192.168.1.100/desc.xml", "ssdp"),
        ]

    import denon_proxy.avr.discover as discover_mod
    monkeypatch.setattr(discover_mod, "discover", fake_discover)
    stdout = StringIO()
    with pytest.MonkeyPatch().context() as m:
        m.setattr("sys.stdout", stdout, raising=True)
        rc = cli_main(["discover", "--timeout", "1"])
    assert rc == 0
    out = stdout.getvalue()
    assert "192.168.1.100:80" in out
    assert "Denon AVR-X" in out


def test_cli_discover_subcommand_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """discover --json outputs JSON array of discovered AVRs."""
    import json
    from denon_proxy.avr.discover import DiscoveredAVR

    async def fake_discover(*, method: str, timeout: float, **kwargs):
        return [DiscoveredAVR("10.0.0.5", 80, None, None, "ssdp")]

    import denon_proxy.avr.discover as discover_mod
    monkeypatch.setattr(discover_mod, "discover", fake_discover)
    stdout = StringIO()
    with pytest.MonkeyPatch().context() as m:
        m.setattr("sys.stdout", stdout, raising=True)
        rc = cli_main(["discover", "--json", "--timeout", "1"])
    assert rc == 0
    data = json.loads(stdout.getvalue())
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["host"] == "10.0.0.5"
    assert data[0]["port"] == 80
    assert data[0]["method"] == "ssdp"


def test_cli_discover_subcommand_no_avrs_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    """discover with no results prints message and exits 0."""
    async def fake_discover(*, method: str, timeout: float, **kwargs):
        return []

    import denon_proxy.avr.discover as discover_mod
    monkeypatch.setattr(discover_mod, "discover", fake_discover)
    stdout = StringIO()
    with pytest.MonkeyPatch().context() as m:
        m.setattr("sys.stdout", stdout, raising=True)
        rc = cli_main(["discover", "--timeout", "0.1"])
    assert rc == 0
    assert "No AVRs found" in stdout.getvalue()


def test_cli_discover_show_all_includes_filtered(monkeypatch: pytest.MonkeyPatch) -> None:
    """discover --show-all shows matching devices on top, then a section for filtered devices."""
    import json
    from denon_proxy.avr.discover import DiscoveredAVR

    async def fake_discover(*, method: str, timeout: float, include_filtered: bool = False, **kwargs):
        return [
            DiscoveredAVR("192.168.1.1", 80, "Denon AVR", None, "ssdp", matched=True),
            DiscoveredAVR("192.168.1.2", 80, "Other Device", None, "ssdp", matched=False),
        ]

    import denon_proxy.avr.discover as discover_mod
    monkeypatch.setattr(discover_mod, "discover", fake_discover)
    stdout = StringIO()
    with pytest.MonkeyPatch().context() as m:
        m.setattr("sys.stdout", stdout, raising=True)
        rc = cli_main(["discover", "--show-all", "--timeout", "1"])
    assert rc == 0
    out = stdout.getvalue()
    assert "Denon/Marantz AVRs:" in out
    assert "192.168.1.1:80" in out
    assert "Other discovered devices (filtered):" in out
    assert "192.168.1.2:80" in out
    # Matched section appears before filtered section
    assert out.index("Denon/Marantz AVRs:") < out.index("Other discovered devices (filtered):")
    assert out.index("192.168.1.1:80") < out.index("192.168.1.2:80")

    stdout2 = StringIO()
    with pytest.MonkeyPatch().context() as m:
        m.setattr("sys.stdout", stdout2, raising=True)
        rc = cli_main(["discover", "--show-all", "--json", "--timeout", "1"])
    assert rc == 0
    data = json.loads(stdout2.getvalue())
    assert "matched" in data
    assert "filtered" in data
    assert len(data["matched"]) == 1
    assert data["matched"][0]["host"] == "192.168.1.1"
    assert data["matched"][0]["matched"] is True
    assert len(data["filtered"]) == 1
    assert data["filtered"][0]["host"] == "192.168.1.2"
    assert data["filtered"][0]["matched"] is False
