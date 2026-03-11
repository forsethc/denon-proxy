from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from denon_proxy.main import _load_config_and_report_errors, run_proxy
from denon_proxy.utils.utils import get_version


def _add_version_subcommand(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "version",
        help="Print denon-proxy version",
        description="Print the installed denon-proxy version.",
    )
    parser.set_defaults(func=_cmd_version)


def _add_check_config_subcommand(subparsers: argparse._SubParsersAction) -> None:
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


def _add_run_subcommand(subparsers: argparse._SubParsersAction) -> None:
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


def _cmd_version(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Print the current denon-proxy version."""
    print(get_version())
    return 0


def _cmd_check_config(args: argparse.Namespace) -> int:
    """Validate configuration and report any errors."""
    config = _load_config_and_report_errors(args.config)
    if config is None:
        return 1

    print("Configuration is valid.")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """Start the proxy server (same behavior as python -m denon_proxy.main)."""
    return run_proxy(args.config)


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

