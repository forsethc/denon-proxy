"""Unit tests for denon_proxy.avr.discover (AVR discovery via SSDP/mDNS)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from denon_proxy.avr.discover import (
    DiscoveredAVR,
    _is_denon_proxy,
    _is_denon_ssdp_response,
    _parse_friendly_name,
    _parse_ssdp_extra_headers_text,
    _parse_ssdp_location_text,
    _parse_ssdp_response,
    _parse_ssdp_server_or_usn_text,
    discover,
    discover_via_mdns,
    discover_via_ssdp,
    mdns_available,
)
from denon_proxy.constants import PROXY_NAME, PROXY_SERVER_PRODUCT

# --- _parse_ssdp_location_text ---


def test_parse_ssdp_location_returns_host_port_and_url():
    msg = "HTTP/1.1 200 OK\r\nLOCATION: http://192.168.1.50:80/description.xml\r\nCACHE-CONTROL: max-age=1800\r\n\r\n"
    assert _parse_ssdp_location_text(msg) == ("192.168.1.50", 80, "http://192.168.1.50:80/description.xml")


def test_parse_ssdp_location_default_port_80():
    msg = "HTTP/1.1 200 OK\r\nLOCATION: http://10.0.0.1/foo.xml\r\n\r\n"
    assert _parse_ssdp_location_text(msg) == ("10.0.0.1", 80, "http://10.0.0.1/foo.xml")


def test_parse_ssdp_location_non_200_returns_none():
    assert _parse_ssdp_location_text("HTTP/1.1 404 Not Found\r\n\r\n") is None


def test_parse_ssdp_location_no_location_returns_none():
    msg = "HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\n\r\n"
    assert _parse_ssdp_location_text(msg) is None


def test_parse_ssdp_location_case_insensitive():
    msg = "HTTP/1.1 200 OK\r\nlocation: http://127.0.0.1:8080/desc.xml\r\n\r\n"
    assert _parse_ssdp_location_text(msg) == ("127.0.0.1", 8080, "http://127.0.0.1:8080/desc.xml")


def test_parse_ssdp_location_https_default_port_443():
    msg = "HTTP/1.1 200 OK\r\nLOCATION: https://192.168.1.1/desc.xml\r\n\r\n"
    assert _parse_ssdp_location_text(msg) == ("192.168.1.1", 443, "https://192.168.1.1/desc.xml")


def test_parse_ssdp_location_invalid_url_returns_none():
    assert _parse_ssdp_location_text("HTTP/1.1 200 OK\r\nLOCATION: :://not-a-valid-url\r\n\r\n") is None


# --- _parse_ssdp_extra_headers_text ---


def test_parse_ssdp_extra_headers_extracts_usn_and_max_age():
    msg = (
        "HTTP/1.1 200 OK\r\n"
        "LOCATION: http://192.168.1.1/\r\n"
        "USN: uuid:abc-123::urn:denon:device:1\r\n"
        "CACHE-CONTROL: max-age=1800\r\n\r\n"
    )
    extra = _parse_ssdp_extra_headers_text(msg)
    assert extra["usn"] == "uuid:abc-123::urn:denon:device:1"
    assert extra["max_age"] == 1800


# --- _parse_ssdp_server_or_usn_text ---


def test_parse_ssdp_server_extracts_server_header():
    msg = "HTTP/1.1 200 OK\r\nSERVER: Linux/1.0 UPnP/1.0 Denon/1.0\r\n\r\n"
    assert _parse_ssdp_server_or_usn_text(msg) == "Linux/1.0 UPnP/1.0 Denon/1.0"


def test_parse_ssdp_server_missing_returns_none():
    msg = "HTTP/1.1 200 OK\r\nLOCATION: http://a/b\r\n\r\n"
    assert _parse_ssdp_server_or_usn_text(msg) is None


def test_parse_ssdp_server_fallback_to_usn_when_no_server():
    msg = (
        "HTTP/1.1 200 OK\r\n"
        "USN: uuid:abc::urn:schemas-denon-com:device:AiosDevice:1\r\n"
        "LOCATION: http://192.168.1.50:80/description.xml\r\n\r\n"
    )
    assert "urn:schemas-denon-com" in (_parse_ssdp_server_or_usn_text(msg) or "")


# --- _is_denon_ssdp_response ---


def test_is_denon_ssdp_response_accepts_denon_server():
    parsed = _parse_ssdp_response(
        b"HTTP/1.1 200 OK\r\nSERVER: Linux/1.0 UPnP/1.0 Denon/1.0\r\nLOCATION: http://10.0.0.1/\r\n\r\n"
    )
    assert _is_denon_ssdp_response(parsed.server_or_usn if parsed else None) is True


def test_is_denon_ssdp_response_accepts_marantz_server():
    parsed = _parse_ssdp_response(
        b"HTTP/1.1 200 OK\r\nSERVER: Marantz/1.0 UPnP/1.0\r\nLOCATION: http://10.0.0.1/\r\n\r\n"
    )
    assert _is_denon_ssdp_response(parsed.server_or_usn if parsed else None) is True


def test_is_denon_ssdp_response_accepts_knos_dmp_avr():
    """Some Denon AVRs advertise as KnOS/3.2 UPnP/1.0 DMP/3.5 (no 'Denon' in SERVER)."""
    parsed = _parse_ssdp_response(
        b"HTTP/1.1 200 OK\r\n"
        b"SERVER: KnOS/3.2 UPnP/1.0 DMP/3.5\r\n"
        b"LOCATION: http://10.0.2.5:8080/description.xml\r\n\r\n"
    )
    assert _is_denon_ssdp_response(parsed.server_or_usn if parsed else None) is True


def test_is_denon_ssdp_response_accepts_denon_in_usn_when_no_server():
    parsed = _parse_ssdp_response(
        b"HTTP/1.1 200 OK\r\n"
        b"USN: uuid:abc::urn:schemas-denon-com:device:AiosDevice:1\r\n"
        b"LOCATION: http://10.0.0.1/\r\n\r\n"
    )
    assert _is_denon_ssdp_response(parsed.server_or_usn if parsed else None) is True


def test_is_denon_ssdp_response_accepts_aios_in_usn():
    """USN often contains AiosDevice (Denon Aios platform); aios is a marker."""
    parsed = _parse_ssdp_response(
        b"HTTP/1.1 200 OK\r\n"
        b"USN: uuid:xyz::urn:example:device:AiosDevice:1\r\n"
        b"LOCATION: http://192.168.1.10:8080/\r\n\r\n"
    )
    assert _is_denon_ssdp_response(parsed.server_or_usn if parsed else None) is True


def test_is_denon_ssdp_response_rejects_unknown_vendor():
    parsed = _parse_ssdp_response(
        b"HTTP/1.1 200 OK\r\nSERVER: SomeOther/1.0 UPnP/1.0\r\nLOCATION: http://10.0.0.1/\r\n\r\n"
    )
    assert _is_denon_ssdp_response(parsed.server_or_usn if parsed else None) is False


def test_is_denon_ssdp_response_rejects_when_no_server_or_usn():
    parsed = _parse_ssdp_response(b"HTTP/1.1 200 OK\r\nLOCATION: http://10.0.0.1/\r\n\r\n")
    assert _is_denon_ssdp_response(parsed.server_or_usn if parsed else None) is False


# --- _is_denon_proxy ---


def test_is_denon_proxy_detects_ssdp_server():
    """Proxy advertises SERVER: Linux/1.0 UPnP/1.0 {PROXY_SERVER_PRODUCT}/1.0."""
    assert _is_denon_proxy(f"Linux/1.0 UPnP/1.0 {PROXY_SERVER_PRODUCT}/1.0", None) is True


def test_is_denon_proxy_detects_usn():
    """Proxy USN is uuid:{PROXY_NAME}-<serial>::..."""
    assert _is_denon_proxy(None, f"uuid:{PROXY_NAME}-abc123::urn:schemas-upnp-org:device:root:1") is True


def test_is_denon_proxy_detects_friendly_name():
    """Friendly name may be 'Denon AVR Proxy' or contain PROXY_NAME."""
    assert _is_denon_proxy(None, "Denon AVR Proxy") is True
    assert _is_denon_proxy(None, f"Living Room ({PROXY_NAME})") is True


def test_is_denon_proxy_rejects_real_avr():
    """Real Denon AVR SERVER does not contain proxy markers."""
    assert _is_denon_proxy("Linux/1.0 UPnP/1.0 Denon/1.0", None) is False
    assert _is_denon_proxy(None, "Denon AVR-X2700H") is False


# --- _parse_friendly_name ---


def test_parse_friendly_name_denon_avr_model():
    assert _parse_friendly_name("Denon AVR-X2700H") == ("Denon", "AVR-X2700H")
    assert _parse_friendly_name("Denon AVR-X2700H._http._tcp.local.") == ("Denon", "AVR-X2700H")


def test_parse_friendly_name_marantz_model():
    assert _parse_friendly_name("Marantz SR5015") == ("Marantz", "SR5015")


def test_parse_friendly_name_parentheses():
    assert _parse_friendly_name("Living Room (Denon AVR-X2700H)") == ("Denon", "AVR-X2700H")


def test_parse_friendly_name_server_string():
    assert _parse_friendly_name("Linux/1.0 UPnP/1.0 Denon/1.0") == ("Denon", None)


def test_parse_friendly_name_empty_or_none():
    assert _parse_friendly_name(None) == (None, None)
    assert _parse_friendly_name("") == (None, None)


def test_parse_friendly_name_usn_returns_brand():
    """USN string (no SERVER) still yields brand from 'denon' in urn."""
    assert _parse_friendly_name("uuid:xyz::urn:schemas-denon-com:device:AiosDevice:1") == (
        "Denon",
        None,
    )


def test_parse_friendly_name_marantz_only_fallback():
    """When only 'Marantz' appears (no model in regex), brand is Marantz."""
    assert _parse_friendly_name("Marantz/1.0 UPnP/1.0") == ("Marantz", None)


# --- DiscoveredAVR ---


def test_discovered_avr_as_dict():
    avr = DiscoveredAVR("192.168.1.1", 80, "My AVR", "http://192.168.1.1/d", "ssdp")
    d = avr.as_dict()
    assert d["host"] == "192.168.1.1"
    assert d["port"] == 80
    assert d["name"] == "My AVR"
    assert d["location"] == "http://192.168.1.1/d"
    assert d["method"] == "ssdp"
    assert d["matched"] is True


def test_discovered_avr_as_dict_includes_parsed_info_when_present():
    avr = DiscoveredAVR(
        "192.168.1.1",
        80,
        "Denon AVR-X2700H",
        "http://192.168.1.1/desc.xml",
        "ssdp",
        model="AVR-X2700H",
        brand="Denon",
        extra={"usn": "uuid:abc::urn:denon:device:1", "max_age": 1800},
    )
    d = avr.as_dict()
    assert d["model"] == "AVR-X2700H"
    assert d["brand"] == "Denon"
    assert d["extra"]["usn"] == "uuid:abc::urn:denon:device:1"
    assert d["extra"]["max_age"] == 1800


# --- discover_via_ssdp ---


@pytest.mark.asyncio
async def test_discover_via_ssdp_returns_devices_from_ssdp_responses():
    """discover_via_ssdp parses SSDP responses and returns DiscoveredAVR list."""

    async def mock_create_datagram_endpoint(protocol_factory, *, local_addr=None):
        protocol = protocol_factory()
        transport = MagicMock()
        # Inject a Denon SSDP response so the discovery loop processes it
        fake = (
            b"HTTP/1.1 200 OK\r\n"
            b"SERVER: Linux/1.0 UPnP/1.0 Denon/1.0\r\n"
            b"LOCATION: http://192.168.1.100:80/description.xml\r\n"
            b"USN: uuid:abc::urn:schemas-denon-com:device:AiosDevice:1\r\n"
            b"CACHE-CONTROL: max-age=1800\r\n\r\n"
        )
        protocol.datagram_received(fake, ("239.255.255.250", 1900))
        return (transport, protocol)

    with patch("denon_proxy.avr.discover.asyncio.get_running_loop") as get_loop_mock:
        mock_loop = MagicMock()
        mock_loop.create_datagram_endpoint = mock_create_datagram_endpoint
        mock_loop.time.side_effect = [0, 0, 0, 10]  # deadline, while checks; 10 > deadline to exit
        get_loop_mock.return_value = mock_loop

        results = await discover_via_ssdp(timeout=0.5)

    assert len(results) == 1
    assert results[0].host == "192.168.1.100"
    assert results[0].port == 80
    assert results[0].method == "ssdp"
    assert results[0].matched is True
    assert results[0].brand == "Denon"
    assert results[0].extra is not None
    assert "usn" in results[0].extra
    assert results[0].extra.get("max_age") == 1800


# --- discover(method="both") merge ---


@pytest.mark.asyncio
async def test_discover_both_merges_and_deduplicates():
    """When method is 'both', SSDP and mDNS results are merged and deduplicated by (host, port)."""
    with (
        patch("denon_proxy.avr.discover.discover_via_ssdp", new_callable=AsyncMock) as ssdp_mock,
        patch("denon_proxy.avr.discover.discover_via_mdns", new_callable=AsyncMock) as mdns_mock,
    ):
        ssdp_mock.return_value = [
            DiscoveredAVR("192.168.1.100", 80, None, None, "ssdp"),
        ]
        mdns_mock.return_value = [
            DiscoveredAVR("192.168.1.100", 80, "Denon AVR", None, "mdns"),
            DiscoveredAVR("192.168.1.101", 80, None, None, "mdns"),
        ]
        results = await discover(method="both", timeout=1.0)
    assert len(results) == 2  # 192.168.1.100 once, 192.168.1.101 once
    hosts = {(r.host, r.port) for r in results}
    assert hosts == {("192.168.1.100", 80), ("192.168.1.101", 80)}


def test_discover_invalid_method_raises():
    with pytest.raises(ValueError, match="method must be"):
        asyncio.run(discover(method="invalid", timeout=1.0))


@pytest.mark.asyncio
async def test_discover_ssdp_delegates_to_discover_via_ssdp():
    """discover(method='ssdp') calls discover_via_ssdp and returns its results."""
    avr = DiscoveredAVR("192.168.1.1", 80, "Denon AVR", None, "ssdp")
    with patch(
        "denon_proxy.avr.discover.discover_via_ssdp",
        new_callable=AsyncMock,
        return_value=[avr],
    ) as ssdp_mock:
        results = await discover(method="ssdp", timeout=2.0)
    assert results == [avr]
    ssdp_mock.assert_called_once_with(timeout=2.0)


@pytest.mark.asyncio
async def test_discover_mdns_delegates_to_discover_via_mdns():
    """discover(method='mdns') calls discover_via_mdns and returns its results."""
    avr = DiscoveredAVR("192.168.1.1", 80, "Denon AVR", None, "mdns")
    with patch(
        "denon_proxy.avr.discover.discover_via_mdns",
        new_callable=AsyncMock,
        return_value=[avr],
    ) as mdns_mock:
        results = await discover(method="mdns", timeout=2.0)
    assert results == [avr]
    mdns_mock.assert_called_once_with(timeout=2.0)


@pytest.mark.asyncio
async def test_discover_progress_callback_invoked_while_searching():
    """When progress_callback is provided, it is called while discovery runs."""
    calls = []

    async def slow_ssdp(*, timeout=None):
        await asyncio.sleep(0.6)  # Ensure progress runs at least once
        return [DiscoveredAVR("192.168.1.1", 80, None, None, "ssdp")]

    with patch(
        "denon_proxy.avr.discover.discover_via_ssdp",
        side_effect=slow_ssdp,
    ):
        await discover(method="ssdp", timeout=2.0, progress_callback=lambda: calls.append(1))
    assert len(calls) >= 1


@pytest.mark.asyncio
async def test_discover_via_mdns_returns_empty_when_zeroconf_unavailable():
    """discover_via_mdns returns [] when zeroconf is not available."""
    with patch("denon_proxy.avr.discover.mdns_available", return_value=False):
        results = await discover_via_mdns(timeout=1.0)
    assert results == []


# --- mdns_available ---


def test_mdns_available_returns_bool():
    """mdns_available returns True or False depending on zeroconf import."""
    assert isinstance(mdns_available(), bool)


# --- discover_via_mdns (mocked zeroconf) ---


@pytest.mark.asyncio
async def test_discover_via_mdns_finds_denon_service_when_zeroconf_reports_one():
    """When zeroconf reports a _http._tcp service whose name contains 'denon', discover_via_mdns returns it."""
    from zeroconf import ServiceStateChange

    mock_info = MagicMock()
    mock_info.parsed_addresses.return_value = ["192.168.1.50"]
    mock_info.port = 80

    mock_zc = MagicMock()
    mock_zc.get_service_info.return_value = mock_info

    def capture_browser(zc, service_type, handlers=None, **kwargs):
        for h in handlers or []:
            h(
                mock_zc,
                "_http._tcp.local.",
                "Denon Test AVR._http._tcp.local.",
                ServiceStateChange.Added,
            )

    with (
        patch("zeroconf.Zeroconf", return_value=mock_zc),
        patch("zeroconf.ServiceBrowser", side_effect=capture_browser),
        patch("time.sleep"),
    ):
        results = await discover_via_mdns(timeout=5.0)

    assert len(results) == 1
    assert results[0].host == "192.168.1.50"
    assert results[0].port == 80
    assert results[0].name == "Denon Test AVR._http._tcp.local."
    assert results[0].method == "mdns"


async def _run_mdns_with_mock_info(mock_info):
    """Run discover_via_mdns with Zeroconf/ServiceBrowser patched so get_service_info returns mock_info."""
    from zeroconf import ServiceStateChange

    mock_zc = MagicMock()
    mock_zc.get_service_info.return_value = mock_info

    def capture_browser(zc, service_type, handlers=None, **kwargs):
        for h in handlers or []:
            h(mock_zc, "_http._tcp.local.", "Denon AVR._http._tcp.local.", ServiceStateChange.Added)

    with (
        patch("zeroconf.Zeroconf", return_value=mock_zc),
        patch("zeroconf.ServiceBrowser", side_effect=capture_browser),
        patch("time.sleep"),
    ):
        return await discover_via_mdns(timeout=0.0)


@pytest.mark.asyncio
async def test_discover_via_mdns_properties_from_decoded_properties_callable():
    """When info has callable decoded_properties, its return value is used for extra['properties']."""
    mock_info = MagicMock()
    mock_info.parsed_addresses.return_value = ["192.168.1.50"]
    mock_info.port = 80
    mock_info.decoded_properties = lambda: {"path": "/api", "version": "1.0"}
    mock_info.properties = {"_": "_"}  # must be truthy to enter properties block; decoded_properties is then used

    results = await _run_mdns_with_mock_info(mock_info)

    assert len(results) == 1
    assert results[0].extra is not None
    assert results[0].extra["properties"] == {"path": "/api", "version": "1.0"}


@pytest.mark.asyncio
async def test_discover_via_mdns_properties_from_raw_properties_dict():
    """When info has no decoded_properties, properties dict is decoded (bytes -> str) into extra['properties']."""
    mock_info = MagicMock()
    mock_info.parsed_addresses.return_value = ["192.168.1.50"]
    mock_info.port = 80
    mock_info.decoded_properties = None
    mock_info.properties = {"path": b"/api", "model": "already-str"}

    results = await _run_mdns_with_mock_info(mock_info)

    assert len(results) == 1
    assert results[0].extra is not None
    assert results[0].extra["properties"] == {"path": "/api", "model": "already-str"}


@pytest.mark.asyncio
async def test_discover_via_mdns_properties_decoding_errors_tolerated():
    """
    UnicodeDecodeError, AttributeError, and TypeError during properties decoding are caught;
    device still discovered without properties.
    """
    for exc_cls in (UnicodeDecodeError, AttributeError, TypeError):
        mock_info = MagicMock()
        mock_info.parsed_addresses.return_value = ["192.168.1.50"]
        mock_info.port = 80
        if exc_cls is UnicodeDecodeError:

            def raise_unicode():
                raise UnicodeDecodeError("utf-8", b"x", 0, 1, "invalid")

            mock_info.decoded_properties = raise_unicode
            mock_info.properties = {"_": "_"}  # truthy so we enter block and call decoded_properties()
        elif exc_cls is AttributeError:
            mock_info.decoded_properties = None
            mock_info.properties = MagicMock()
            mock_info.properties.items.side_effect = AttributeError("no items")
        else:
            mock_info.decoded_properties = None
            mock_info.properties = MagicMock()
            mock_info.properties.items.side_effect = TypeError("not iterable")

        results = await _run_mdns_with_mock_info(mock_info)

        assert len(results) == 1, f"Expected one device when {exc_cls.__name__} is raised"
        assert results[0].host == "192.168.1.50"
        assert results[0].extra is None or "properties" not in (results[0].extra or {})


@pytest.mark.asyncio
async def test_discover_via_mdns_properties_other_exception_propagates():
    """Exceptions other than UnicodeDecodeError/AttributeError/TypeError during properties decoding propagate."""

    def raise_keyerror():
        raise KeyError("missing")

    mock_info = MagicMock()
    mock_info.parsed_addresses.return_value = ["192.168.1.50"]
    mock_info.port = 80
    mock_info.decoded_properties = raise_keyerror
    mock_info.properties = {"_": "_"}  # must be truthy to enter properties block so decoded_properties() is called

    with pytest.raises(KeyError, match="missing"):
        await _run_mdns_with_mock_info(mock_info)
