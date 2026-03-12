"""Unit tests for denon_proxy.main entrypoints (CLI and async runner)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from denon_proxy.main import main, main_async, run_proxy
from denon_proxy.runtime.config import Config


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

    with (
        patch(
            "denon_proxy.runtime.config_io.load_config",
            return_value=Config(log_level="INFO"),
        ),
        patch("denon_proxy.main.setup_logging"),
        patch(
            "denon_proxy.main.asyncio.run",
            side_effect=KeyboardInterrupt,
        ),
        patch("sys.argv", ["denon_proxy"]),
    ):
        assert main() == 0


def test_main_returns_0_on_successful_run():
    """When the proxy runs and exits normally, main() returns 0."""

    with (
        patch(
            "denon_proxy.runtime.config_io.load_config",
            return_value=Config(log_level="INFO"),
        ),
        patch("denon_proxy.main.setup_logging"),
        patch("denon_proxy.main.asyncio.run", return_value=None),
        patch(
            "sys.argv",
            ["denon_proxy"],
        ),
    ):
        assert main() == 0


def test_run_proxy_returns_1_when_config_load_fails():
    """run_proxy returns 1 when load_config_and_report_errors returns None."""
    with patch("denon_proxy.main.load_config_and_report_errors", return_value=None):
        assert run_proxy(Path("config.yaml")) == 1


def test_run_proxy_import_error_prints_and_returns_1(capsys):
    """run_proxy catches ImportError from asyncio.run and prints a message."""
    cfg = Config(log_level="INFO")
    with (
        patch("denon_proxy.main.load_config_and_report_errors", return_value=cfg),
        patch("denon_proxy.main.setup_logging"),
        patch("denon_proxy.main.asyncio.run", side_effect=ImportError("boom")),
    ):
        rc = run_proxy(Path("config.yaml"))
    assert rc == 1
    captured = capsys.readouterr()
    assert "Import error:" in captured.err
    assert "boom" in captured.err


def test_run_proxy_success_path():
    """run_proxy returns 0 when asyncio.run(main_async) completes successfully."""
    cfg = Config(log_level="INFO")
    with (
        patch("denon_proxy.main.load_config_and_report_errors", return_value=cfg),
        patch("denon_proxy.main.setup_logging"),
        patch("denon_proxy.main.asyncio.run", return_value=None) as run_mock,
    ):
        rc = run_proxy(Path("config.yaml"))
    assert rc == 0
    run_mock.assert_called_once()


@pytest.mark.asyncio
async def test_main_async_starts_and_shuts_down_proxy_and_discovery(monkeypatch):
    """main_async wires proxy + discovery and performs graceful shutdown."""

    events: dict[str, bool] = {}

    class FakeProxy:
        def __init__(self, config, logger, avr_factory, runtime_state):
            self.config = config
            self.logger = logger
            self.avr_state = object()
            self.runtime_state = runtime_state
            self.started = False
            self.stopped = False

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    class FakeEvent:
        def __init__(self):
            events["created"] = True

        def set(self):
            events["set"] = True

        async def wait(self):
            events["waited"] = True
            return

    class FakeServer:
        def __init__(self):
            self.closed = False
            self.wait_closed_called = False

        def close(self):
            self.closed = True

        async def wait_closed(self):
            self.wait_closed_called = True

        # For isinstance(http_server, list) check: ensure this is not a list

    class FakeTransport:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    async def fake_run_discovery_servers(config, logger, avr_state, runtime_state):
        return FakeTransport(), FakeServer()

    class FakeLoop:
        def add_signal_handler(self, *_args, **_kwargs):
            # Force the NotImplementedError branch to exercise Windows-style signal setup.
            raise NotImplementedError

    monkeypatch.setattr("denon_proxy.main.DenonProxyServer", FakeProxy)
    monkeypatch.setattr("denon_proxy.main.run_discovery_servers", fake_run_discovery_servers)
    monkeypatch.setattr("denon_proxy.main.get_version", lambda: "1.2.3")
    monkeypatch.setattr("denon_proxy.main.asyncio.Event", FakeEvent)
    monkeypatch.setattr("denon_proxy.main.asyncio.get_running_loop", lambda: FakeLoop())

    config = Config(log_level="INFO", enable_ssdp=True)

    await main_async(config)

    # Ensure our fake event was created and waited on so shutdown path ran.
    assert events.get("created") is True
    assert events.get("waited") is True
