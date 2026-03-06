"""
Runtime / environment helpers: container detection, internal IP classification,
and server port resolution (e.g. record OS-chosen port when binding to 0).
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
