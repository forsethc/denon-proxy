"""
Runtime / environment helpers: container detection, internal IP classification,
server port resolution, and version from git.
"""

from __future__ import annotations

import ipaddress
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import Config
    from runtime_state import RuntimeState

_DOCKER_NETWORKS = (
    ipaddress.IPv4Network("172.16.0.0/12"), # typical Docker bridge on Linux
    ipaddress.IPv4Network("192.168.65.0/24"), # Docker Desktop for Mac/Windows
)


def get_version() -> str:
    """
    Get version from git describe when in a git repo, else from VERSION file.
    Docker builds (no .git) use the VERSION file written by the release workflow.
    Falls back to 'unknown' when neither is available.
    """
    root = Path(__file__).resolve().parent
    # Prefer git when available (local dev, accurate for current commit)
    if (root / ".git").exists():
        try:
            r = subprocess.run(
                ["git", "describe", "--tags", "--always", "--dirty"],
                capture_output=True,
                text=True,
                timeout=2,
                cwd=root,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
    # No git (e.g. Docker): use VERSION file
    version_file = root / "VERSION"
    try:
        if version_file.exists():
            v = version_file.read_text().strip()
            if v:
                return v
    except OSError:
        pass
    return "unknown"


def is_running_in_docker() -> bool:
    """
    True if the process is running inside a Docker or Podman container.
    Uses /.dockerenv (Docker) and /run/.containerenv (Podman) so client IPs
    will appear as the container gateway (e.g. 192.168.65.1 on Docker Desktop).
    """
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def is_docker_internal_ip(ip: str | None) -> bool:
    """
    True if ip is in a Docker/internal bridge range:
    - 172.16.0.0/12 (typical Docker bridge on Linux)
    - 192.168.65.0/24 (Docker Desktop for Mac/Windows gateway)
    """
    if not ip or not isinstance(ip, str):
        return False
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    if not isinstance(addr, ipaddress.IPv4Address):
        return False
    return any(addr in net for net in _DOCKER_NETWORKS)


def resolve_listening_port(
    server: object,
    requested_port: int,
    target: object,
    port_attr: str,
) -> None:
    """
    When requested_port was 0 (OS pick), set target.<port_attr> to the actual bound port.
    Use after create_server(..., port=requested_port) so callers know the chosen port.
    """
    if requested_port == 0 and getattr(server, "sockets", None):
        setattr(target, port_attr, server.sockets[0].getsockname()[1])


def get_resolved_port(
    runtime_state: "RuntimeState",
    config: "Config",
    config_key: str,
    default: int,
) -> int:
    """
    Return the effective port: runtime_state's resolved value if set (e.g. from binding to 0),
    else config[config_key] with default. Use for ssdp_http_port, http_port, proxy_port.
    """
    resolved = getattr(runtime_state, config_key, None)
    if resolved is not None:
        return int(resolved)
    return int(config.get(config_key, default))
