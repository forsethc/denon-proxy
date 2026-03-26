"""
Map Denon telnet commands to config-driven INFO logging groups.

Used by the proxy and AVR connection so client commands, broadcasts, AVR responses,
and outbound sends share the same log_command_groups_info rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_COMMAND_GROUPS = {
    "PW": "power",
    "ZM": "power",
    "MV": "volume",
    "SI": "input",
    "MU": "mute",
    "MS": "sound_mode",
}


def command_group(cmd: str) -> str:
    """Return the group name for a Denon telnet command (e.g. PWON -> power)."""
    if not cmd or len(cmd) < 2:
        return "other"
    # MSSMART is Smart Select slot, not sound mode; must check before MS
    if cmd.upper().startswith("MSSMART"):
        return "smart_select"
    prefix = cmd[:2].upper()
    if prefix == "MV" and len(cmd) > 2 and "MAX" in cmd.upper():
        return "other"  # MVMAX is config, not state
    return _COMMAND_GROUPS.get(prefix, "other")


def should_log_command_info(config: Mapping[str, Any], cmd: str) -> bool:
    """True if this command's group is configured for INFO-level logging."""
    groups = config.get("log_command_groups_info") or []
    if not groups:
        return False
    return command_group(cmd) in groups
