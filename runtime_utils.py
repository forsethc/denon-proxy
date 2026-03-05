"""
Runtime / environment helpers: container detection and internal IP classification.

Used by the proxy (build_json_state) and discovery (SSDP warning) to decide
when to show Docker-related UI messages and when the advertised IP is internal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def is_running_in_docker() -> bool:
    """
    True if the process is running inside a Docker or Podman container.
    Uses /.dockerenv (Docker) and /run/.containerenv (Podman) so client IPs
    will appear as the container gateway (e.g. 192.168.65.1 on Docker Desktop).
    """
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def is_docker_internal_ip(ip: Optional[str]) -> bool:
    """
    True if ip is in a Docker/internal bridge range:
    - 172.16.0.0/12 (typical Docker bridge on Linux)
    - 192.168.65.0/24 (Docker Desktop for Mac/Windows gateway)
    """
    if not ip or not isinstance(ip, str):
        return False
    parts = ip.strip().split(".")
    if len(parts) != 4:
        return False
    try:
        a, b, c, d = (int(p) for p in parts)
        if 0 <= a <= 255 and 0 <= b <= 255 and 0 <= c <= 255 and 0 <= d <= 255:
            # 172.16.0.0/12 = 172.16.x.x - 172.31.x.x
            if a == 172 and 16 <= b <= 31:
                return True
            # 192.168.65.0/24 = Docker Desktop for Mac/Windows
            if a == 192 and b == 168 and c == 65:
                return True
    except ValueError:
        pass
    return False
