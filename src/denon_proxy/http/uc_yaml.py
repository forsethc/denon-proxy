"""
Generators for Unfolded Circle Remote 3 (UCR3) custom-entity configuration.

build_capabilities() returns a JSON-serializable dict describing all supported
control endpoints and the resolved source list.

build_custom_entities_yaml() returns a ready-to-paste custom_entities.yaml
string for the ucr2-integration-requests integration:
  https://github.com/kennymc-c/ucr2-integration-requests#4---custom-entities-remote--select
"""

from __future__ import annotations

from typing import Any

from denon_proxy.http.avr_control import normalize_source_cmd_name


def build_capabilities(base_url: str, sources: list[tuple[str, str]]) -> dict[str, Any]:
    """Return a JSON-serializable capabilities description of all AVR control endpoints."""
    source_options = [
        {
            "code": func,
            "display_name": name,
            "url": f"{base_url}/api/avr/source/{func}",
            "cmd_name": normalize_source_cmd_name(func),
        }
        for func, name in sources
    ]
    return {
        "base_url": base_url,
        "features": {
            "power": {
                "endpoints": {
                    "on": f"{base_url}/api/avr/power/on",
                    "off": f"{base_url}/api/avr/power/off",
                    "toggle": f"{base_url}/api/avr/power/toggle",
                },
            },
            "volume": {
                "endpoints": {
                    "up": f"{base_url}/api/avr/volume/up",
                    "down": f"{base_url}/api/avr/volume/down",
                    "set": f"{base_url}/api/avr/volume/set?level={{level}}",
                },
            },
            "mute": {
                "endpoints": {
                    "on": f"{base_url}/api/avr/mute/on",
                    "off": f"{base_url}/api/avr/mute/off",
                    "toggle": f"{base_url}/api/avr/mute/toggle",
                },
            },
            "source": {
                "options": source_options,
            },
        },
        "endpoints": [
            {"method": "GET", "path": "/api/avr/power/on", "telnet": "PWON"},
            {"method": "GET", "path": "/api/avr/power/off", "telnet": "PWSTANDBY"},
            {"method": "GET", "path": "/api/avr/power/toggle", "telnet": "PWON or PWSTANDBY (state-aware)"},
            {"method": "GET", "path": "/api/avr/volume/up", "telnet": "MVUP"},
            {"method": "GET", "path": "/api/avr/volume/down", "telnet": "MVDOWN"},
            {"method": "GET", "path": "/api/avr/volume/set?level=<n>", "telnet": "MV<n>"},
            {"method": "GET", "path": "/api/avr/mute/on", "telnet": "MUON"},
            {"method": "GET", "path": "/api/avr/mute/off", "telnet": "MUOFF"},
            {"method": "GET", "path": "/api/avr/mute/toggle", "telnet": "MUON or MUOFF (state-aware)"},
            {"method": "GET", "path": "/api/avr/source/<CODE>", "telnet": "SI<CODE>"},
            {"method": "GET", "path": "/api/uc/capabilities", "telnet": None, "note": "this endpoint"},
            {"method": "GET", "path": "/api/uc/custom_entities.yaml", "telnet": None, "note": "UC YAML generator"},
        ],
    }


def build_custom_entities_yaml(base_url: str, sources: list[tuple[str, str]]) -> str:
    """
    Build a ready-to-paste custom_entities.yaml for ucr2-integration-requests.

    Uses plain string building (no PyYAML dependency) to preserve the exact
    quoting and indentation that the UC integration requires. The caller passes
    the proxy's base URL, which is stored in a _vars block so the YAML remains
    portable (edit one line to point at a new proxy address).
    """
    lines: list[str] = []

    # _vars block – one line to change if the proxy address moves
    lines.append("_vars:")
    lines.append(f"  denon_proxy_url: {base_url}")
    lines.append("")

    lines.append("Denon AVR Proxy:")

    # Features: power on / off / toggle map to the UC On/Off/Toggle feature slots
    lines.append("  Features:")
    lines.append("    'On':")
    lines.append("      Type: get")
    lines.append("      Parameter: ${denon_proxy_url}/api/avr/power/on")
    lines.append("    'Off':")
    lines.append("      Type: get")
    lines.append("      Parameter: ${denon_proxy_url}/api/avr/power/off")
    lines.append("    Toggle:")
    lines.append("      Type: get")
    lines.append("      Parameter: ${denon_proxy_url}/api/avr/power/toggle")

    # Simple Commands: fixed controls + one entry per source
    lines.append("  Simple Commands:")

    static_cmds: list[tuple[str, str]] = [
        ("VOLUME_UP", "${denon_proxy_url}/api/avr/volume/up"),
        ("VOLUME_DOWN", "${denon_proxy_url}/api/avr/volume/down"),
        ("MUTE_ON", "${denon_proxy_url}/api/avr/mute/on"),
        ("MUTE_OFF", "${denon_proxy_url}/api/avr/mute/off"),
        ("MUTE_TOGGLE", "${denon_proxy_url}/api/avr/mute/toggle"),
    ]
    for cmd_name, param in static_cmds:
        lines.append(f"    {cmd_name}:")
        lines.append("      Type: get")
        lines.append(f"      Parameter: {param}")

    for func_code, _ in sources:
        cmd_name = normalize_source_cmd_name(func_code)
        lines.append(f"    {cmd_name}:")
        lines.append("      Type: get")
        lines.append(f"      Parameter: ${{denon_proxy_url}}/api/avr/source/{func_code}")

    # Selects: a single "Source" select entity listing every source as an option
    if sources:
        lines.append("  Selects:")
        lines.append("    Source:")
        for func_code, display_name in sources:
            cmd_name = normalize_source_cmd_name(func_code)
            lines.append(f"    - {cmd_name}: {display_name}")

    lines.append("")
    return "\n".join(lines)
