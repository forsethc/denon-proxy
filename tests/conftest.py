"""
Pytest configuration: register test class markers (unit, integration, e2e).

Run by class:
  pytest -m unit
  pytest -m integration
  pytest -m e2e
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path so root-level modules (avr_connection,
# denon_proxy, http_server, etc.) import regardless of pytest cwd.
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "unit: tests that exercise a single component with mocked or pure dependencies (tests/unit/).",
    )
    config.addinivalue_line(
        "markers",
        "integration: tests that exercise multiple real components together, not the full app (tests/integration/).",
    )
    config.addinivalue_line(
        "markers",
        "e2e: end-to-end tests that run the full application stack, production-like (tests/e2e/).",
    )


def pytest_collection_modifyitems(config, items):
    """Apply unit/integration/e2e marker based on test path so -m unit/integration/e2e works."""
    tests_dir = Path(__file__).resolve().parent
    for item in items:
        try:
            rel = item.path.relative_to(tests_dir)
        except ValueError:
            continue
        parts = rel.parts
        if not parts:
            continue
        if parts[0] == "unit":
            item.add_marker(pytest.mark.unit)
        elif parts[0] == "integration":
            item.add_marker(pytest.mark.integration)
        elif parts[0] == "e2e":
            item.add_marker(pytest.mark.e2e)
