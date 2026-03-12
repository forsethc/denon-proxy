from pathlib import Path

from denon_proxy.http.dashboard import load_dashboard_html


def test_load_dashboard_html_returns_none_when_file_unreadable():
    """When the path is missing or unreadable, load_dashboard_html returns None."""
    path = Path(__file__).parent / "does_not_exist_dashboard.html"
    result = load_dashboard_html(path=path)
    assert result is None
