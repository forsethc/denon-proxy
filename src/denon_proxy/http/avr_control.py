"""
Pure routing helpers for the /api/avr/... control endpoints.

route_to_command() maps an HTTP path + query-string to a Denon telnet command,
performing state-aware toggle resolution and validation. No I/O.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from denon_proxy.avr.state import AVRState


def route_to_command(
    path: str,
    query_string: str,
    avr_state: AVRState,
    sources: list[tuple[str, str]],
) -> tuple[str | None, dict[str, Any], int]:
    """
    Map an HTTP path + query-string to a Denon telnet command.

    Returns (command, response_dict, status_code).
    When command is None, it is an error (status_code is 4xx or 5xx).
    When command is a string, it is a success (status_code is 200).
    """
    # Strip leading slash and split: ["api", "avr", <feature>, <action_or_code>]
    parts = path.strip("/").split("/")
    if len(parts) < 4 or parts[0] != "api" or parts[1] != "avr":
        return None, {"error": "Not Found"}, 404

    feature = parts[2].lower()
    action = parts[3]

    if feature == "power":
        return _handle_power(action, avr_state)
    if feature == "volume":
        return _handle_volume(action, query_string, avr_state)
    if feature == "mute":
        return _handle_mute(action, avr_state)
    if feature == "source":
        return _handle_source(action, sources)
    return None, {"error": "Not Found"}, 404


# ---------------------------------------------------------------------------
# Feature handlers
# ---------------------------------------------------------------------------


def _handle_power(action: str, avr_state: AVRState) -> tuple[str | None, dict[str, Any], int]:
    if action == "on":
        cmd = "PWON"
    elif action == "off":
        cmd = "PWSTANDBY"
    elif action == "toggle":
        power = (avr_state.power or "").upper()
        cmd = "PWSTANDBY" if power == "ON" else "PWON"
    else:
        return None, {"error": f"Unknown power action {action!r}. Valid: on, off, toggle"}, 404
    return cmd, {"ok": True, "command": cmd}, 200


def _handle_volume(
    action: str,
    query_string: str,
    avr_state: AVRState,
) -> tuple[str | None, dict[str, Any], int]:
    if action == "up":
        cmd = "MVUP"
    elif action == "down":
        cmd = "MVDOWN"
    elif action == "set":
        level_str = _parse_query_param(query_string, "level")
        if level_str is None:
            return None, {"error": "Missing 'level' query parameter (e.g. ?level=45)"}, 400
        try:
            level = float(level_str)
        except ValueError:
            return None, {"error": f"Invalid level {level_str!r}: must be a number"}, 400
        vol_max = avr_state.volume_max
        if not (0.0 <= level <= vol_max):
            return None, {"error": f"level must be between 0 and {vol_max}"}, 400
        # Round to nearest half step (Denon supports 0.5 increments)
        level = round(level * 2) / 2
        from denon_proxy.avr.state import _format_volume  # noqa: PLC0415

        cmd = f"MV{_format_volume(level, vol_max)}"
    else:
        return None, {"error": f"Unknown volume action {action!r}. Valid: up, down, set"}, 404
    return cmd, {"ok": True, "command": cmd}, 200


def _handle_mute(action: str, avr_state: AVRState) -> tuple[str | None, dict[str, Any], int]:
    if action == "on":
        cmd = "MUON"
    elif action == "off":
        cmd = "MUOFF"
    elif action == "toggle":
        cmd = "MUOFF" if avr_state.mute else "MUON"
    else:
        return None, {"error": f"Unknown mute action {action!r}. Valid: on, off, toggle"}, 404
    return cmd, {"ok": True, "command": cmd}, 200


def _handle_source(code: str, sources: list[tuple[str, str]]) -> tuple[str | None, dict[str, Any], int]:
    if not code:
        return None, {"error": "Source code is required"}, 400
    known = {func.upper(): func for func, _ in sources}
    canonical = known.get(code.upper())
    if canonical is None:
        return None, {"error": f"Unknown source {code!r}. Known: {sorted(known)}"}, 404
    cmd = f"SI{canonical}"
    return cmd, {"ok": True, "command": cmd}, 200


# ---------------------------------------------------------------------------
# Query-string helper
# ---------------------------------------------------------------------------


def _parse_query_param(query_string: str, param: str) -> str | None:
    """Parse a single parameter value from a query string like 'level=45&foo=bar'."""
    if not query_string:
        return None
    for part in query_string.split("&"):
        if "=" in part:
            key, _, value = part.partition("=")
            if key.strip().lower() == param.lower():
                return value.strip() or None
    return None


def normalize_source_cmd_name(func_code: str) -> str:
    """
    Produce a UC-safe simple-command name for a source input code.

    UC allows A-Z, a-z, 0-9 and /_.:+#*°@%()?- in simple command names.
    We replace non-alphanumeric chars (e.g. '/' in 'SAT/CBL') with underscores
    to keep YAML keys clean, then prefix with SRC_.
    """
    clean = re.sub(r"[^A-Za-z0-9]", "_", func_code).upper()
    clean = re.sub(r"_+", "_", clean).strip("_")
    return f"SRC_{clean}"
