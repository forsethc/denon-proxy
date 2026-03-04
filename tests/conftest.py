from __future__ import annotations

import sys
from pathlib import Path

import pytest


# Ensure the project root is on sys.path so tests can import modules like `avr_state`.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def reset_avr_discovery_cached_name():
    """
    Reset avr_discovery's cached friendly name before each test.

    get_proxy_friendly_name caches into a module-level _cached_friendly_name;
    without resetting it, tests that depend on different configs can interfere.
    """
    try:
        import avr_discovery  # type: ignore
    except ImportError:
        yield
        return

    # Reset before the test
    if hasattr(avr_discovery, "_cached_friendly_name"):
        avr_discovery._cached_friendly_name = None  # type: ignore[attr-defined]

    yield

    # Also reset after the test in case something set it during the test
    if hasattr(avr_discovery, "_cached_friendly_name"):
        avr_discovery._cached_friendly_name = None  # type: ignore[attr-defined]


