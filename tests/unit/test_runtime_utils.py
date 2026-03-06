from pathlib import Path
from unittest.mock import MagicMock, patch

from runtime_utils import is_docker_internal_ip, is_running_in_docker, resolve_listening_port


def test_is_docker_internal_ip_rejects_non_string_or_empty():
    """Non-string or empty ip returns False before parsing."""
    assert is_docker_internal_ip(None) is False
    assert is_docker_internal_ip("") is False
    assert is_docker_internal_ip(123) is False


def test_is_docker_internal_ip_rejects_invalid_octets():
    """IP with non-numeric octets returns False (ValueError path)."""
    assert is_docker_internal_ip("172.16.0.a") is False
    assert is_docker_internal_ip("192.168.65.1x") is False


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


def test_resolve_listening_port_when_port_0_sets_target_attr():
    """When requested_port is 0 and server has sockets, target.<port_attr> is set to bound port."""
    mock_sock = MagicMock()
    mock_sock.getsockname.return_value = ("0.0.0.0", 43210)
    server = MagicMock(sockets=[mock_sock])
    target = MagicMock()

    resolve_listening_port(server, 0, target, "http_port")

    assert target.http_port == 43210


def test_resolve_listening_port_when_port_nonzero_does_nothing():
    """When requested_port is non-zero, target is not modified."""
    mock_sock = MagicMock()
    mock_sock.getsockname.return_value = ("0.0.0.0", 8081)
    server = MagicMock(sockets=[mock_sock])
    target = object()  # plain object, no attributes

    resolve_listening_port(server, 8081, target, "http_port")

    assert not hasattr(target, "http_port")


def test_resolve_listening_port_when_no_sockets_does_nothing():
    """When server has no sockets, target is not modified."""
    server = MagicMock(sockets=[])
    target = object()

    resolve_listening_port(server, 0, target, "proxy_port")

    assert not hasattr(target, "proxy_port")


def test_resolve_listening_port_when_sockets_missing_does_nothing():
    """When server has no 'sockets' attr, target is not modified."""
    server = object()  # no sockets attr
    target = object()

    resolve_listening_port(server, 0, target, "proxy_port")

    assert not hasattr(target, "proxy_port")
