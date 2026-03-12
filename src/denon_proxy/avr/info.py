"""
AVR identity and capability info discovered at runtime.

Immutable type populated from HTTP sync (denonavr); holds manufacturer,
model, serial, raw friendly name from the device, and the raw list of
input sources. It exposes exactly what SSDP and HTTP discovery need
(e.g. UDN serial, model fields) so those layers depend on this typed
object instead of raw dicts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AVRInfo:
    """
    Immutable AVR identity and capability info discovered at runtime.

    Populated from HTTP sync (denonavr); holds manufacturer, model_name,
    serial_number, raw friendly name, and raw_sources.
    """

    manufacturer: str | None
    model_name: str | None
    serial_number: str | None
    raw_friendly_name: str | None
    raw_sources: list[tuple[str, str]]

    def __post_init__(self) -> None:
        if not isinstance(self.raw_sources, list):
            object.__setattr__(self, "raw_sources", list(self.raw_sources))

    def udn_serial(self, fallback_advertise_ip: str) -> str:
        """Serial string for SSDP UDN/USN. Uses device serial_number if set, else proxy-{ip}."""
        if self.serial_number and str(self.serial_number).strip():
            return str(self.serial_number).strip()
        return f"proxy-{fallback_advertise_ip.replace('.', '-')}"

    @classmethod
    def virtual(cls) -> AVRInfo:
        """Canonical AVRInfo when running without a physical AVR (virtual mode)."""
        return cls(
            manufacturer="Denon",
            model_name="Virtual",
            serial_number=None,
            raw_friendly_name=None,
            raw_sources=[],
        )

    @classmethod
    def unknown(cls) -> AVRInfo:
        """Placeholder when a physical AVR is configured but identity
        could not be discovered (e.g. HTTP sync failed).
        """
        return cls(
            manufacturer="Denon",
            model_name=None,
            serial_number=None,
            raw_friendly_name=None,
            raw_sources=[],
        )

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
