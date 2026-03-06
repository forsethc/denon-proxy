"""
AVR identity and capability info discovered at runtime.

Immutable type populated from HTTP sync (denonavr); holds manufacturer, model,
serial, raw friendly name from the device, and the raw list of input sources.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AVRInfo:
    """
    Immutable AVR identity and capability info discovered at runtime.

    Populated from HTTP sync (denonavr); holds manufacturer, model, serial,
    raw friendly name from the device, and raw_sources (raw list of input sources).
    """

    manufacturer: str | None
    model_name: str | None
    serial_number: str | None
    raw_friendly_name: str | None
    raw_sources: list[tuple[str, str]]

    def __post_init__(self) -> None:
        if not isinstance(self.raw_sources, list):
            object.__setattr__(self, "raw_sources", list(self.raw_sources))

    def has_sources(self) -> bool:
        """Return True if the AVR reported any input sources."""
        return bool(self.raw_sources)

    def describe(self) -> str:
        """Short human-readable description of the AVR (manufacturer, model, name)."""
        parts = []
        if self.manufacturer:
            parts.append(self.manufacturer.strip())
        if self.model_name:
            parts.append(self.model_name.strip())
        if self.raw_friendly_name:
            parts.append(f'"{self.raw_friendly_name.strip()}"')
        return " ".join(parts) if parts else "Unknown AVR"
