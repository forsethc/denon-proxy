from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Mapping as TypingMapping

import os

from constants import (
    DEFAULT_AVR_PORT,
    DEFAULT_HTTP_PORT,
    DEFAULT_PROXY_PORT,
    DEFAULT_SSDP_HTTP_PORT,
)


@dataclass
class Config(Mapping[str, Any]):
    """Typed configuration for denon-proxy."""

    avr_host: str = ""
    avr_port: int = DEFAULT_AVR_PORT
    proxy_host: str = "0.0.0.0"
    proxy_port: int = DEFAULT_PROXY_PORT
    log_level: str = "INFO"
    denonavr_log_level: str = "INFO"
    enable_ssdp: bool = True
    ssdp_http_port: int = DEFAULT_SSDP_HTTP_PORT
    ssdp_advertise_ip: str = ""
    optimistic_state: bool = True
    optimistic_broadcast_delay: float = 0.1
    volume_step: float = 0.5
    volume_query_delay: float = 0.15
    enable_http: bool = True
    http_port: int = DEFAULT_HTTP_PORT
    log_command_groups_info: list[str] = field(default_factory=list)
    # Web UI: per-client command log (enable and max entries per client)
    client_command_log: bool = True
    client_command_log_max_entries: int = 200
    # When true, query commands (e.g. PW?, MV?, SI?) are excluded from the log in the UI
    client_command_log_hide_queries: bool = False

    # Optional/less common config keys
    ssdp_friendly_name: str | None = None
    sources: Any | None = None
    # IP -> friendly name for connected clients (e.g. {"192.168.1.5": "Living Room HA"})
    client_aliases: dict[str, str] = field(default_factory=dict)

    # Mapping interface (read-only) --------------------------------------

    _FIELDS = frozenset(
        {
            "avr_host", "avr_port", "proxy_host", "proxy_port", "log_level",
            "denonavr_log_level", "enable_ssdp", "ssdp_http_port", "ssdp_advertise_ip",
            "optimistic_state", "optimistic_broadcast_delay", "volume_step", "volume_query_delay",
            "enable_http", "http_port", "log_command_groups_info",
            "client_command_log", "client_command_log_max_entries", "client_command_log_hide_queries",
            "ssdp_friendly_name", "sources", "client_aliases",
        }
    )

    def __getitem__(self, key: str) -> Any:
        if key not in self._FIELDS:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._FIELDS)

    def __len__(self) -> int:
        return len(self._FIELDS)

    def get(self, key: str, default: Any | None = None) -> Any:
        if key not in self._FIELDS:
            return default
        return getattr(self, key)

    # Construction only: apply raw dict during from_dict. Unknown keys ignored.
    def _apply_dict(self, other: Mapping[str, Any]) -> None:
        for k in self._FIELDS:
            if k in other:
                val = other[k]
                if k == "client_aliases" and val is not None and not isinstance(val, dict):
                    val = dict(val) if hasattr(val, "items") else {}
                setattr(self, k, val)

    @classmethod
    def from_dict(cls, raw: TypingMapping[str, Any] | None) -> "Config":
        cfg = cls()
        if raw:
            cfg._apply_dict(raw)
        return cfg

    def apply_env_overrides(self, env: TypingMapping[str, str] | None = None) -> None:
        """Apply environment variable overrides in-place."""
        env = env or os.environ

        avr_host = env.get("AVR_HOST")
        if avr_host is not None:
            self.avr_host = avr_host

        avr_port = env.get("AVR_PORT")
        if avr_port:
            self.avr_port = int(avr_port)

        proxy_host = env.get("PROXY_HOST")
        if proxy_host is not None:
            self.proxy_host = proxy_host

        proxy_port = env.get("PROXY_PORT")
        if proxy_port:
            self.proxy_port = int(proxy_port)

        log_level = env.get("LOG_LEVEL")
        if log_level is not None:
            self.log_level = log_level

        denonavr_log_level = env.get("DENONAVR_LOG_LEVEL")
        if denonavr_log_level is not None:
            self.denonavr_log_level = denonavr_log_level

    def finalize(self) -> None:
        """
        Apply derived settings/invariants after file/env merges.

        - When no avr_host is specified, optimistic_state is forced False.
        """
        if not (self.avr_host or "").strip():
            self.optimistic_state = False

    def client_display_for_log(self, ip: str) -> str:
        """Return IP for logging; if config has an alias for this IP, return 'alias (ip)'."""
        aliases = self.get("client_aliases") or {}
        if isinstance(aliases, dict) and ip and ip in aliases:
            return f"{aliases[ip]} ({ip})"
        return ip or "?"


