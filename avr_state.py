"""
Canonical Denon AVR state model and presentation helpers.

AVRState is the shared state used by the proxy (telnet/optimistic updates),
avr_connection (physical or virtual AVR I/O), and avr_discovery (HTTP/XML).
Volume helpers are used for presentation (JSON, XML).
"""

from __future__ import annotations

from typing import Optional

# Denon 0–98 scale: 80 = 0 dB reference; (vol - 80) * 0.5 ≈ dB.
VOLUME_REFERENCE_LEVEL = 80.0
# Default state/display level when we have no AVR value (e.g. demo, discovery). 50 ≈ -15 dB.
VOLUME_DEFAULT_LEVEL = 50


# -----------------------------------------------------------------------------
# AVR State
# -----------------------------------------------------------------------------


class AVRState:
    """
    Tracks Denon AVR state (power, volume, input, mute, sound_mode).
    Used by the connection layer for telnet and by discovery for HTTP/XML.
    """

    def __init__(self) -> None:
        # Defaults for demo mode / before AVR responds
        self.power: Optional[str] = "ON"       # ON, STANDBY, OFF
        self.volume: Optional[str] = str(VOLUME_DEFAULT_LEVEL)  # 0 to AVR max; overwritten by AVR
        self.input_source: Optional[str] = "CD"  # e.g. "CD", "TUNER", "DVD"
        self.mute: Optional[bool] = False      # True = muted
        self.sound_mode: Optional[str] = "STEREO"  # e.g. STEREO, MULTI CH IN, DOLBY DIGITAL

    def update_from_message(self, message: str) -> None:
        """Update state from a Denon telnet response (PW, MV, SI, MU, ZM, MS)."""
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
                # MVMAX = AVR max volume limit (handled by connection; not current volume)
                pass
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

        elif message.startswith("MS") and len(message) > 2:
            param = message[2:].strip()
            if param and param != "?":
                self.sound_mode = param

    def get_status_dump(self) -> str:
        """Return Denon telnet-format status lines for new clients."""
        lines = []
        if self.power:
            lines.append(f"PW{self.power}")
            # ZM (Zone Main) so HA denonavr receives power updates via telnet (it ignores PW).
            # ZMSTANDBY uses parameter "STANDBY" which denonavr accepts; ZMOFF for compatibility.
            if self.power == "ON":
                lines.append("ZMON")
            elif self.power in ("STANDBY", "OFF"):
                lines.append("ZMSTANDBY")
                lines.append("ZMOFF")
        if self.volume:
            lines.append(f"MV{self.volume}")
        if self.input_source:
            lines.append(f"SI{self.input_source}")
        if self.mute is not None:
            lines.append("MUON" if self.mute else "MUOFF")
        if self.sound_mode:
            lines.append(f"MS{self.sound_mode}")
        return "\r\n".join(lines) + "\r\n" if lines else ""

    def snapshot(self) -> dict:
        """Snapshot for optimistic update rollback."""
        return {
            "power": self.power,
            "volume": self.volume,
            "input_source": self.input_source,
            "mute": self.mute,
            "sound_mode": self.sound_mode,
        }

    def restore(self, snapshot: dict) -> None:
        """Restore from snapshot after failed send."""
        self.power = snapshot.get("power")
        self.volume = snapshot.get("volume")
        self.input_source = snapshot.get("input_source")
        self.mute = snapshot.get("mute")
        self.sound_mode = snapshot.get("sound_mode")

    def apply_command(
        self,
        command: str,
        volume_step: float,
        volume_max: float,
    ) -> bool:
        """Optimistically apply a Denon telnet command. Returns True if state changed.

        volume_step and volume_max are AVR/config properties; pass from connection/config.
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
                level = volume_to_level(self.volume, volume_max)
                level = min(volume_max, level + volume_step)
                self.volume = _format_volume(level, volume_max)
                applied = True
            elif param.upper() == "DOWN":
                level = volume_to_level(self.volume, volume_max)
                level = max(0.0, level - volume_step)
                self.volume = _format_volume(level, volume_max)
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


def volume_to_level(vol_str: Optional[str], max_volume: float = 98.0) -> float:
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


def volume_to_db(vol_str: Optional[str]) -> str:
    """Convert Denon telnet volume (0-98, half steps, MAX, etc.) to dB for status XML."""
    vol = volume_to_level(vol_str)
    db = (vol - VOLUME_REFERENCE_LEVEL) * 0.5
    return f"{db:.1f}"
