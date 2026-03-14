"""
Discover Denon/Marantz AVRs on the local network via SSDP or mDNS.

Use this for the `denon-proxy discover` CLI: find physical AVRs so you can
set avr_host in config (or pass to run).

TODO (short prompts, descending importance):
- Lint, format, type-check, test, and review this module.
- Add verbosity levels: -v = basic, -vv = current verbose.
- Show progress (e.g. dots) while searching.
- Add interactive mode: pick device and write config.
- Remove or repurpose scripts dir if unused.

"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from denon_proxy.constants import SSDP_MCAST_GRP, SSDP_MCAST_PORT

_logger = logging.getLogger(__name__)

# Search targets that Denon/Marantz AVRs respond to (we send M-SEARCH with these).
# Real AVRs vary: some use AiosDevice, some only rootdevice/MediaRenderer, some ACT-Denon.
DENON_SSDP_ST = "urn:schemas-denon-com:device:AiosDevice:1"
SSDP_ALL = "ssdp:all"
# Extra STs some Denon/Marantz models respond to (discover sends all of these)
SSDP_EXTRA_ST = (
    "upnp:rootdevice",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-denon-com:device:ACT-Denon:1",
)

# Default discovery timeout (seconds)
DISCOVER_TIMEOUT = 5.0

# SERVER/USN substrings that indicate a Denon/Marantz AVR (case-insensitive).
# Sources: HA denonavr manifest (Denon, DENON, DENON PROFESSIONAL, Marantz), device
# reports (KnOS/3.2 DMP/3.5), and URNs (urn:schemas-denon-com:device:AiosDevice:1).
# aios = Denon/Marantz Aios platform / AiosDevice.
SSDP_VENDOR_MARKERS = ("denon", "marantz", "heos", "knos", "dmp", "aios")

# Patterns to extract brand and model from friendly name or SERVER string.
# Denon: AVR-X*, AVR-S*, AVR-X*H, etc. Marantz: SR*, NR*, etc.
_RE_BRAND_MODEL = re.compile(
    r"(?:(Denon|Marantz)\s+)?"
    r"(AVR-[A-Z0-9-]+|AVR-[A-Z0-9]+H|SR[0-9]+|NR[0-9]+|DNP-[A-Z0-9]+|PMA-[A-Z0-9-]+)?",
    re.IGNORECASE,
)
# Content in parentheses often holds full "Denon AVR-X2700H" when outer is "Living Room (...)"
_RE_PAREN_CONTENT = re.compile(r"\(([^)]+)\)")


def _parse_friendly_name(raw: str | None) -> tuple[str | None, str | None]:
    """Parse friendly name or SERVER string into (brand, model). Best-effort."""
    if not raw or not raw.strip():
        return (None, None)
    text = raw.strip()
    # Prefer content in parentheses (e.g. "Living Room (Denon AVR-X2700H)")
    paren = _RE_PAREN_CONTENT.search(text)
    if paren:
        text = paren.group(1).strip()
    # Strip mDNS suffix for display parsing
    if "._http._tcp.local." in text.lower():
        text = text.split("._http._tcp.local.")[0].strip()
    brand, model = None, None
    match = _RE_BRAND_MODEL.search(text)
    if match:
        brand_grp, model_grp = match.group(1), match.group(2)
        if brand_grp:
            brand = brand_grp.strip()
        if model_grp:
            model = model_grp.strip()
    # If we only see "Denon" or "Marantz" (e.g. SERVER: Linux/1.0 UPnP/1.0 Denon/1.0)
    if not brand and not model:
        lower = text.lower()
        if "denon" in lower:
            brand = "Denon"
        elif "marantz" in lower:
            brand = "Marantz"
    return (brand, model)


def _parse_ssdp_extra_headers(data: bytes) -> dict[str, Any]:
    """Extract extra SSDP headers (USN, CACHE-CONTROL) for display/debug."""
    text = data.decode("utf-8", errors="replace")
    extra: dict[str, Any] = {}
    usn_match = re.search(r"USN:\s*([^\r\n]+)", text, re.IGNORECASE)
    if usn_match:
        extra["usn"] = usn_match.group(1).strip()
    cache_match = re.search(r"CACHE-CONTROL:\s*max-age\s*=\s*(\d+)", text, re.IGNORECASE)
    if cache_match:
        try:
            extra["max_age"] = int(cache_match.group(1))
        except ValueError:
            pass
    return extra


@dataclass(frozen=True)
class DiscoveredAVR:
    """A single discovered AVR (host, port, optional friendly name, LOCATION, and parsed info)."""

    host: str
    port: int
    name: str | None
    location: str | None
    method: str  # "ssdp" or "mdns"
    matched: bool = True  # False when discovered but filtered out (non-Denon/Marantz)
    model: str | None = None  # Parsed from friendly name (e.g. AVR-X2700H)
    brand: str | None = None  # Parsed from friendly name (Denon or Marantz)
    extra: dict[str, Any] | None = None  # USN, max_age, mdns server/properties, etc.

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "name": self.name,
            "location": self.location,
            "method": self.method,
            "matched": self.matched,
        }
        if self.model is not None:
            out["model"] = self.model
        if self.brand is not None:
            out["brand"] = self.brand
        if self.extra:
            out["extra"] = self.extra
        return out


def _parse_ssdp_location(data: bytes) -> tuple[str, int] | None:
    """Parse SSDP response and return (host, port) from LOCATION header, or None."""
    text = data.decode("utf-8", errors="replace")
    if "HTTP/1.1 200" not in text and "200 OK" not in text:
        return None
    match = re.search(r"LOCATION:\s*(\S+)", text, re.IGNORECASE)
    if not match:
        return None
    url_str = match.group(1).strip()
    try:
        parsed = urlparse(url_str)
        host = parsed.hostname or ""
        port = parsed.port or (80 if parsed.scheme == "http" else 443)
        if host:
            return (host, port)
    except (ValueError, AttributeError):
        pass
    return None


def _parse_ssdp_server_or_usn(data: bytes) -> str | None:
    """Extract friendly name from SERVER or USN in SSDP response (best-effort)."""
    text = data.decode("utf-8", errors="replace")
    # SERVER: often "Linux/1.0 UPnP/1.0 Denon/1.0" or similar
    match = re.search(r"SERVER:\s*([^\r\n]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip() or None
    # Fallback: USN (e.g. uuid:...::urn:schemas-denon-com:device:AiosDevice:1) for display/parsing
    match = re.search(r"USN:\s*([^\r\n]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip() or None
    return None


def _is_denon_ssdp_response(data: bytes) -> bool:
    """Return True if the SSDP response looks like a Denon/Marantz AVR (SERVER or USN)."""
    server_or_usn = _parse_ssdp_server_or_usn(data)
    if not server_or_usn:
        text = data.decode("utf-8", errors="replace")
        match = re.search(r"USN:\s*([^\r\n]+)", text, re.IGNORECASE)
        server_or_usn = match.group(1).strip() if match else ""
    s = (server_or_usn or "").lower()
    return any(m in s for m in SSDP_VENDOR_MARKERS)


async def _send_msearch(transport: asyncio.DatagramTransport, st: str) -> None:
    """Send one M-SEARCH packet for the given search target."""
    msearch = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_MCAST_GRP}:{SSDP_MCAST_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"ST: {st}\r\n"
        "MX: 2\r\n"
        "\r\n"
    ).encode()
    transport.sendto(msearch, (SSDP_MCAST_GRP, SSDP_MCAST_PORT))
    _logger.debug("SSDP M-SEARCH sent (ST=%s)", st)


async def discover_via_ssdp(
    timeout: float = DISCOVER_TIMEOUT,
    include_filtered: bool = False,
) -> list[DiscoveredAVR]:
    """
    Discover Denon/Marantz AVRs on the LAN using SSDP (UPnP M-SEARCH).

    Sends M-SEARCH for multiple search targets (AiosDevice, ssdp:all, rootdevice,
    MediaRenderer, ACT-Denon) since real AVRs vary by model. Collects HTTP 200
    responses and returns unique devices by (host, port). Filters by SERVER/USN
    using SSDP_VENDOR_MARKERS (denon, marantz, heos, knos, dmp, aios) so that AVRs that
    advertise as e.g. "KnOS/3.2 UPnP/1.0 DMP/3.5" are still included. Does not require root
    if binding to a random port (we only send to multicast and receive unicast
    replies). If the real AVR still does not appear, try --method mdns or both.
    """
    results: dict[tuple[str, int], DiscoveredAVR] = {}
    received: asyncio.Queue[bytes] = asyncio.Queue()

    class OneShotProtocol(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, _addr: tuple[str, int]) -> None:
            received.put_nowait(data)

    loop = asyncio.get_running_loop()
    transport, _protocol = await loop.create_datagram_endpoint(
        lambda: OneShotProtocol(),
        local_addr=("0.0.0.0", 0),
    )
    _logger.debug("SSDP discovery starting (timeout=%.1fs)", timeout)
    try:
        # Send M-SEARCH for Denon-specific and generic targets (real AVRs vary by model)
        for st in (DENON_SSDP_ST, SSDP_ALL) + SSDP_EXTRA_ST:
            await _send_msearch(transport, st)
            await asyncio.sleep(0.05)

        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                remaining = max(0.1, deadline - loop.time())
                data = await asyncio.wait_for(received.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            loc = _parse_ssdp_location(data)
            if loc:
                host, port = loc
                is_denon = _is_denon_ssdp_response(data)
                if not is_denon and not include_filtered:
                    _logger.debug(
                        "SSDP response %s:%d skipped (SERVER/USN has no known AVR marker)",
                        host,
                        port,
                    )
                    continue
                key = (host, port)
                if key not in results:
                    name = _parse_ssdp_server_or_usn(data)
                    location_url = None
                    for line in data.decode("utf-8", errors="replace").split("\r\n"):
                        if line.upper().startswith("LOCATION:"):
                            location_url = line.split(":", 1)[1].strip()
                            break
                    brand, model = _parse_friendly_name(name)
                    extra = _parse_ssdp_extra_headers(data)
                    results[key] = DiscoveredAVR(
                        host=host,
                        port=port,
                        name=name,
                        location=location_url,
                        method="ssdp",
                        matched=is_denon,
                        model=model,
                        brand=brand,
                        extra=extra or None,
                    )
                    _logger.debug(
                        "SSDP response: %s:%d (name=%r)%s",
                        host,
                        port,
                        name,
                        "" if is_denon else " [filtered]",
                    )
                else:
                    _logger.debug("SSDP response: %s:%d (duplicate, skipped)", host, port)
            else:
                _logger.debug("SSDP datagram ignored (no 200 OK or LOCATION)")
    finally:
        transport.close()

    _logger.debug("SSDP discovery finished: %d device(s)", len(results))
    return list(results.values())


def mdns_available() -> bool:
    """Return True if the optional 'zeroconf' package is installed for mDNS discovery."""
    try:
        import zeroconf  # noqa: F401
        return True
    except ImportError:
        return False


def _run_mdns_sync(timeout: float, include_filtered: bool = False) -> list[DiscoveredAVR]:
    """Run synchronous zeroconf browse in executor. Used by discover_via_mdns."""
    import time
    from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf

    found: list[DiscoveredAVR] = []
    found_keys: set[tuple[str, int]] = set()

    def on_service_state_change(
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        if state_change != ServiceStateChange.Added:
            return
        info = zeroconf.get_service_info(service_type, name)
        if not info or not info.parsed_addresses():
            _logger.debug("mDNS service %r: no info or addresses", name)
            return
        host = info.parsed_addresses()[0]
        port = info.port or 80
        key = (host, port)
        if key in found_keys:
            _logger.debug("mDNS service %r: %s:%d (duplicate)", name, host, port)
            return
        name_lower = (name or "").lower()
        is_denon = any(m in name_lower for m in SSDP_VENDOR_MARKERS)
        if is_denon or include_filtered:
            found_keys.add(key)
            brand, model = _parse_friendly_name(name)
            extra: dict[str, Any] = {}
            if getattr(info, "server", None):
                extra["server"] = (info.server or "").strip() or None
            if getattr(info, "properties", None) and info.properties:
                try:
                    decoded = getattr(info, "decoded_properties", None)
                    if callable(decoded):
                        extra["properties"] = decoded()
                    else:
                        extra["properties"] = {
                            k: v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v
                            for k, v in info.properties.items()
                        }
                except Exception:  # noqa: BLE001
                    pass
            if not extra:
                extra = None
            found.append(
                DiscoveredAVR(
                    host=host,
                    port=port,
                    name=name,
                    location=None,
                    method="mdns",
                    matched=is_denon,
                    model=model,
                    brand=brand,
                    extra=extra,
                )
            )
            _logger.debug(
                "mDNS %s: %s:%d (%s)",
                "match" if is_denon else "filtered",
                host,
                port,
                name,
            )
        else:
            _logger.debug(
                "mDNS service %r (%s:%d) skipped (name has no known AVR marker)",
                name,
                host,
                port,
            )

    _logger.debug("mDNS discovery starting (timeout=%.1fs, browsing _http._tcp.local.)", timeout)
    zc = Zeroconf()
    try:
        ServiceBrowser(zc, "_http._tcp.local.", handlers=[on_service_state_change])
        time.sleep(min(timeout, 5.0))
    finally:
        zc.close()
    _logger.debug("mDNS discovery finished: %d device(s)", len(found))
    return found


async def discover_via_mdns(
    timeout: float = DISCOVER_TIMEOUT,
    include_filtered: bool = False,
) -> list[DiscoveredAVR]:
    """
    Discover Denon/Marantz AVRs via mDNS/Bonjour (zeroconf).

    Browses _http._tcp for service names containing any SSDP_VENDOR_MARKERS
    (denon, marantz, heos, knos, dmp, aios).
    Requires the optional dependency 'zeroconf'. Returns empty list if not installed.
    """
    if not mdns_available():
        return []
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run_mdns_sync, timeout, include_filtered)


async def discover(
    method: str = "ssdp",
    timeout: float = DISCOVER_TIMEOUT,
    include_filtered: bool = False,
) -> list[DiscoveredAVR]:
    """
    Discover AVRs using the given method: "ssdp", "mdns", or "both".

    "both" runs SSDP and mDNS in parallel and merges results (deduplicated by host:port).
    When include_filtered is True, devices that did not match Denon/Marantz markers are
    also returned, with matched=False.
    """
    _logger.debug("discover method=%s timeout=%.1f include_filtered=%s", method, timeout, include_filtered)
    if method == "ssdp":
        return await discover_via_ssdp(timeout=timeout, include_filtered=include_filtered)
    if method == "mdns":
        return await discover_via_mdns(timeout=timeout, include_filtered=include_filtered)
    if method == "both":
        ssdp_task = asyncio.create_task(
            discover_via_ssdp(timeout=timeout, include_filtered=include_filtered)
        )
        mdns_task = asyncio.create_task(
            discover_via_mdns(timeout=timeout, include_filtered=include_filtered)
        )
        ssdp_results, mdns_results = await asyncio.gather(ssdp_task, mdns_task)
        _logger.debug("both: SSDP %d, mDNS %d", len(ssdp_results), len(mdns_results))
        seen: set[tuple[str, int]] = set()
        merged: list[DiscoveredAVR] = []
        for avr in ssdp_results + mdns_results:
            key = (avr.host, avr.port)
            if key not in seen:
                seen.add(key)
                merged.append(avr)
        _logger.debug("both: merged %d unique device(s)", len(merged))
        return merged
    raise ValueError(f"method must be 'ssdp', 'mdns', or 'both'; got {method!r}")


__all__ = [
    "DiscoveredAVR",
    "discover",
    "discover_via_ssdp",
    "discover_via_mdns",
    "mdns_available",
    "DISCOVER_TIMEOUT",
]
