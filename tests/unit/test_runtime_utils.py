from pathlib import Path
from unittest.mock import patch

from runtime_utils import is_docker_internal_ip, is_running_in_docker


def test_is_running_in_docker_detection():
    # Patch Path.exists so we can assert True when container markers exist
    with patch.object(Path, "exists", lambda self: str(self) in ("/.dockerenv", "/run/.containerenv")):
        assert is_running_in_docker() is True
    with patch.object(Path, "exists", lambda self: False):
        assert is_running_in_docker() is False


def test_is_docker_internal_ip():
    # 172.16.0.0/12 (Docker Linux bridge)
    assert is_docker_internal_ip("172.17.0.1") is True
    assert is_docker_internal_ip("172.19.0.2") is True
    assert is_docker_internal_ip("172.16.0.1") is True
    assert is_docker_internal_ip("172.31.255.255") is True
    # 192.168.65.0/24 (Docker Desktop for Mac/Windows)
    assert is_docker_internal_ip("192.168.65.1") is True
    assert is_docker_internal_ip("192.168.65.255") is True
    assert is_docker_internal_ip("192.168.64.1") is False
    assert is_docker_internal_ip("192.168.66.1") is False
    assert is_docker_internal_ip("192.168.1.1") is False
    assert is_docker_internal_ip("10.0.0.1") is False
    assert is_docker_internal_ip("172.15.0.1") is False
    assert is_docker_internal_ip("172.32.0.1") is False
    assert is_docker_internal_ip(None) is False
    assert is_docker_internal_ip("") is False
