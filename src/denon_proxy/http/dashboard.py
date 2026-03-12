from __future__ import annotations

from pathlib import Path


def load_dashboard_html(path: Path | None = None) -> str | None:
    """
    Load the Web UI HTML dashboard from http/web_ui.html.

    Returns the HTML string, or None if the file is missing or unreadable.
    path: optional path for tests; when None, uses http/web_ui.html in this package.
    """
    html_path = path if path is not None else Path(__file__).parent / "web_ui.html"
    try:
        return html_path.read_text(encoding="utf-8")
    except OSError:
        return None
