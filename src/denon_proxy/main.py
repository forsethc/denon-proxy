#!/usr/bin/env python3
"""
Denon AVR Proxy - Virtual Denon AVR for multiple clients.

This proxy allows Home Assistant, UC Remote 3, and other Denon-compatible
clients to connect simultaneously, while maintaining a single Telnet
connection to the physical AVR.

Usage:
    python denon_proxy.py [--config config.yaml]

    Or with environment variables:
    AVR_HOST=192.168.1.100 PROXY_PORT=2323 python denon_proxy.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import pprint
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from denon_proxy.avr.connection import create_avr_connection
from denon_proxy.avr.discovery import run_discovery_servers
from denon_proxy.constants import SHUTDOWN_PROXY_WAIT, SHUTDOWN_SERVER_WAIT
from denon_proxy.proxy.core import DenonProxyServer
from denon_proxy.runtime.config import Config
from denon_proxy.runtime.config_io import load_config_and_report_errors
from denon_proxy.runtime.logging import setup_logging
from denon_proxy.runtime.state import RuntimeState
from denon_proxy.utils.utils import get_version

if TYPE_CHECKING:
    from denon_proxy.runtime.config import Config


async def main_async(config: Config) -> None:
    """Run the proxy server."""
    logger = logging.getLogger("denon-proxy")
    logger.debug(
        "Starting proxy with config:\n%s",
        pprint.pformat(dict(config), width=88, sort_dicts=True),
    )
    runtime_state = RuntimeState()
    runtime_state.version = get_version()
    logger.info("denon-proxy %s", runtime_state.version)
    proxy = DenonProxyServer(config, logger, create_avr_connection, runtime_state)

    # Handle shutdown gracefully - must not block event loop or Ctrl-C won't work
    stop_event = asyncio.Event()
    _shutting_down = False

    def shutdown() -> None:
        nonlocal _shutting_down
        if _shutting_down:
            logger.warning("Second Ctrl-C: forcing exit")
            os._exit(1)
        _shutting_down = True
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown)
    except NotImplementedError:
        # add_signal_handler not supported on Windows - use signal.signal
        try:
            signal.signal(signal.SIGINT, lambda s, f: shutdown())
            signal.signal(signal.SIGTERM, lambda s, f: shutdown())
        except (ValueError, OSError):
            pass
    except OSError:
        # add_signal_handler can fail (e.g. main thread check) - try signal.signal
        try:
            signal.signal(signal.SIGINT, lambda s, f: shutdown())
            signal.signal(signal.SIGTERM, lambda s, f: shutdown())
        except (ValueError, OSError):
            pass

    await proxy.start()

    ssdp_transport, http_server = None, None
    if config.get("enable_ssdp"):
        try:
            ssdp_transport, http_server = await run_discovery_servers(config, logger, proxy.avr_state, runtime_state)
        except (OSError, asyncio.TimeoutError) as e:
            logger.warning("SSDP failed: %s", e)

    await stop_event.wait()

    logger.info("Shutting down...")
    try:
        if ssdp_transport:
            ssdp_transport.close()
        if http_server:
            servers = http_server if isinstance(http_server, list) else [http_server]
            for srv in servers:
                srv.close()
            for srv in servers:
                await asyncio.wait_for(srv.wait_closed(), timeout=SHUTDOWN_SERVER_WAIT)
        await asyncio.wait_for(proxy.stop(), timeout=SHUTDOWN_PROXY_WAIT)
    except asyncio.TimeoutError:
        logger.warning("Shutdown timed out, exiting anyway")


def run_proxy(config_path: Path | None) -> int:
    """
    Run the proxy server given an optional config path.

    Shared by the module entrypoint (__main__) and the CLI 'run' subcommand.
    """
    config = load_config_and_report_errors(config_path)
    if config is None:
        return 1

    setup_logging(config["log_level"], config.get("denonavr_log_level"))

    try:
        asyncio.run(main_async(config))
    except KeyboardInterrupt:
        return 0
    except ImportError as e:
        print(f"Import error: {e}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    """Parse arguments and run the proxy."""
    parser = argparse.ArgumentParser(description="Denon AVR Proxy - Virtual AVR for multiple clients")
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to config YAML (default: config.yaml in current working directory)",
    )
    args = parser.parse_args()

    return run_proxy(args.config)


if __name__ == "__main__":
    sys.exit(main())
