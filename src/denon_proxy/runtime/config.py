"""Configuration model and loading for denon-proxy."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterator, Mapping
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from denon_proxy.constants import (
    DEFAULT_AVR_PORT,
    DEFAULT_HTTP_PORT,
    DEFAULT_PROXY_PORT,
    DEFAULT_SSDP_HTTP_PORT,
)

_VALID_COMMAND_GROUPS = frozenset({"power", "volume", "input", "mute", "sound_mode", "smart_select", "other"})


class VirtualSource(BaseModel):
    """An extra entry in the source list that selects a real input plus an input mode (Denon ``SD``).

    Example: Apple TV with analog audio and HDMI video is ``input: MPLAY, input_mode: ANALOG``.
    """

    model_config = {"extra": "forbid", "frozen": True}

    input: str = Field(min_length=1)  # real Denon source code, e.g. MPLAY
    # Denon input mode (SD) values are usually AUTO, HDMI, DIGITAL or ANALOG; some models have more, and
    # each input only offers the connectors assigned to it, so the value isn't restricted to a fixed set.
    input_mode: str = Field(min_length=1)  # SD parameter to apply, e.g. ANALOG
    name: str | None = None  # display name; defaults to "<input's display name> (<Input_mode>)"
    default_input_mode: str = "AUTO"  # SD parameter set whenever the plain input is selected

    @field_validator("input", "input_mode", "default_input_mode", mode="before")
    @classmethod
    def _normalize_code(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        v = v.strip().upper()
        if not v or any(c.isspace() or c == "?" for c in v):
            raise ValueError("must be a non-empty code without spaces or '?'")
        return v

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip() or None
        return v

    @model_validator(mode="after")
    def _modes_differ(self) -> VirtualSource:
        if self.input_mode == self.default_input_mode:
            raise ValueError("input_mode and default_input_mode must differ")
        return self


def _apply_env_to_dict(
    raw: dict[str, Any],
    env: Mapping[str, str] | None = None,
) -> None:
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
    # Physical AVR: learn which telnet *query* lines (ending with '?') get no response and optionally
    # stop logging them (uses avr_unanswered_log_suppress_after / avr_unanswered_response_timeout).
    # Set false to disable.
    dynamic_command_filtering: bool = True
    # Suppress client + "Sent to AVR" logs after this many unanswered sends per command (must be > 0).
    avr_unanswered_log_suppress_after: int = Field(3, gt=0)
    # Seconds to wait after each physical AVR send for a matching response line; if none arrives, that
    # send counts toward avr_unanswered_log_suppress_after (dynamic_command_filtering only).
    avr_unanswered_response_timeout: float = Field(1.0, ge=0.1, le=120.0)

    # Optional/less common config keys
    ssdp_friendly_name: str | None = None
    # Map Denon function codes to display names (e.g. {"CD": "CD Player", "HDMI1": "Apple TV"})
    sources: dict[str, str] | None = None
    # Extra source-list entries that pick a real input and an input mode (Denon SD), keyed by the
    # code clients send as SI<code> (e.g. MPLAY_ANALOG: Apple TV with analog audio).
    virtual_sources: dict[str, VirtualSource] = Field(default_factory=dict)
    # IP -> friendly name for connected clients (e.g. {"192.168.1.5": "Living Room HA"})
    client_aliases: dict[str, str] = Field(default_factory=dict)

    # Mapping interface (read-only), for code that uses config["key"] or config.get("key")
    def __getitem__(self, key: str) -> Any:
        if key not in self.__class__.model_fields:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        # Expose keys for Mapping interface (dict(config) uses this).
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
                raise ValueError(f"invalid group {g!r}, must be one of {sorted(_VALID_COMMAND_GROUPS)}")
        return v

    @field_validator("sources", mode="before")
    @classmethod
    def _sources_dict_str_str(cls, v: Any) -> Any:
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

    @field_validator("virtual_sources", mode="before")
    @classmethod
    def _virtual_source_codes(cls, v: Any) -> Any:
        """Normalize codes to upper case (SI commands are matched case-insensitively) and reject bad ones."""
        if not isinstance(v, dict):
            return v
        out: dict[str, Any] = {}
        for code, entry in v.items():
            if not isinstance(code, str) or not code.strip() or any(c.isspace() or c == "?" for c in code.strip()):
                raise ValueError(f"invalid virtual source code {code!r}: no spaces or '?'")
            key = code.strip().upper()
            if key in out:
                raise ValueError(f"duplicate virtual source code {key!r}")
            out[key] = entry
        return out

    @field_validator("ssdp_friendly_name", mode="before")
    @classmethod
    def _ssdp_friendly_name_normalize(cls, v: str | None) -> str | None:
        """Normalize friendly name: strip whitespace, treat empty as None."""
        if v is None:
            return None
        if isinstance(v, str):
            stripped = v.strip()
            return stripped or None
        return v

    @model_validator(mode="after")
    def _virtual_sources_consistent(self) -> Config:
        """Cross-check virtual_sources against each other and, when configured, against sources.

        Runs after all field validators, so codes are already normalized. Without a sources mapping
        the source list comes from the AVR at runtime, which resolve_sources checks instead.
        """
        real_codes = {code.strip().upper() for code in self.sources or {}}
        seen: dict[tuple[str, str], str] = {}
        default_modes: dict[str, tuple[str, str]] = {}  # input -> (first code, its default_input_mode)
        for code, vs in self.virtual_sources.items():
            if real_codes and code in real_codes:
                raise ValueError(f"virtual source {code!r} has the same code as an entry in sources")
            if real_codes and vs.input not in real_codes:
                raise ValueError(f"virtual source {code!r}: input {vs.input!r} is not in sources")
            # Each (input, mode) may back only one virtual source, so the AVR state maps to one code.
            pair = (vs.input, vs.input_mode)
            if pair in seen:
                raise ValueError(f"virtual sources {seen[pair]!r} and {code!r} both use {pair[0]} + {pair[1]}")
            seen[pair] = code
            # Selecting the plain input sets default_input_mode, so an input can only have one.
            other = default_modes.setdefault(vs.input, (code, vs.default_input_mode))
            if other[1] != vs.default_input_mode:
                raise ValueError(
                    f"virtual sources {other[0]!r} and {code!r} use input {vs.input} with different default_input_mode"
                )
        return self

    @model_validator(mode="after")
    def _no_avr_host_force_optimistic_off(self) -> Config:
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
        raw: Mapping[str, Any] | None,
        env: Mapping[str, str] | None = None,
    ) -> Config:
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
