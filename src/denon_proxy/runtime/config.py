"""Configuration model and loading for denon-proxy."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterator, Mapping
from typing import Any, Mapping as TypingMapping

from pydantic import BaseModel, Field, field_validator, model_validator

from denon_proxy.constants import (
    DEFAULT_AVR_PORT,
    DEFAULT_HTTP_PORT,
    DEFAULT_PROXY_PORT,
    DEFAULT_SSDP_HTTP_PORT,
)

_VALID_COMMAND_GROUPS = frozenset(
    {"power", "volume", "input", "mute", "sound_mode", "smart_select", "other"}
)


def _apply_env_to_dict(raw: dict[str, Any], env: TypingMapping[str, str] | None = None) -> None:
    """Mutate raw config dict with environment variable overrides."""
    if env is None:
        env = os.environ

    if (v := env.get("AVR_HOST")) is not None:
        raw["avr_host"] = v
    if (v := env.get("AVR_PORT")) and str(v).strip():
        raw["avr_port"] = v
    if (v := env.get("PROXY_HOST")) is not None:
        raw["proxy_host"] = v
    if (v := env.get("PROXY_PORT")) and str(v).strip():
        raw["proxy_port"] = v
    if (v := env.get("LOG_LEVEL")) is not None:
        raw["log_level"] = v
    if (v := env.get("DENONAVR_LOG_LEVEL")) is not None:
        raw["denonavr_log_level"] = v


class Config(BaseModel, Mapping[str, Any]):
    """Typed configuration for denon-proxy. Validates on construction."""

    model_config = {
        "extra": "forbid",
        "validate_default": True,
        "frozen": True,
    }

    avr_host: str = ""
    avr_port: int = Field(DEFAULT_AVR_PORT, ge=1, le=65535)
    proxy_host: str = Field("0.0.0.0", min_length=1)
    # Ports may be 0 to allow OS-assigned ports in tests (bind to 0).
    proxy_port: int = Field(DEFAULT_PROXY_PORT, ge=0, le=65535)
    log_level: str = Field("INFO", pattern="(?i)^(DEBUG|INFO|WARNING|ERROR)$")
    denonavr_log_level: str = Field("INFO", pattern="(?i)^(DEBUG|INFO|WARNING|ERROR)$")
    enable_ssdp: bool = True
    ssdp_http_port: int = Field(DEFAULT_SSDP_HTTP_PORT, ge=0, le=65535)
    ssdp_advertise_ip: str = ""
    optimistic_state: bool = True
    optimistic_broadcast_delay: float = Field(0.1, ge=0)
    volume_step: float = Field(0.5, ge=0)
    volume_query_delay: float = Field(0.15, ge=0)
    enable_http: bool = True
    http_port: int = Field(DEFAULT_HTTP_PORT, ge=0, le=65535)
    log_command_groups_info: list[str] = Field(default_factory=list)
    # Web UI: client activity log (connections, disconnections, commands per client)
    client_activity_log: bool = True
    client_activity_log_max_entries: int = Field(200, ge=1)
    # When true, query commands (e.g. PW?, MV?, SI?) are excluded from the activity log in the UI
    client_activity_log_hide_queries: bool = False

    # Optional/less common config keys
    ssdp_friendly_name: str | None = None
    # Map Denon function codes to display names (e.g. {"CD": "CD Player", "HDMI1": "Apple TV"})
    sources: dict[str, str] | None = None
    # IP -> friendly name for connected clients (e.g. {"192.168.1.5": "Living Room HA"})
    client_aliases: dict[str, str] = Field(default_factory=dict)

    # Mapping interface (read-only), for code that uses config["key"] or config.get("key")
    def __getitem__(self, key: str) -> Any:
        if key not in self.__class__.model_fields:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self.__class__.model_fields)

    def __len__(self) -> int:
        return len(self.__class__.model_fields)

    def get(self, key: str, default: Any | None = None) -> Any:
        if key not in self.__class__.model_fields:
            return default
        return getattr(self, key)

    # Validators --------------------------------------------------------------

    @field_validator("log_level", "denonavr_log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.upper()
        return v

    @field_validator("ssdp_advertise_ip")
    @classmethod
    def _ssdp_advertise_ip_valid_ip(cls, v: str) -> str:
        if not v or not v.strip():
            return v
        ipaddress.ip_address(v.strip())
        return v

    @field_validator("volume_step")
    @classmethod
    def _volume_step_in_half_steps(cls, v: float) -> float:
        """Require volume_step to be in 0.5 increments (0.0, 0.5, 1.0, ...)."""
        # Multiply by 2 and check it's effectively an integer to allow for float rounding.
        if (v * 2) % 1 != 0:
            raise ValueError("volume_step must be in increments of 0.5")
        return v

    @field_validator("avr_host")
    @classmethod
    def _avr_host_no_spaces(cls, v: str) -> str:
        if v and (" " in v or "\t" in v):
            raise ValueError("hostname must not contain spaces")
        return v

    @field_validator("log_command_groups_info")
    @classmethod
    def _log_command_groups_valid_set(cls, v: list[str]) -> list[str]:
        for i, g in enumerate(v):
            if not isinstance(g, str):
                raise ValueError(f"entry {i} must be a string, got {type(g).__name__}")
            if g not in _VALID_COMMAND_GROUPS:
                raise ValueError(
                    f"invalid group {g!r}, must be one of {sorted(_VALID_COMMAND_GROUPS)}"
                )
        return v

    @field_validator("sources", mode="before")
    @classmethod
    def _sources_dict_str_str(cls, v: Any):
        """Ensure sources is a dict with string keys and string values."""
        if v is None:
            return v
        if not isinstance(v, dict):
            # Let Pydantic's own type validation handle non-dict inputs.
            return v
        for k, val in v.items():
            if not isinstance(k, str) or not isinstance(val, str):
                raise ValueError("sources keys and values must be strings")
        return v

    @field_validator("ssdp_friendly_name", mode="before")
    @classmethod
    def _ssdp_friendly_name_normalize(cls, v: str | None):
        """Normalize friendly name: strip whitespace, treat empty as None."""
        if v is None:
            return None
        if isinstance(v, str):
            stripped = v.strip()
            return stripped or None
        return v

    @model_validator(mode="after")
    def _no_avr_host_force_optimistic_off(self) -> "Config":
        """After building config, force optimistic_state=False when no avr_host."""
        if not (self.avr_host or "").strip():
            object.__setattr__(self, "optimistic_state", False)
        return self

    # Helpers -----------------------------------------------------------------

    def client_display_for_log(self, ip: str) -> str:
        """Return IP for logging; if config has an alias for this IP, return 'alias (ip)'."""
        aliases = self.get("client_aliases") or {}
        if isinstance(aliases, dict) and ip and ip in aliases:
            return f"{aliases[ip]} ({ip})"
        return ip or "?"

    @classmethod
    def load_from_dict(
        cls,
        raw: TypingMapping[str, Any] | None,
        env: TypingMapping[str, str] | None = None,
    ) -> "Config":
        """
        Build Config from a raw dict (e.g. from YAML) and apply env overrides.

        This helper is used by load_config, which expects environment-variable
        overrides to be applied via _apply_env_to_dict(). Callers that must avoid
        env overrides (e.g. load_config_from_dict) should instead call
        Config.model_validate(...) directly on the raw dict.
        """
        d: dict[str, Any] = dict(raw) if raw else {}
        _apply_env_to_dict(d, env)
        return cls.model_validate(d)

