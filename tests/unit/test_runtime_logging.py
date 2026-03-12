from __future__ import annotations

from denon_proxy.runtime.logging import setup_logging


def test_setup_logging_accepts_level_and_denonavr_level() -> None:
    """setup_logging runs without error and accepts optional denonavr_log_level."""
    setup_logging("INFO")
    setup_logging("DEBUG", denonavr_log_level="WARNING")
