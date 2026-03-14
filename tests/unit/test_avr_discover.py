"""Unit tests for denon_proxy.avr.discover (AVR discovery via SSDP/mDNS)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from denon_proxy.avr.discover import (
    DISCOVER_TIMEOUT,
    DiscoveredAVR,
    discover,
    discover_via_mdns,
    discover_via_ssdp,
    mdns_available,
)
from denon_proxy.avr.discover import _is_denon_ssdp_response as is_denon_ssdp_response
from denon_proxy.avr.discover import _parse_friendly_name as parse_friendly_name
from denon_proxy.avr.discover import _parse_ssdp_location as parse_ssdp_location
from denon_proxy.avr.discover import _parse_ssdp_server_or_usn as parse_ssdp_server


# --- _parse_ssdp_location ---


def test_parse_ssdp_location_returns_host_port():
    msg = (
        "HTTP/1.1 200 OK\r\n"
        "LOCATION: http://192.168.1.50:80/description.xml\r\n"
        "CACHE-CONTROL: max-age=1800\r\n\r\n"
    )
    assert parse_ssdp_location(msg.encode()) == ("192.168.1.50", 80)


def test_parse_ssdp_location_default_port_80():
    msg = "HTTP/1.1 200 OK\r\nLOCATION: http://10.0.0.1/foo.xml\r\n\r\n"
    assert parse_ssdp_location(msg.encode()) == ("10.0.0.1", 80)


def test_parse_ssdp_location_non_200_returns_none():
    msg = "HTTP/1.1 404 Not Found\r\n\r\n"
    assert parse_ssdp_location(msg.encode()) is None


def test_parse_ssdp_location_no_location_returns_none():
    msg = "HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\n\r\n"
    assert parse_ssdp_location(msg.encode()) is None


def test_parse_ssdp_location_case_insensitive():
    msg = "HTTP/1.1 200 OK\r\nlocation: http://127.0.0.1:8080/desc.xml\r\n\r\n"
    assert parse_ssdp_location(msg.encode()) == ("127.0.0.1", 8080)


# --- _parse_ssdp_server_or_usn ---


def test_parse_ssdp_server_extracts_server_header():
    msg = "HTTP/1.1 200 OK\r\nSERVER: Linux/1.0 UPnP/1.0 Denon/1.0\r\n\r\n"
    assert parse_ssdp_server(msg.encode()) == "Linux/1.0 UPnP/1.0 Denon/1.0"


def test_parse_ssdp_server_missing_returns_none():
    msg = "HTTP/1.1 200 OK\r\nLOCATION: http://a/b\r\n\r\n"
    assert parse_ssdp_server(msg.encode()) is None


def test_parse_ssdp_server_fallback_to_usn_when_no_server():
    msg = (
        "HTTP/1.1 200 OK\r\n"
        "USN: uuid:abc::urn:schemas-denon-com:device:AiosDevice:1\r\n"
        "LOCATION: http://192.168.1.50:80/description.xml\r\n\r\n"
    )
    assert "urn:schemas-denon-com" in (parse_ssdp_server(msg.encode()) or "")


# --- _is_denon_ssdp_response ---


def test_is_denon_ssdp_response_accepts_denon_server():
    msg = "HTTP/1.1 200 OK\r\nSERVER: Linux/1.0 UPnP/1.0 Denon/1.0\r\nLOCATION: http://10.0.0.1/\r\n\r\n"
    assert is_denon_ssdp_response(msg.encode()) is True


def test_is_denon_ssdp_response_accepts_marantz_server():
    msg = "HTTP/1.1 200 OK\r\nSERVER: Marantz/1.0 UPnP/1.0\r\nLOCATION: http://10.0.0.1/\r\n\r\n"
    assert is_denon_ssdp_response(msg.encode()) is True


def test_is_denon_ssdp_response_accepts_knos_dmp_avr():
    """Some Denon AVRs advertise as KnOS/3.2 UPnP/1.0 DMP/3.5 (no 'Denon' in SERVER)."""
    msg = (
        "HTTP/1.1 200 OK\r\n"
        "SERVER: KnOS/3.2 UPnP/1.0 DMP/3.5\r\n"
        "LOCATION: http://10.0.2.5:8080/description.xml\r\n\r\n"
    )
    assert is_denon_ssdp_response(msg.encode()) is True


def test_is_denon_ssdp_response_accepts_denon_in_usn_when_no_server():
    msg = (
        "HTTP/1.1 200 OK\r\n"
        "USN: uuid:abc::urn:schemas-denon-com:device:AiosDevice:1\r\n"
        "LOCATION: http://10.0.0.1/\r\n\r\n"
    )
    assert is_denon_ssdp_response(msg.encode()) is True


def test_is_denon_ssdp_response_accepts_aios_in_usn():
    """USN often contains AiosDevice (Denon Aios platform); aios is a marker."""
    msg = (
        "HTTP/1.1 200 OK\r\n"
        "USN: uuid:xyz::urn:example:device:AiosDevice:1\r\n"
        "LOCATION: http://192.168.1.10:8080/\r\n\r\n"
    )
    assert is_denon_ssdp_response(msg.encode()) is True


def test_is_denon_ssdp_response_rejects_unknown_vendor():
    msg = "HTTP/1.1 200 OK\r\nSERVER: SomeOther/1.0 UPnP/1.0\r\nLOCATION: http://10.0.0.1/\r\n\r\n"
    assert is_denon_ssdp_response(msg.encode()) is False


def test_is_denon_ssdp_response_rejects_when_no_server_or_usn():
    msg = "HTTP/1.1 200 OK\r\nLOCATION: http://10.0.0.1/\r\n\r\n"
    assert is_denon_ssdp_response(msg.encode()) is False


# --- _parse_friendly_name ---


def test_parse_friendly_name_denon_avr_model():
    assert parse_friendly_name("Denon AVR-X2700H") == ("Denon", "AVR-X2700H")
    assert parse_friendly_name("Denon AVR-X2700H._http._tcp.local.") == ("Denon", "AVR-X2700H")


def test_parse_friendly_name_marantz_model():
    assert parse_friendly_name("Marantz SR5015") == ("Marantz", "SR5015")


def test_parse_friendly_name_parentheses():
    assert parse_friendly_name("Living Room (Denon AVR-X2700H)") == ("Denon", "AVR-X2700H")


def test_parse_friendly_name_server_string():
    assert parse_friendly_name("Linux/1.0 UPnP/1.0 Denon/1.0") == ("Denon", None)


def test_parse_friendly_name_empty_or_none():
    assert parse_friendly_name(None) == (None, None)
    assert parse_friendly_name("") == (None, None)


def test_parse_friendly_name_usn_returns_brand():
    """USN string (no SERVER) still yields brand from 'denon' in urn."""
    assert parse_friendly_name("uuid:xyz::urn:schemas-denon-com:device:AiosDevice:1") == (
        "Denon",
        None,
    )


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
