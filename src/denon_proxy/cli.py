from __future__ import annotations

import argparse
import sys

from denon_proxy.utils.utils import get_version


def _add_version_subcommand(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "version",
        help="Print denon-proxy version",
        description="Print the installed denon-proxy version.",
    )
    parser.set_defaults(func=_cmd_version)


def _cmd_version(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Print the current denon-proxy version."""
    print(get_version())
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

