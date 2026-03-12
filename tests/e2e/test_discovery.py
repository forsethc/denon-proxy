"""
E2E tests: full application stack (DenonProxyServer + discovery).

Spins up DenonProxyServer (VirtualAVR) + run_discovery_servers, then hits discovery
endpoints with real TCP HTTP. Matches how the app runs (main_async). M-SEARCH tests
send to 239.255.255.250:1900 and are skipped when port 1900 is unavailable.

Fixtures discovery_config, discovery_logger, discovery_stack are in conftest.py.
"""

from __future__ import annotations

import asyncio

import pytest

from denon_proxy.constants import SSDP_MCAST_GRP, SSDP_MCAST_PORT


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
async def test_discovery_description_xml_rendered(discovery_stack):
    """GET /description.xml returns 200 and valid device description with correct port."""
    proxy, _ssdp, http_servers = discovery_stack
    assert http_servers, "HTTP discovery server should start"
    port = proxy.runtime_state.ssdp_http_port or proxy.config.get("ssdp_http_port")
    assert port and port != 0, "Dynamic port should be set"

    status, body = await _http_get("127.0.0.1", port, "/description.xml")
    assert status == 200, f"Expected 200, got {status} with body: {body[:200]!r}"
    text = body.decode("utf-8", errors="replace")
    assert "urn:schemas-upnp-org:device-1-0" in text or "device" in text.lower()
    assert "root" in text.lower() or "deviceType" in text
    assert "presentationURL" in text or "LOCATION" in text or "description.xml" in text
    assert "Test Discovery Proxy" in text
    assert f":{port}/" in text or f":{port}\"" in text


@pytest.mark.asyncio
async def test_discovery_root_returns_html(discovery_stack):
    """GET / (root) returns 200 and HTML (description.xml is the XML endpoint)."""
    proxy, _ssdp, http_servers = discovery_stack
    assert http_servers
    port = proxy.runtime_state.ssdp_http_port or proxy.config.get("ssdp_http_port")
    status, body = await _http_get("127.0.0.1", port, "/")
    assert status == 200
    text = body.decode("utf-8", errors="replace")
    assert "Denon AVR Proxy" in text or "html" in text.lower()


@pytest.mark.asyncio
async def test_discovery_alternate_description_paths(discovery_stack):
    """Paths aios_device.xml and upnp/desc also serve description XML."""
    proxy, _ssdp, http_servers = discovery_stack
    assert http_servers
    port = proxy.runtime_state.ssdp_http_port or proxy.config.get("ssdp_http_port")
    for path in ("/aios_device.xml", "/upnp/desc"):
        status, body = await _http_get("127.0.0.1", port, path)
        assert status == 200, f"GET {path} should return 200"
        text = body.decode("utf-8", errors="replace")
        assert "device" in text.lower() and "friendlyName" in text


@pytest.mark.asyncio
async def test_discovery_deviceinfo_xml_rendered(discovery_stack):
    """GET /goform/deviceinfo.xml returns 200 and Device_Info XML with sources."""
    proxy, _ssdp, http_servers = discovery_stack
    assert http_servers
    port = proxy.runtime_state.ssdp_http_port or proxy.config.get("ssdp_http_port")
    status, body = await _http_get("127.0.0.1", port, "/goform/deviceinfo.xml")
    assert status == 200
    text = body.decode("utf-8", errors="replace")
    assert "<Device_Info>" in text
    assert "ModelName" in text or "AVR-" in text
    assert "Source" in text or "FuncName" in text


@pytest.mark.asyncio
async def test_discovery_appcommand_get_friendly_name(discovery_stack):
    """POST /goform/appcommand.xml with GetFriendlyName returns 200 and friendlyname."""
    proxy, _ssdp, http_servers = discovery_stack
    assert http_servers
    port = proxy.runtime_state.ssdp_http_port or proxy.config.get("ssdp_http_port")
    body = b'<tx><cmd id="1">GetFriendlyName</cmd></tx>'
    status, resp_body = await _http_post(
        "127.0.0.1", port, "/goform/appcommand.xml", body
    )
    assert status == 200
    text = resp_body.decode("utf-8", errors="replace")
    assert "friendlyname" in text.lower()
    assert "Test Discovery Proxy" in text


@pytest.mark.asyncio
async def test_discovery_http_ports_exposed(discovery_stack):
    """Discovery HTTP server listens on the port advertised in description.xml."""
    proxy, _ssdp, http_servers = discovery_stack
    assert http_servers
    port = proxy.runtime_state.ssdp_http_port or proxy.config.get("ssdp_http_port")
    status, _ = await _http_get("127.0.0.1", port, "/description.xml")
    assert status == 200
    status2, body = await _http_get("127.0.0.1", port, "/description.xml")
    assert status2 == 200
    assert f"http://127.0.0.1:{port}/description.xml" in body.decode("utf-8")


@pytest.mark.asyncio
async def test_discovery_msearch_handled_and_responded(discovery_stack):
    """M-SEARCH (Denon URN) receives HTTP 200 response with LOCATION and ST/USN."""
    proxy, _ssdp_transport, http_servers = discovery_stack
    http_port = proxy.runtime_state.ssdp_http_port or proxy.config.get("ssdp_http_port")
    assert http_port and http_port != 0, "Discovery HTTP port should be set"

    msearch = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_MCAST_GRP}:{SSDP_MCAST_PORT}\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        "ST: urn:schemas-denon-com:device:AiosDevice:1\r\n"
        "\r\n"
    ).encode()

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
    assert "ST:" in text
    assert "USN:" in text
    assert f"http://127.0.0.1:{http_port}/description.xml" in text
    assert "CACHE-CONTROL:" in text
    assert "EXT:" in text
    assert "SERVER:" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("st_value", [
    "ssdp:all",
    "upnp:rootdevice",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:device:MediaServer:1",
])
async def test_discovery_msearch_all_match_st_receive_response(st_value, discovery_stack):
    """Each MATCH_ST search target receives a 200 response with LOCATION."""
    proxy, _ssdp_transport, http_servers = discovery_stack
    http_port = proxy.runtime_state.ssdp_http_port or proxy.config.get("ssdp_http_port")
    assert http_port and http_port != 0, "Discovery HTTP port should be set"

    msearch = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_MCAST_GRP}:{SSDP_MCAST_PORT}\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        f"ST: {st_value}\r\n"
        "\r\n"
    ).encode()

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

    assert response, f"Expected SSDP response for ST={st_value!r}"
    text = response.decode("utf-8", errors="replace")
    assert "200" in text
    assert "LOCATION:" in text and "description.xml" in text
    assert f"ST: {st_value}" in text
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
