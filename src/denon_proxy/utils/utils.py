"""
Runtime / environment helpers: container detection, internal IP classification,
server port resolution, and version from git.
"""

from __future__ import annotations

import ipaddress
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from denon_proxy.runtime.config import Config
    from denon_proxy.runtime.state import RuntimeState

_DOCKER_NETWORKS = (
    ipaddress.IPv4Network("172.16.0.0/12"), # typical Docker bridge on Linux
    ipaddress.IPv4Network("192.168.65.0/24"), # Docker Desktop for Mac/Windows
)


def get_version() -> str:
    """
    Get version from installed package metadata provided by setuptools-scm.

    setuptools-scm derives the version from Git tags and the current state
    (including local version information such as commits since tag and
    dirty status), and exposes it via importlib.metadata.
    """
    try:
        return metadata.version("denon-proxy")
    except metadata.PackageNotFoundError:
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


from typing import Any


def resolve_listening_port(
    server: Any,
    requested_port: int,
    target: object,
    port_attr: str,
) -> None:
    """
    When requested_port was 0 (OS pick), set target.<port_attr> to the actual bound port.
    Use after create_server(..., port=requested_port) so callers know the chosen port.
    """
    sockets = getattr(server, "sockets", None)
    if requested_port == 0 and sockets:
        setattr(target, port_attr, sockets[0].getsockname()[1])


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
