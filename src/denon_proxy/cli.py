from __future__ import annotations

import argparse
import ipaddress
import logging
import sys
from pathlib import Path
from typing import Any

from denon_proxy.main import run_proxy
from denon_proxy.runtime.config_io import load_config_and_report_errors
from denon_proxy.utils.utils import get_version


def _add_version_subcommand(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser(
        "version",
        help="Print denon-proxy version",
        description="Print the installed denon-proxy version.",
    )
    parser.set_defaults(func=_cmd_version)


def _add_check_config_subcommand(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser(
        "check-config",
        help="Validate configuration and exit",
        description="Validate the denon-proxy configuration file and exit without starting the proxy.",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to config YAML (default: config.yaml in current working directory)",
    )
    parser.set_defaults(func=_cmd_check_config)


def _add_run_subcommand(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser(
        "run",
        help="Start the proxy server",
        description="Start the Denon AVR proxy server.",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to config YAML (default: config.yaml in current working directory)",
    )
    parser.set_defaults(func=_cmd_run)


def _add_discover_subcommand(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser(
        "discover",
        help="Find Denon/Marantz AVRs on the network",
        description="Discover AVRs via SSDP (UPnP) and/or mDNS (Bonjour). Use --method to choose. "
        "If SSDP finds the proxy but not the real AVR, try --method mdns or --method both.",
    )
    parser.add_argument(
        "--method",
        "-m",
        choices=("ssdp", "mdns", "both"),
        default="both",
        help="Discovery method: ssdp (UPnP M-SEARCH), mdns (Bonjour; requires zeroconf), or both (default: both)",
    )
    parser.add_argument(
        "--timeout",
        "-t",
        type=float,
        default=5.0,
        metavar="SECS",
        help="Seconds to wait for responses (default: 5.0)",
    )
    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        help="Output results as JSON (one object per line or single list)",
    )
    parser.add_argument(
        "-a",
        "--show-all",
        action="store_true",
        help="Show all discovered devices, including those filtered out (non-Denon/Marantz)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging (SSDP/mDNS discovery details)",
    )
    parser.set_defaults(func=_cmd_discover)


def _cmd_version(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Print the current denon-proxy version."""
    print(get_version())
    return 0


def _cmd_check_config(args: argparse.Namespace) -> int:
    """Validate configuration and report any errors."""
    config = load_config_and_report_errors(args.config)
    if config is None:
        return 1

    print("Configuration is valid.")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """Start the proxy server (same behavior as python -m denon_proxy.main)."""
    return run_proxy(args.config)


def _cmd_discover(args: argparse.Namespace) -> int:
    """Discover AVRs on the network and print host/port (and optional name)."""
    import asyncio
    import json
    from denon_proxy.avr.discover import discover, mdns_available

    if getattr(args, "verbose", False):
        from denon_proxy.runtime.logging import setup_logging
        setup_logging("DEBUG")
        logging.getLogger("zeroconf").setLevel(logging.WARNING)
        logging.getLogger("asyncio").setLevel(logging.WARNING)

    if args.method == "mdns" and not mdns_available():
        print(
            "mDNS discovery requires the 'zeroconf' package.\n"
            "Install it with: pip install zeroconf\n"
            "Or install denon-proxy with the discover extra: pip install denon-proxy[discover]",
            file=sys.stderr,
        )
        return 1

    async def run() -> list:
        return await discover(
            method=args.method,
            timeout=args.timeout,
            include_filtered=getattr(args, "show_all", False),
        )

    try:
        results = asyncio.run(run())
    except (OSError, asyncio.TimeoutError) as e:
        print(f"Discovery failed: {e}", file=sys.stderr)
        return 1

    def _ip_sort_key(avr: Any) -> tuple[int, Any]:
        try:
            return (0, ipaddress.ip_address(avr.host))
        except ValueError:
            return (1, avr.host)

    show_all = getattr(args, "show_all", False)
    matched = [r for r in results if r.matched]
    filtered = [r for r in results if not r.matched]
    matched.sort(key=_ip_sort_key)
    filtered.sort(key=_ip_sort_key)

    if args.json:
        if show_all:
            out = {
                "matched": [r.as_dict() for r in matched],
                "filtered": [r.as_dict() for r in filtered],
            }
        else:
            out = [r.as_dict() for r in matched]
        print(json.dumps(out, indent=2))
        return 0

    if not results:
        print("No AVRs found. Try --method both or increase --timeout.")
        if args.method == "both" and not mdns_available():
            print("(mDNS was skipped; install 'zeroconf' for mDNS: pip install zeroconf)", file=sys.stderr)
        return 0

    def _print_avr_list(avrs: list, show_filtered_suffix: bool = False) -> None:
        for avr in avrs:
            name_part = f"  # {avr.name}" if avr.name else ""
            suffix = "  (filtered)" if show_filtered_suffix else ""
            print(f"{avr.host}:{avr.port}{name_part}{suffix}")

    if show_all:
        if matched:
            print("Denon/Marantz AVRs:")
            _print_avr_list(matched)
        if filtered:
            if matched:
                print()
            print("Other discovered devices (filtered):")
            _print_avr_list(filtered, show_filtered_suffix=False)
    else:
        _print_avr_list(matched)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI argument parser.

    Exposed for unit tests so they can construct and exercise the CLI
    without invoking subprocesses.
    """
    parser = argparse.ArgumentParser(
        prog="denon-proxy",
        description="Command-line interface for denon-proxy.",
    )
    subparsers = parser.add_subparsers(
        title="subcommands",
        dest="command",
        metavar="<command>",
    )

    _add_version_subcommand(subparsers)
    _add_check_config_subcommand(subparsers)
    _add_run_subcommand(subparsers)
    _add_discover_subcommand(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Example:
        python -m denon_proxy.cli version
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 1

    return int(func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
