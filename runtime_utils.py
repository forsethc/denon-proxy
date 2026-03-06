"""
Runtime / environment helpers: container detection and internal IP classification.

Used by the proxy (build_json_state) and discovery (SSDP warning) to decide
when to show Docker-related UI messages and when the advertised IP is internal.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

_DOCKER_NETWORKS = (
    ipaddress.IPv4Network("172.16.0.0/12"), # typical Docker bridge on Linux
    ipaddress.IPv4Network("192.168.65.0/24"), # Docker Desktop for Mac/Windows
)


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
