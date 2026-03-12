"""
Runtime state for the proxy – live data kept separate from static configuration.

Config holds user/settings only (avr_host, proxy_port, etc.). This module holds
state that changes at runtime: AVR info from device, resolved sources, notify
callback, and resolved ports (e.g. when config specifies port 0).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from denon_proxy.utils.utils import get_resolved_port as _get_resolved_port

if TYPE_CHECKING:
    from collections.abc import Callable

    from denon_proxy.avr.info import AVRInfo
    from denon_proxy.runtime.config import Config


class RuntimeState:
    """
    Mutable runtime context for the proxy.

    Holds avr_info (AVR identity and raw_sources), resolved_sources,
    notify_web_state, resolved friendly name (cached), and resolved port values.
    Pass one instance through the proxy and discovery instead of mutating config.
    """

    __slots__ = (
        "avr_info",
        "resolved_sources",
        "notify_web_state",
        "ssdp_http_port",
        "proxy_port",
        "http_port",
        "_cached_friendly_name",
        "version",
    )

    def __init__(self) -> None:
        # AVR identity and capabilities (from HTTP sync); None until first sync
        self.avr_info: AVRInfo | None = None
        # Resolved list used by discovery/API (cached result of get_sources logic)
        self.resolved_sources: list[tuple[str, str]] | None = None
        # Callback to push state to Web UI / SSE when something changes
        self.notify_web_state: Callable[[], None] = lambda: None
        # Resolved SSDP HTTP port when config had ssdp_http_port=0 (OS-chosen port)
        self.ssdp_http_port: int | None = None
        # Resolved proxy (telnet) port when config had proxy_port=0 (OS-chosen port)
        self.proxy_port: int | None = None
        # Resolved HTTP API port when config had http_port=0 (OS-chosen port)
        self.http_port: int | None = None
        # Resolved friendly name (computed on first access; avr_info is set once at startup)
        self._cached_friendly_name: str | None = None
        # Version from git describe (set at startup; used in JSON API and web UI)
        self.version: str = "unknown"

    def get_resolved_port(self, config: Config, config_key: str, default: int) -> int:
        """Return effective port: resolved value if set, else config key with default."""
        return _get_resolved_port(self, config, config_key, default)

    def get_friendly_name(self, config: Config) -> str:
        """Resolved proxy friendly name: config override, else AVR name + ' Proxy', else default.

        Computed on first access from config + avr_info and cached for the lifetime of this
        RuntimeState. avr_info is set once at startup and is not refreshed today.
        """
        if self._cached_friendly_name is not None:
            return self._cached_friendly_name
        configured = (config.get("ssdp_friendly_name") or "").strip() or None
        if configured:
            resolved = configured
        else:
            avr_raw = None
            if self.avr_info and self.avr_info.raw_friendly_name:
                avr_raw = (self.avr_info.raw_friendly_name or "").strip() or None
            resolved = f"{avr_raw} Proxy" if avr_raw else "Denon AVR Proxy"
        self._cached_friendly_name = resolved
        return resolved
