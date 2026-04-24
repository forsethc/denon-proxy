"""
Integration tests for the /api/avr/... and /api/uc/... HTTP control endpoints.

Spins up run_http_server directly with minimal stubs (no full proxy stack)
to confirm routing, response shapes, and content-type headers.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from denon_proxy.avr.state import AVRState
from denon_proxy.http.server import run_http_server
from denon_proxy.runtime.config import Config
from denon_proxy.runtime.state import RuntimeState

SOURCES = [
    ("CD", "CD"),
    ("HDMI1", "HDMI 1"),
    ("SAT/CBL", "CBL/SAT"),
]


async def _open(port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=2.0)


def _parse_response(raw: bytes) -> tuple[bytes, bytes, bytes]:
    """Return (status_line, all_headers_bytes, body_bytes)."""
    head, _, body = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0]
    return status_line, head, body


async def _get(port: int, path: str) -> tuple[bytes, bytes, bytes]:
    reader, writer = await _open(port)
    try:
        writer.write(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=2.0)
    finally:
        writer.close()
        await writer.wait_closed()
    return _parse_response(raw)


@pytest.fixture
async def avr_control_server():
    """Minimal HTTP server with AVR control callbacks enabled."""
    config = Config.model_validate({"enable_http": True, "http_port": 0, "enable_ssdp": False})
    avr_state = AVRState()
    commands: list[str] = []

    def send_command(cmd: str) -> None:
        commands.append(cmd)

    result = await run_http_server(
        config,
        logging.getLogger("test.avr_control"),
        get_state=lambda: {},
        send_command=send_command,
        runtime_state=RuntimeState(),
        get_avr_state=lambda: avr_state,
        get_sources=lambda: SOURCES,
        get_base_url=lambda: "http://127.0.0.1:8081",
    )
    assert result is not None
    server, _ = result
    port = server.sockets[0].getsockname()[1]

    yield port, avr_state, commands

    server.close()
    await server.wait_closed()


# ---------------------------------------------------------------------------
# Power endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_avr_power_on(avr_control_server):
    port, _, commands = avr_control_server
    status, _, body = await _get(port, "/api/avr/power/on")
    assert b"200" in status
    data = json.loads(body)
    assert data == {"ok": True, "command": "PWON"}
    assert commands[-1] == "PWON"


@pytest.mark.asyncio
async def test_avr_power_off(avr_control_server):
    port, _, commands = avr_control_server
    status, _, body = await _get(port, "/api/avr/power/off")
    assert b"200" in status
    assert json.loads(body) == {"ok": True, "command": "PWSTANDBY"}


@pytest.mark.asyncio
async def test_avr_power_toggle_from_on(avr_control_server):
    port, avr_state, commands = avr_control_server
    avr_state.power = "ON"
    status, _, body = await _get(port, "/api/avr/power/toggle")
    assert b"200" in status
    assert json.loads(body)["command"] == "PWSTANDBY"


@pytest.mark.asyncio
async def test_avr_power_unknown_action_404(avr_control_server):
    port, _, _ = avr_control_server
    status, _, _ = await _get(port, "/api/avr/power/restart")
    assert b"404" in status


# ---------------------------------------------------------------------------
# Volume endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_avr_volume_up(avr_control_server):
    port, _, commands = avr_control_server
    status, _, body = await _get(port, "/api/avr/volume/up")
    assert b"200" in status
    assert json.loads(body)["command"] == "MVUP"


@pytest.mark.asyncio
async def test_avr_volume_set(avr_control_server):
    port, _, commands = avr_control_server
    status, _, body = await _get(port, "/api/avr/volume/set?level=45")
    assert b"200" in status
    assert json.loads(body)["command"] == "MV45"


@pytest.mark.asyncio
async def test_avr_volume_set_half_step(avr_control_server):
    port, _, _ = avr_control_server
    status, _, body = await _get(port, "/api/avr/volume/set?level=45.5")
    assert b"200" in status
    assert json.loads(body)["command"] == "MV455"


@pytest.mark.asyncio
async def test_avr_volume_set_missing_level_400(avr_control_server):
    port, _, _ = avr_control_server
    status, _, _ = await _get(port, "/api/avr/volume/set")
    assert b"400" in status


# ---------------------------------------------------------------------------
# Mute endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_avr_mute_on(avr_control_server):
    port, _, commands = avr_control_server
    status, _, body = await _get(port, "/api/avr/mute/on")
    assert b"200" in status
    assert json.loads(body)["command"] == "MUON"


@pytest.mark.asyncio
async def test_avr_mute_toggle_when_unmuted(avr_control_server):
    port, avr_state, _ = avr_control_server
    avr_state.mute = False
    status, _, body = await _get(port, "/api/avr/mute/toggle")
    assert b"200" in status
    assert json.loads(body)["command"] == "MUON"


# ---------------------------------------------------------------------------
# Source endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_avr_source_known(avr_control_server):
    port, _, commands = avr_control_server
    status, _, body = await _get(port, "/api/avr/source/CD")
    assert b"200" in status
    assert json.loads(body)["command"] == "SICD"


@pytest.mark.asyncio
async def test_avr_source_unknown_404(avr_control_server):
    port, _, _ = avr_control_server
    status, _, _ = await _get(port, "/api/avr/source/BLURAY")
    assert b"404" in status


# ---------------------------------------------------------------------------
# UC capabilities endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uc_capabilities_json(avr_control_server):
    port, _, _ = avr_control_server
    status, headers, body = await _get(port, "/api/uc/capabilities")
    assert b"200" in status
    assert b"application/json" in headers
    data = json.loads(body)
    assert "base_url" in data
    assert "features" in data
    assert "endpoints" in data
    assert "power" in data["features"]
    assert "source" in data["features"]
    opts = data["features"]["source"]["options"]
    assert any(o["code"] == "CD" for o in opts)
    assert any(o["code"] == "SAT/CBL" for o in opts)


# ---------------------------------------------------------------------------
# UC custom_entities.yaml endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uc_yaml_content_type(avr_control_server):
    port, _, _ = avr_control_server
    status, headers, _ = await _get(port, "/api/uc/custom_entities.yaml")
    assert b"200" in status
    assert b"text/yaml" in headers


@pytest.mark.asyncio
async def test_uc_yaml_content(avr_control_server):
    port, _, _ = avr_control_server
    _, _, body = await _get(port, "/api/uc/custom_entities.yaml")
    yaml_text = body.decode("utf-8")
    assert "_vars:" in yaml_text
    assert "Denon AVR Proxy:" in yaml_text
    assert "VOLUME_UP:" in yaml_text
    assert "SRC_CD:" in yaml_text
    assert "SRC_SAT_CBL:" in yaml_text
    assert "Selects:" in yaml_text


@pytest.mark.asyncio
async def test_uc_yaml_uses_host_header(avr_control_server):
    """The generated YAML's _vars block must use the request Host header."""
    port, _, _ = avr_control_server
    reader, writer = await _open(port)
    try:
        writer.write(
            b"GET /api/uc/custom_entities.yaml HTTP/1.1\r\n"
            b"Host: myproxy.local:8081\r\n"
            b"Connection: close\r\n\r\n"
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=2.0)
    finally:
        writer.close()
        await writer.wait_closed()
    _, _, body = _parse_response(raw)
    yaml_text = body.decode("utf-8")
    assert "http://myproxy.local:8081" in yaml_text


@pytest.mark.asyncio
async def test_avr_power_on_via_post(avr_control_server):
    """POST is accepted on /api/avr/... routes the same as GET."""
    port, _, commands = avr_control_server
    reader, writer = await _open(port)
    try:
        writer.write(
            f"POST /api/avr/power/on HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=2.0)
    finally:
        writer.close()
        await writer.wait_closed()
    status, _, body = _parse_response(raw)
    assert b"200" in status
    assert json.loads(body)["command"] == "PWON"
