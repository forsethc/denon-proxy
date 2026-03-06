"""
Integration tests: discovery layer only (run_discovery_servers + AVRState).

Starts run_discovery_servers with a standalone AVRState (no proxy). Exercises
description.xml, deviceinfo, appcommand, ports, and M-SEARCH in isolation.
Faster and more focused than e2e; use e2e for full-stack discovery.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from runtime_state import AVRInfo, RuntimeState
from avr_discovery import run_discovery_servers, SSDP_MCAST_GRP, SSDP_MCAST_PORT
from avr_state import AVRState
from denon_proxy import load_config_from_dict


@pytest.fixture
def discovery_config():
    """Config for discovery-only: SSDP + discovery HTTP, no proxy."""
    return load_config_from_dict({
        "enable_ssdp": True,
        "ssdp_advertise_ip": "127.0.0.1",
        "ssdp_http_port": 0,
        "ssdp_friendly_name": "Test Discovery Proxy",
    })


@pytest.fixture
def discovery_logger():
    return logging.getLogger("test.discovery")


@pytest.fixture
async def discovery_servers(discovery_config, discovery_logger):
    """
    Start discovery only: run_discovery_servers(config, logger, state, runtime_state).
    Yields (ssdp_transport, http_servers, config, runtime_state). Teardown closes all.
    """
    state = AVRState()
    runtime_state = RuntimeState()
    runtime_state.avr_info = AVRInfo.virtual()
    ssdp_transport, http_servers = await run_discovery_servers(
        discovery_config, discovery_logger, state, runtime_state
    )
    yield ssdp_transport, http_servers, discovery_config, runtime_state
    if ssdp_transport:
        ssdp_transport.close()
    if http_servers:
        for srv in http_servers:
            srv.close()
        for srv in http_servers:
            await asyncio.wait_for(srv.wait_closed(), timeout=2.0)


async def _http_get(host: str, port: int, path: str) -> tuple[int, bytes]:
    """GET path; return (status_code, body)."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=2.0,
    )
    try:
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        writer.write(request)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=2.0)
    finally:
        writer.close()
        await writer.wait_closed()
    if b"\r\n\r\n" not in raw:
        return 0, raw
    headers, body = raw.split(b"\r\n\r\n", 1)
    status_line = headers.split(b"\r\n", 1)[0]
    if b"200" in status_line:
        return 200, body
    if b"404" in status_line:
        return 404, body
    return 0, body


async def _http_post(host: str, port: int, path: str, body: bytes) -> tuple[int, bytes]:
    """POST path with body; return (status_code, body)."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=2.0,
    )
    try:
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Content-Type: application/xml\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii") + body
        writer.write(request)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=2.0)
    finally:
        writer.close()
        await writer.wait_closed()
    if b"\r\n\r\n" not in raw:
        return 0, raw
    _, body_bytes = raw.split(b"\r\n\r\n", 1)
    status_line = raw.split(b"\r\n", 1)[0]
    if b"200" in status_line:
        return 200, body_bytes
    return 0, body_bytes


@pytest.mark.asyncio
async def test_discovery_description_xml_rendered(discovery_servers):
    """GET /description.xml returns 200 and valid device description with correct port."""
    _ssdp, http_servers, config, runtime_state = discovery_servers
    assert http_servers, "HTTP discovery server should start"
    port = runtime_state.ssdp_http_port
    assert port is not None and port != 0, "Dynamic port should be set"

    status, body = await _http_get("127.0.0.1", port, "/description.xml")
    assert status == 200, f"Expected 200, got {status} with body: {body[:200]!r}"
    text = body.decode("utf-8", errors="replace")
    assert "urn:schemas-upnp-org:device-1-0" in text or "device" in text.lower()
    assert "root" in text.lower() or "deviceType" in text
    assert "presentationURL" in text or "LOCATION" in text or "description.xml" in text
    assert "Test Discovery Proxy" in text
    assert f":{port}/" in text or f":{port}\"" in text


@pytest.mark.asyncio
async def test_discovery_root_returns_html(discovery_servers):
    """GET / (root) returns 200 and HTML."""
    _ssdp, http_servers, config, runtime_state = discovery_servers
    assert http_servers
    port = runtime_state.ssdp_http_port
    status, body = await _http_get("127.0.0.1", port, "/")
    assert status == 200
    text = body.decode("utf-8", errors="replace")
    assert "Denon AVR Proxy" in text or "html" in text.lower()


@pytest.mark.asyncio
async def test_discovery_alternate_description_paths(discovery_servers):
    """Paths aios_device.xml and upnp/desc also serve description XML."""
    _ssdp, http_servers, config, runtime_state = discovery_servers
    assert http_servers
    port = runtime_state.ssdp_http_port
    for path in ("/aios_device.xml", "/upnp/desc"):
        status, body = await _http_get("127.0.0.1", port, path)
        assert status == 200, f"GET {path} should return 200"
        text = body.decode("utf-8", errors="replace")
        assert "device" in text.lower() and "friendlyName" in text


@pytest.mark.asyncio
async def test_discovery_deviceinfo_xml_rendered(discovery_servers):
    """GET /goform/deviceinfo.xml returns 200 and Device_Info XML with sources."""
    _ssdp, http_servers, config, runtime_state = discovery_servers
    assert http_servers
    port = runtime_state.ssdp_http_port
    status, body = await _http_get("127.0.0.1", port, "/goform/deviceinfo.xml")
    assert status == 200
    text = body.decode("utf-8", errors="replace")
    assert "<Device_Info>" in text
    assert "ModelName" in text or "AVR-" in text
    assert "Source" in text or "FuncName" in text


@pytest.mark.asyncio
async def test_discovery_appcommand_get_friendly_name(discovery_servers):
    """POST /goform/appcommand.xml with GetFriendlyName returns 200 and friendlyname."""
    _ssdp, http_servers, config, runtime_state = discovery_servers
    assert http_servers
    port = runtime_state.ssdp_http_port
    body = b'<tx><cmd id="1">GetFriendlyName</cmd></tx>'
    status, resp_body = await _http_post(
        "127.0.0.1", port, "/goform/appcommand.xml", body
    )
    assert status == 200
    text = resp_body.decode("utf-8", errors="replace")
    assert "friendlyname" in text.lower()
    assert "Test Discovery Proxy" in text


@pytest.mark.asyncio
async def test_discovery_http_ports_exposed(discovery_servers):
    """Discovery HTTP server listens on the port advertised in description.xml."""
    _ssdp, http_servers, config, runtime_state = discovery_servers
    assert http_servers
    port = runtime_state.ssdp_http_port
    status, _ = await _http_get("127.0.0.1", port, "/description.xml")
    assert status == 200
    status2, body = await _http_get("127.0.0.1", port, "/description.xml")
    assert status2 == 200
    assert f"http://127.0.0.1:{port}/description.xml" in body.decode("utf-8")


@pytest.mark.asyncio
async def test_discovery_msearch_handled_and_responded(discovery_servers):
    """M-SEARCH (Denon URN) receives HTTP 200 response with LOCATION and ST/USN.
    Skipped when port 1900 is unavailable.
    """
    ssdp_transport, http_servers, config, runtime_state = discovery_servers
    if ssdp_transport is None:
        pytest.skip("SSDP port 1900 unavailable (need root or run with cap_net_bind_service)")
    http_port = runtime_state.ssdp_http_port

    msearch = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_MCAST_GRP}:{SSDP_MCAST_PORT}\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        "ST: urn:schemas-denon-com:device:AiosDevice:1\r\n"
        "\r\n"
    ).encode("utf-8")

    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: _OneShotDatagramProtocol(),
        local_addr=("0.0.0.0", 0),
    )
    try:
        transport.sendto(msearch, (SSDP_MCAST_GRP, SSDP_MCAST_PORT))
        response = await asyncio.wait_for(protocol.get_response(), timeout=2.0)
    finally:
        transport.close()

    assert response, "Expected SSDP response"
    text = response.decode("utf-8", errors="replace")
    assert "HTTP/1.1 200" in text or "200 OK" in text
    assert "LOCATION:" in text and "description.xml" in text
    assert "ST:" in text and "USN:" in text
    assert f"http://127.0.0.1:{http_port}/description.xml" in text


class _OneShotDatagramProtocol(asyncio.DatagramProtocol):
    """Capture one datagram response."""

    def __init__(self) -> None:
        self._response: asyncio.Future = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if not self._response.done():
            self._response.set_result(data)

    async def get_response(self) -> bytes:
        return await self._response
