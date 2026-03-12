from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO", denonavr_log_level: str | None = None) -> None:
    """Configure logging format and level.

    denonavr_log_level sets the denonavr library logger separately.
    When stdout is not a TTY (e.g. systemd captures it), we use a format
    without timestamp/name so journal/syslog doesn't duplicate them.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    if sys.stdout.isatty():
        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"
    else:
        fmt = "[%(levelname)s] %(message)s"
        datefmt = None
    logging.basicConfig(
        level=log_level,
        format=fmt,
        datefmt=datefmt,
    )
    # Reduce noise from libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if denonavr_log_level is not None:
        logging.getLogger("denonavr").setLevel(getattr(logging, denonavr_log_level.upper(), logging.INFO))
