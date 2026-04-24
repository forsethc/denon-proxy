"""
Canonical Denon AVR state model and presentation helpers.

AVRState is the shared state used by the proxy (telnet/optimistic updates),
avr_connection (physical or virtual AVR I/O), and avr_discovery (HTTP/XML).
Volume helpers are used for presentation (JSON, XML).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from denon_proxy.constants import (
    DEFAULT_MAX_VOLUME,
    VOLUME_DEFAULT_LEVEL,
    VOLUME_REFERENCE_LEVEL,
)


def _normalize_smart_select(value: str | None) -> str | None:
    """Normalize to SMART{n} (e.g. SMART0, SMART1). Accepts numeral, smart0, or SMART0."""
    if not value or not str(value).strip():
        return None
    s = str(value).strip().upper()
    if s.startswith("SMART"):
        rest = s[5:].lstrip()
        if rest.isdigit():
            return f"SMART{rest}"
        return None
    if s.isdigit():
        return f"SMART{s}"
    return None


def _zm_line_for_power(power: str) -> str:
    """Telnet zone-main power line matching PW state (for status dumps and multi-zone clients)."""
    return "ZMON" if power.upper() == "ON" else "ZMOFF"


def _parse_mvmax(param: str) -> float | None:
    """Parse MVMAX nn from param (e.g. 'MAX 60' or 'MAX60'). Returns None if no number."""
    if not param.upper().startswith("MAX"):
        return None
    rest = param[3:].strip() if len(param) > 3 else ""
    if not rest and " " in param:
        parts = param.split()
        rest = parts[-1] if len(parts) > 1 else ""
    match = re.search(r"\d+", rest) if rest else None
    return max(0.0, float(match.group())) if match else None


# -----------------------------------------------------------------------------
# AVR State
# -----------------------------------------------------------------------------


@dataclass
class AVRState:
    """
    Tracks Denon AVR state (power, volume, input, mute, sound_mode, smart_select).
    Used by the connection layer for telnet and by discovery for HTTP/XML.
    """

    power: str | None = "ON"  # ON, STANDBY, OFF
    volume: str | None = str(VOLUME_DEFAULT_LEVEL)  # 0 to AVR max; overwritten by AVR
    input_source: str | None = "CD"  # e.g. "CD", "TUNER", "DVD"
    mute: bool | None = False  # True = muted
    sound_mode: str | None = "STEREO"  # e.g. STEREO, MULTI CH IN, DOLBY DIGITAL
    smart_select: str | None = None  # Smart Select slot, always "SMART{n}" (e.g. SMART0, SMART1)
    volume_max: float = DEFAULT_MAX_VOLUME  # AVR max volume limit from MVMAX

    def update_from_message(self, message: str) -> None:
        """Update state from a Denon telnet response (PW, MV, SI, MU, ZM, MS, MSSMART)."""
        if not message or len(message) < 2:
            return

        if message.startswith("PW"):
            if message == "PWON":
                self.power = "ON"
            elif message == "PWSTANDBY" or "STANDBY" in message:
                self.power = "STANDBY"
            elif message == "PW?":
                pass
            elif len(message) > 2:
                param = message[2:].strip()
                if param and param != "?":
                    self.power = param

        elif message.startswith("ZM"):
            if "ON" in message.upper():
                self.power = "ON"
            elif "OFF" in message.upper() or "STANDBY" in message.upper():
                self.power = "STANDBY"

        elif message.startswith("MV") and len(message) > 2:
            param = message[2:].strip()
            if param == "?":
                pass
            elif param.upper().startswith("MAX"):
                # MVMAX = AVR max volume limit; stored on state, not current volume
                parsed = _parse_mvmax(param)
                if parsed is not None:
                    self.volume_max = parsed
            elif param.upper() not in ("UP", "DOWN") and param:
                self.volume = param

        elif message.startswith("MU"):
            if "ON" in message.upper():
                self.mute = True
            elif "OFF" in message.upper():
                self.mute = False

        elif message.startswith("SI") and len(message) > 2:
            param = message[2:].strip()
            if param and param != "?":
                self.input_source = param

        elif message.upper().startswith("MSSMART") and len(message) > 7:
            # MSSMART ? response gives Smart Select slot (e.g. smart0, 0), not sound mode
            param = message[7:].strip()
            if param and param != "?":
                self.smart_select = _normalize_smart_select(param)

        elif message.startswith("MS") and len(message) > 2:
            param = message[2:].strip()
            if param and param != "?":
                self.sound_mode = param

    def get_status_dump(self) -> str:
        """Return Denon telnet-format status lines for new clients and telnet sync."""
        lines = []
        if self.power:
            lines.append(f"PW{self.power}")
            lines.append(_zm_line_for_power(self.power))
        if self.volume:
            lines.append(f"MV{self.volume}")
        if self.input_source:
            lines.append(f"SI{self.input_source}")
        if self.mute is not None:
            lines.append("MUON" if self.mute else "MUOFF")
        if self.sound_mode:
            lines.append(f"MS{self.sound_mode}")
        if self.smart_select is not None:
            # Telnet uses numeral only (MSSMART0); we store SMART0
            numeral = self.smart_select[5:] if self.smart_select.upper().startswith("SMART") else self.smart_select
            lines.append(f"MSSMART{numeral}")
        return "\r\n".join(lines) + "\r\n" if lines else ""

    def snapshot(self) -> dict[str, Any]:
        """Snapshot for optimistic update rollback."""
        return asdict(self)

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore from snapshot after failed send."""
        self.power = snapshot.get("power")
        self.volume = snapshot.get("volume")
        self.input_source = snapshot.get("input_source")
        self.mute = snapshot.get("mute")
        self.sound_mode = snapshot.get("sound_mode")
        self.smart_select = snapshot.get("smart_select")
        self.volume_max = snapshot.get("volume_max", DEFAULT_MAX_VOLUME)

    def apply_payload(self, payload: dict[str, Any]) -> None:
        """
        Apply a dict of state fields (e.g. from JSON POST /state or denonavr sync).
        Only updates keys present in payload. smart_select is normalized to SMART{n}.
        """
        if "power" in payload:
            v = payload["power"]
            self.power = str(v).upper() if v else None
        if "volume" in payload:
            v = payload["volume"]
            self.volume = str(v) if v is not None else None
        if "input_source" in payload:
            v = payload["input_source"]
            self.input_source = str(v) if v is not None else None
        if "mute" in payload:
            self.mute = bool(payload["mute"])
        if "sound_mode" in payload:
            v = payload["sound_mode"]
            self.sound_mode = str(v) if v is not None else None
        if "smart_select" in payload:
            v = payload["smart_select"]
            self.smart_select = _normalize_smart_select(str(v) if v is not None else None)

    def apply_command(
        self,
        command: str,
        volume_step: float,
    ) -> bool:
        """Optimistically apply a Denon telnet command. Returns True if state changed.

        volume_step is an AVR/config property. volume_max is tracked on this state instance.
        """
        if not command or len(command) < 2:
            return False
        cmd = command.strip().upper()
        applied = False
        if cmd.startswith("PW"):
            if cmd == "PWON":
                self.power = "ON"
                applied = True
            elif "STANDBY" in cmd:
                self.power = "STANDBY"
                applied = True
            elif len(cmd) > 2 and cmd[2] not in ("?", ""):
                self.power = cmd[2:]
                applied = True
        elif cmd.startswith("ZM"):
            if "ON" in cmd:
                self.power = "ON"
                applied = True
            elif "OFF" in cmd or "STANDBY" in cmd:
                self.power = "STANDBY"
                applied = True
        elif cmd.startswith("MV") and len(cmd) > 2:
            param = cmd[2:].strip()
            if param == "?":
                pass
            elif param.upper() == "UP":
                level = volume_to_level(self.volume, self.volume_max)
                level = min(self.volume_max, level + volume_step)
                self.volume = _format_volume(level, self.volume_max)
                applied = True
            elif param.upper() == "DOWN":
                level = volume_to_level(self.volume, self.volume_max)
                level = max(0.0, level - volume_step)
                self.volume = _format_volume(level, self.volume_max)
                applied = True
            elif param and "MAX" not in param.upper():
                self.volume = param
                applied = True
        elif cmd.startswith("MU"):
            if "ON" in cmd:
                self.mute = True
                applied = True
            elif "OFF" in cmd:
                self.mute = False
                applied = True
        elif cmd.startswith("SI") and len(cmd) > 2:
            param = cmd[2:].strip()
            if param and param != "?":
                self.input_source = param
                applied = True
        elif cmd.upper().startswith("MSSMART") and len(cmd) > 7:
            param = cmd[7:].strip()
            if param and param != "?":
                self.smart_select = _normalize_smart_select(param)
                applied = True
        elif cmd.startswith("MS") and len(cmd) > 2:
            param = cmd[2:].strip()
            if param and param != "?":
                self.sound_mode = param
                applied = True
        return applied


# -----------------------------------------------------------------------------
# Volume helpers (presentation: JSON, XML)
# -----------------------------------------------------------------------------


def _format_volume(level: float, max_volume: float = 98.0) -> str:
    """Format numeric level as Denon telnet volume string.

    Denon uses 2 digits for whole steps (50 = 50) and 3 digits for half steps
    (535 = 53.5). Clamps to max_volume. Returns e.g. '50', '535'.
    """
    level = max(0.0, min(max_volume, level))
    if level == int(level):
        return str(int(level))
    return str(int(round(level * 10)))


def volume_to_level(vol_str: str | None, max_volume: float = 98.0) -> float:
    """Extract numeric level from Denon volume. Handles half steps (e.g. 535=53.5). Clamps to max_volume."""
    if not vol_str or not str(vol_str).strip():
        return VOLUME_DEFAULT_LEVEL
    s = str(vol_str).strip().upper()
    if "MAX" in s:
        parts = s.split()
        return min(max_volume, float(parts[-1]) if len(parts) > 1 and parts[-1].isdigit() else max_volume)
    if s.isdigit():
        # 3 digits = XX.X (e.g. 535 = 53.5), 2 digits = XX (e.g. 50 = 50)
        val = float(s) / 10.0 if len(s) == 3 else float(s)
        return max(0.0, min(max_volume, val))
    return VOLUME_DEFAULT_LEVEL


def volume_to_db(vol_str: str | None) -> str:
    """Convert Denon telnet volume (0-98, half steps, MAX, etc.) to dB for status XML."""
    vol = volume_to_level(vol_str)
    db = (vol - VOLUME_REFERENCE_LEVEL) * 0.5
    return f"{db:.1f}"
