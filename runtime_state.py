"""
Runtime state for the proxy – live data kept separate from static configuration.

Config holds user/settings only (avr_host, proxy_port, etc.). This module holds
state that changes at runtime: AVR info from device, resolved sources, notify
callback, and resolved ports (e.g. when config specifies port 0).
"""

from __future__ import annotations

from typing import Any, Callable


class RuntimeState:
    """
    Mutable runtime context for the proxy.

    Holds avr_info, device_sources, resolved_sources, notify_web_state, and
    resolved port values. Pass one instance through the proxy and discovery
    instead of mutating config.
    """

    __slots__ = (
        "avr_info",
        "device_sources",
        "resolved_sources",
        "notify_web_state",
        "ssdp_http_port",
        "proxy_port",
    )

    def __init__(self) -> None:
        # From physical AVR (e.g. HTTP sync): manufacturer, model_name, serial_number, friendly_name
        self.avr_info: dict[str, Any] = {}
        # Raw (func, display_name) list from AVR
        self.device_sources: list[tuple[str, str]] | None = None
        # Resolved list used by discovery/API (cached result of get_sources logic)
        self.resolved_sources: list[tuple[str, str]] | None = None
        # Callback to push state to Web UI / SSE when something changes
        self.notify_web_state: Callable[[], None] = lambda: None
        # Resolved SSDP HTTP port when config had ssdp_http_port=0 (OS-chosen port)
        self.ssdp_http_port: int | None = None
        # Resolved proxy (telnet) port when config had proxy_port=0 (OS-chosen port)
        self.proxy_port: int | None = None
