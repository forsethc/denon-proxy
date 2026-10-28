"""
Canonical Denon AVR state model and presentation helpers.

AVRState is the shared state used by the proxy (telnet/optimistic updates),
avr_connection (physical or virtual AVR I/O), and avr_discovery (HTTP/XML).
Volume helpers are used for presentation (JSON, XML).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, NamedTuple

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


class InputVariant(NamedTuple):
    """A virtual source: a real input selected together with an input mode (all upper case)."""

    input: str  # real source code, e.g. MPLAY
    mode: str  # SD parameter applied with it, e.g. ANALOG
    default_mode: str  # SD parameter set whenever the plain input is selected, e.g. AUTO


# -----------------------------------------------------------------------------
# AVR State
# -----------------------------------------------------------------------------


@dataclass
class AVRState:
    """
    Tracks Denon AVR state (power, volume, input, input mode, mute, sound_mode, smart_select).
    Used by the connection layer for telnet and by discovery for HTTP/XML.
    """

    power: str | None = "ON"  # ON, STANDBY, OFF
    volume: str | None = str(VOLUME_DEFAULT_LEVEL)  # 0 to AVR max; overwritten by AVR
    input_source: str | None = "CD"  # e.g. "CD", "TUNER", "DVD"
    mute: bool | None = False  # True = muted
    sound_mode: str | None = "STEREO"  # e.g. STEREO, MULTI CH IN, DOLBY DIGITAL
    smart_select: str | None = None  # Smart Select slot, always "SMART{n}" (e.g. SMART0, SMART1)
    volume_max: float = DEFAULT_MAX_VOLUME  # AVR max volume limit from MVMAX
    # Input mode (SD) of the current input, e.g. AUTO, ANALOG; None until the AVR reports it.
    input_mode: str | None = None
    # With virtual sources, input_source is what clients see (the real input, or a virtual source
    # code while the real input is in that virtual source's mode) and _real_input is what the AVR
    # reports. Without virtual sources the two are always equal.
    _real_input: str | None = field(default=None, init=False, repr=False)
    _variants: dict[str, InputVariant] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._real_input = self.input_source

    def update_from_message(self, message: str) -> None:
        """Update state from a Denon telnet response (PW, MV, SI, SD, MU, ZM, MS, MSSMART)."""
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
                self._set_real_input(param)

        elif message.startswith("SD") and len(message) > 2:
            param = message[2:].strip()
            if param and param != "?":
                self._set_input_mode(param)

        elif message.upper().startswith("MSSMART") and len(message) > 7:
            # MSSMART ? response gives Smart Select slot (e.g. smart0, 0), not sound mode
            param = message[7:].strip()
            if param and param != "?":
                self.smart_select = _normalize_smart_select(param)

        elif message.startswith("MS") and len(message) > 2:
            param = message[2:].strip()
            if param and param != "?":
                self.sound_mode = param

    def get_status_dump(self, *, as_avr: bool = False) -> str:
        """Return Denon telnet-format status lines for new clients and telnet sync.

        as_avr: report the real input, as a physical AVR would, instead of a virtual source
        (used by the virtual AVR, whose responses are fed back into this state).
        """
        lines = []
        if self.power:
            lines.append(f"PW{self.power}")
            lines.append(_zm_line_for_power(self.power))
        if self.volume:
            lines.append(f"MV{self.volume}")
        # Without virtual sources input_source is authoritative (callers may assign it directly).
        source = self._real_input if as_avr and self._variants else self.input_source
        if source:
            lines.append(f"SI{source}")
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
        self._real_input = snapshot.get("_real_input", self.input_source)
        self.input_mode = snapshot.get("input_mode")
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
            self._set_real_input(str(v) if v is not None else None)
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
                self._apply_input_command(param)
                applied = True
        elif cmd.startswith("SD") and len(cmd) > 2:
            param = cmd[2:].strip()
            if param and param != "?":
                self._set_input_mode(param)
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

    # -- Virtual sources ----------------------------------------------------------------------------
    # A virtual source lets clients that only know "pick a source" (e.g. Home Assistant) pick an input
    # *and* its input mode, e.g. Apple TV with analog audio. Denon stores the input mode per input, so
    # the mode is only meaningful together with the input it was reported for.

    def set_variants(self, variants: dict[str, InputVariant]) -> None:
        """Configure virtual sources (code -> InputVariant). Codes and fields are upper-cased."""
        self._variants = {
            code.upper(): InputVariant(v.input.upper(), v.mode.upper(), v.default_mode.upper())
            for code, v in variants.items()
        }
        self._refresh_input_source()

    def has_variants(self) -> bool:
        """True when any virtual sources are configured."""
        return bool(self._variants)

    def has_variant_for_current_input(self) -> bool:
        """True when the input the AVR is on has at least one virtual source."""
        real = (self._real_input or "").upper()
        return any(v.input == real for v in self._variants.values())

    def needs_input_mode_query(self) -> bool:
        """True when the current input has a virtual source but its input mode is not known yet."""
        return self.input_mode is None and self.has_variant_for_current_input()

    def expand_command(self, command: str) -> list[str]:
        """Return the commands to send to the AVR for a client command.

        Call before apply_command(): the result depends on the state the command starts from.
        - SI<virtual code> -> SI<input>, SD<mode>. The input goes first so the mode lands on it.
        - SI<input> for an input that has virtual sources -> SI<input>, SD<default_mode>, unless the
          input is already known to be in default_mode. The AVR remembers each input's mode, so without
          this, coming back to the plain input (even via another input) would bring the virtual
          source's mode back with it.
        - Anything else is sent unchanged.
        """
        cmd = command.strip().upper()
        if not self._variants or not cmd.startswith("SI") or len(cmd) <= 2:
            return [command]
        param = cmd[2:]
        variant = self._variants.get(param)
        if variant is not None:
            return [f"SI{variant.input}", f"SD{variant.mode}"]
        # The default comes from the input being selected, never from the virtual source being left.
        default = self._default_mode_for(param)
        if default is not None and not self._is_in_mode(param, default):
            return [command, f"SD{default}"]
        return [command]

    def _default_mode_for(self, input_code: str) -> str | None:
        """default_mode of the input's virtual sources (config ensures they agree), or None if it has none."""
        return next((v.default_mode for v in self._variants.values() if v.input == input_code.upper()), None)

    def _is_in_mode(self, input_code: str, mode: str) -> bool:
        """True when the AVR is known to be on input_code in mode."""
        return (self._real_input or "").upper() == input_code.upper() and self.input_mode == mode

    def _active_variant(self) -> tuple[str, InputVariant] | None:
        """(code, variant) of the virtual source matching the current input and input mode, if any."""
        if self._real_input is None or self.input_mode is None:
            return None
        real, mode = self._real_input.upper(), self.input_mode.upper()
        return next(((c, v) for c, v in self._variants.items() if v.input == real and v.mode == mode), None)

    def _refresh_input_source(self) -> None:
        """Recompute the client-facing input_source from the real input and input mode."""
        active = self._active_variant()
        self.input_source = active[0] if active else self._real_input

    def _set_real_input(self, value: str | None) -> None:
        # A different input has its own (not yet known) mode; the same input keeps the known one.
        if value is None or (self._real_input or "").upper() != value.upper():
            self.input_mode = None
        self._real_input = value
        self._refresh_input_source()

    def _set_input_mode(self, value: str) -> None:
        self.input_mode = value.upper()
        self._refresh_input_source()

    def _apply_input_command(self, param: str) -> None:
        """Optimistic state for SI<param>; mirrors expand_command so state matches what is sent."""
        variant = self._variants.get(param)
        default = self._default_mode_for(param)
        if variant is not None:
            self._set_real_input(variant.input)
            self._set_input_mode(variant.mode)
        else:
            self._set_real_input(param)
            if default is not None:
                self._set_input_mode(default)


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
