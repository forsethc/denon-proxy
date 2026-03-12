"""
AVR discovery - SSDP and HTTP device description so the proxy is discoverable as a Denon AVR.

Serves device discovery (SSDP), device description XML, Deviceinfo, AppCommand,
and MainZone status endpoints. Compatible with Home Assistant's denonavr
integration and UC Remote 3.

Can be used standalone (discovery + HTTP API for testing) or wired to a proxy.

Usage (standalone):
    from denon_proxy.runtime.state import RuntimeState
    from denon_proxy.avr.state import AVRState
    from denon_proxy.avr.discovery import run_discovery_servers

    async def main():
        avr_state = AVRState()
        avr_state.power = "ON"
        avr_state.volume = "50"
        runtime_state = RuntimeState()
        await run_discovery_servers(config, logger, avr_state, runtime_state)

Usage (with denon-proxy):
    from denon_proxy.avr.connection import create_avr_connection
    from denon_proxy.avr.discovery import run_discovery_servers
    avr = create_avr_connection(config, avr_state, on_response, on_disconnect, logger)
    # Returns AVRConnection (physical) or VirtualAVRConnection (no avr_host) - opaque to caller
"""

from __future__ import annotations

import asyncio
import errno
import logging
import re
import socket
import struct
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, cast

import httpx

from denon_proxy.avr.info import AVRInfo
from denon_proxy.avr.state import AVRState, volume_to_db
from denon_proxy.constants import (
    AVR_NETWORK_TIMEOUT,
    DEFAULT_SSDP_HTTP_PORT,
    DEMO_SOURCES,
    DENON_AIOS_HTTP_PORT,
    DISCOVERY_HTTP_PORT,
    SOCKET_TIMEOUT,
    SSDP_MCAST_GRP,
    SSDP_MCAST_PORT,
)
from denon_proxy.utils.utils import is_docker_internal_ip

if TYPE_CHECKING:
    from denon_proxy.runtime.config import Config
    from denon_proxy.runtime.state import RuntimeState

__all__ = ["get_advertise_ip", "run_discovery_servers"]

_logger = logging.getLogger(__name__)


def _get_sources(config: Config, runtime_state: RuntimeState) -> list[tuple[str, str]]:
    """
    Return list of (func_name, display_name) for input sources.
    func_name is the Denon code (e.g. CD, BD, HDMI1) used in SI commands.
    display_name is shown in Home Assistant.
    Uses config['sources'] if provided (dict of func_code -> display_name).
    If no config mapping and runtime_state.avr_info.raw_sources exist, uses those.
    Otherwise DEMO_SOURCES. Cache is read/written on runtime_state.
    """
    if runtime_state.resolved_sources is not None:
        return runtime_state.resolved_sources

    cfg = config.get("sources")
    avr_info = runtime_state.avr_info
    raw_sources = avr_info.raw_sources if avr_info is not None else None

    if cfg:
        out: list[tuple[str, str]] = []
        for func_name, display_name in cfg.items():
            func_str = str(func_name).strip()
            display_str = str(display_name).strip() if display_name else func_str
            if func_str:
                out.append((func_str, display_str))
        # Filter out sources that don't exist on the AVR
        if raw_sources:
            valid_funcs = {str(func).strip() for func, _ in raw_sources if func}
            filtered: list[tuple[str, str]] = []
            for func_name, display_name in out:
                if func_name in valid_funcs:
                    filtered.append((func_name, display_name))
                else:
                    _logger.warning(
                        "Input source '%s' (display_name: '%s') not found on AVR, skipping",
                        func_name, display_name,
                    )
            out = filtered
        result = out if out else DEMO_SOURCES
    else:
        # No user mapping: prefer raw sources (from physical AVR) over defaults
        if raw_sources:
            result = [
                (
                    str(func).strip(),
                    str(display_name).strip() if display_name else str(func).strip(),
                )
                for func, display_name in raw_sources
                if func
            ]
        else:
            result = DEMO_SOURCES

    runtime_state.resolved_sources = result
    if raw_sources:
        _logger.info(
            "Raw sources from AVR:\n  %s",
            "\n  ".join(
                f"{func} -> {display_name}"
                for func, display_name in raw_sources
            ),
        )
    _logger.info(
        "Resolved input sources:\n  %s",
        "\n  ".join(
            f"{func} -> {display_name}" for func, display_name in result
        ),
    )
    return result


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def get_advertise_ip(config: Config) -> str | None:
    """Get the IP to advertise in SSDP LOCATION."""
    ip = str(config.get("ssdp_advertise_ip", "")).strip()
    if ip:
        return ip
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(SOCKET_TIMEOUT)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            return str(ip)
    except OSError:
        return None


# -----------------------------------------------------------------------------
# XML builders
# -----------------------------------------------------------------------------

def _get_proxy_friendly_name(config: Config, runtime_state: RuntimeState) -> str:
    """Proxy's advertised friendly name: config if set, else physical device name + ' Proxy'. Cached on RuntimeState."""
    return runtime_state.get_friendly_name(config)


def _deviceinfo_xml(config: Config, runtime_state: RuntimeState) -> str:
    """Deviceinfo.xml - identify as pre-2016 AVR so denonavr uses port 8080/description.xml
    (avoids port 60006 aios_device.xml which can cause HA config flow issues)."""
    sources_xml = "\n".join(
        f'      <Source><FuncName>{func_name}</FuncName><DefaultName>{display_name}</DefaultName></Source>'
        for func_name, display_name in _get_sources(config, runtime_state)
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Device_Info>
  <ModelName>AVR-3808</ModelName>
  <CategoryName>AV RECEIVER</CategoryName>
  <CommApiVers>0300</CommApiVers>
  <DeviceZones>1</DeviceZones>
  <DeviceZoneCapabilities>
    <Zone><No>0</No></Zone>
    <InputSource>
      <List>
{sources_xml}
      </List>
    </InputSource>
  </DeviceZoneCapabilities>
</Device_Info>"""


def _appcommand_friendlyname_xml(config: Config, runtime_state: RuntimeState) -> str:
    """AppCommand.xml response for GetFriendlyName (denonavr setup)."""
    name = _get_proxy_friendly_name(config, runtime_state)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rx>
  <cmd id="1">
    <friendlyname>{name}</friendlyname>
  </cmd>
</rx>"""


def _parse_appcommand_request(body_bytes: bytes) -> list[tuple[str, str]]:
    """
    Parse AppCommand request body. denonavr sends multiple <tx> chunks;
    ET.fromstring fails on multiple roots. Extract each tx and parse.
    """
    cmds_requested = []
    body_str = body_bytes.decode("utf-8", errors="ignore")
    start = 0
    while True:
        tx_start = body_str.find("<tx>", start)
        if tx_start == -1:
            tx_start = body_str.find("<tx ", start)
        if tx_start == -1:
            break
        tx_end = body_str.find("</tx>", tx_start)
        if tx_end == -1:
            break
        chunk = body_str[tx_start : tx_end + 5].encode("utf-8")
        try:
            root = ET.fromstring(chunk)
            for cmd in root.findall("cmd"):
                text = (cmd.text or "").strip()
                if text:
                    cmds_requested.append((cmd.get("id", "1"), text))
        except ET.ParseError:
            pass
        start = tx_end + 5
    return cmds_requested


def _appcommand_response_xml(
    config: Config,
    avr_state: AVRState,
    body_bytes: bytes,
    logger: logging.Logger,
    runtime_state: RuntimeState,
) -> bytes:
    """
    Build AppCommand.xml response from request body.
    avr_state should have: power, volume, mute, input_source (all optional).
    """
    power = (getattr(avr_state, "power", None) if avr_state else None) or "ON"
    vol_raw = (getattr(avr_state, "volume", None) if avr_state else None) or "50"
    volume = volume_to_db(vol_raw)
    mute_val = "on" if (avr_state and getattr(avr_state, "mute", None)) else "off"
    input_src = (getattr(avr_state, "input_source", None) if avr_state else None) or "CD"
    friendly_name = _get_proxy_friendly_name(config, runtime_state)
    sound_mode = (getattr(avr_state, "sound_mode", None) if avr_state else None) or "STEREO"

    cmds_requested = _parse_appcommand_request(body_bytes)
    logger.debug("AppCommand requested: %s", [cmd_text for _, cmd_text in cmds_requested])
    if not cmds_requested:
        cmds_requested = [("1", "GetFriendlyName")]

    cmd_responses = []
    for cmd_id, cmd_text in cmds_requested:
        command_text = cmd_text or ""
        if command_text == "GetFriendlyName":
            cmd_responses.append(
                f'  <cmd id="{cmd_id}" cmd_text="GetFriendlyName">'
                f"<friendlyname>{friendly_name}</friendlyname></cmd>"
            )
        elif command_text == "GetAllZonePowerStatus":
            # Both zone1 (AppCommand) and list format (some clients expect zone names)
            cmd_responses.append(
                f'  <cmd id="{cmd_id}" cmd_text="GetAllZonePowerStatus">'
                f"<zone1>{power}</zone1>"
                f'<list><listvalue><zone>Main</zone><value>{power}</value></listvalue></list>'
                f"</cmd>"
            )
        elif command_text == "GetAllZoneVolume":
            cmd_responses.append(
                f'  <cmd id="{cmd_id}" cmd_text="GetAllZoneVolume">'
                f"<zone1><volume>{volume}</volume></zone1></cmd>"
            )
        elif command_text == "GetAllZoneMuteStatus":
            cmd_responses.append(
                f'  <cmd id="{cmd_id}" cmd_text="GetAllZoneMuteStatus">'
                f"<zone1>{mute_val}</zone1></cmd>"
            )
        elif command_text == "GetAllZoneSource":
            cmd_responses.append(
                f'  <cmd id="{cmd_id}" cmd_text="GetAllZoneSource">'
                f"<zone1><source>{input_src}</source></zone1></cmd>"
            )
        elif command_text == "GetSurroundModeStatus":
            cmd_responses.append(
                f'  <cmd id="{cmd_id}" cmd_text="GetSurroundModeStatus">'
                f"<surround>{sound_mode}</surround></cmd>"
            )
        elif command_text == "GetAutoStandby":
            cmd_responses.append(
                f'  <cmd id="{cmd_id}" cmd_text="GetAutoStandby">'
                '<list><listvalue><zone>Main</zone><value>OFF</value>'
                "</listvalue></list></cmd>"
            )
        elif command_text == "GetDimmer":
            cmd_responses.append(
                f'  <cmd id="{cmd_id}" cmd_text="GetDimmer">'
                '<value>Bright</value></cmd>'
            )
        elif command_text == "GetECO":
            cmd_responses.append(
                f'  <cmd id="{cmd_id}" cmd_text="GetECO">'
                "<mode>Off</mode></cmd>"
            )
        elif command_text == "GetToneControl":
            # denonavr uses convert_string_int_bool for status/adjust: expects "0" or "1", not "Off"/"On"
            cmd_responses.append(
                f'  <cmd id="{cmd_id}" cmd_text="GetToneControl">'
                '<status>0</status><adjust>0</adjust>'
                '<basslevel>0</basslevel><bassvalue>50</bassvalue>'
                '<treblelevel>0</treblelevel><treblevalue>50</treblevalue></cmd>'
            )
        elif command_text == "GetRenameSource":
            cmd_responses.append(
                f'  <cmd id="{cmd_id}" cmd_text="GetRenameSource">'
                "<list></list></cmd>"
            )
        elif command_text == "GetDeletedSource":
            cmd_responses.append(
                f'  <cmd id="{cmd_id}" cmd_text="GetDeletedSource">'
                "<list></list></cmd>"
            )
        else:
            cmd_responses.append(f'  <cmd id="{cmd_id}" cmd_text="{command_text}"></cmd>')

    xml_str = (
        '<?xml version="1.0" encoding="utf-8"?>\n<rx>\n'
        + "\n".join(cmd_responses)
        + "\n</rx>"
    )
    return xml_str.encode("utf-8")


def _mainzone_xml(avr_state: AVRState, config: Config, runtime_state: RuntimeState) -> bytes:
    """Build MainZone XML for denonavr status polling."""
    friendly_name = _get_proxy_friendly_name(config, runtime_state)
    power = (getattr(avr_state, "power", None) if avr_state else None) or "ON"
    vol_raw = (getattr(avr_state, "volume", None) if avr_state else None) or "50"
    volume = volume_to_db(vol_raw)
    mute_val = "on" if (avr_state and getattr(avr_state, "mute", None)) else "off"
    input_src = (getattr(avr_state, "input_source", None) if avr_state else None) or "CD"
    sound_mode = (getattr(avr_state, "sound_mode", None) if avr_state else None) or "STEREO"
    sources = _get_sources(config, runtime_state)
    func_names = [func_name for func_name, _ in sources]
    display_names = [display_name for _, display_name in sources]
    input_func_list = "\n".join(f"    <Value>{func_name}</Value>" for func_name in func_names)
    rename_source = "\n".join(f"    <Value>{display_name}</Value>" for display_name in display_names)
    source_delete = "\n".join("    <Value>USE</Value>" for _ in func_names)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<item>
  <FriendlyName><value>{friendly_name}</value></FriendlyName>
  <Power><value>{power}</value></Power>
  <ZonePower><value>{power}</value></ZonePower>
  <MasterVolume><value>{volume}</value></MasterVolume>
  <Mute><value>{mute_val}</value></Mute>
  <InputFuncSelect><value>{input_src}</value></InputFuncSelect>
  <selectSurround><value>{sound_mode}</value></selectSurround>
  <SurrMode><value>{sound_mode}</value></SurrMode>
  <ECOMode><value>Off</value></ECOMode>
  <InputFuncList>
{input_func_list}
  </InputFuncList>
  <RenameSource>
{rename_source}
  </RenameSource>
  <SourceDelete>
{source_delete}
  </SourceDelete>
</item>""".encode()


def _escape_xml_text(s: str) -> str:
    """Escape &, <, >, " for use in XML element text."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


async def _fetch_avr_description(avr_host: str, logger: logging.Logger) -> str | None:
    """Fetch description.xml from the physical AVR. Returns None on failure."""
    for port in (DEFAULT_SSDP_HTTP_PORT, DISCOVERY_HTTP_PORT, DENON_AIOS_HTTP_PORT):
        url = f"http://{avr_host}:{port}/description.xml"
        try:
            async with httpx.AsyncClient(timeout=AVR_NETWORK_TIMEOUT) as client:
                r = await client.get(url)
                if r.status_code == 200 and r.text:
                    return r.text
        except (httpx.HTTPError, OSError) as e:
            logger.debug("Fetch %s: %s", url, e)
    return None


def _rewrite_avr_description(
    xml_str: str, avr_host: str, advertise_ip: str, logger: logging.Logger
) -> str:
    """Rewrite AVR's description.xml: replace host with proxy IP, add ' Proxy' to friendlyName."""
    # This assumes the avr and the proxy use the same ports for everything (if any are specified)
    xml_str = xml_str.replace(avr_host, advertise_ip)
    # Add " Proxy" to friendlyName if not already present
    def add_proxy(m: re.Match[str]) -> str:
        content = m.group(1)
        if content.endswith(" Proxy"):
            return m.group(0)
        return f"<friendlyName>{content} Proxy</friendlyName>"

    xml_str = re.sub(r"<friendlyName>(.*?)</friendlyName>", add_proxy, xml_str, count=1)
    return xml_str


def _description_xml(config: Config, advertise_ip: str, runtime_state: RuntimeState) -> str:
    """Minimal UPnP device description XML matching what Home Assistant expects.
    Uses physical AVR manufacturer/model from runtime_state.avr_info when available (e.g. after
    HTTP sync) so UC Remote and other clients can detect Denon vs Marantz correctly."""
    friendly_name = _get_proxy_friendly_name(config, runtime_state)
    http_port = runtime_state.get_resolved_port(
        config, "ssdp_http_port", DEFAULT_SSDP_HTTP_PORT
    )
    avr_info = runtime_state.avr_info or AVRInfo.unknown()
    serial = avr_info.udn_serial(advertise_ip)
    manufacturer = (avr_info.manufacturer or "Denon").strip() or "Denon"
    raw_model = (avr_info.model_name or "").strip()
    model_name = f"{raw_model} Proxy" if raw_model else "AVR-Proxy"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>{friendly_name}</friendlyName>
    <manufacturer>{_escape_xml_text(manufacturer)}</manufacturer>
    <modelName>{_escape_xml_text(model_name)}</modelName>
    <serialNumber>{serial}</serialNumber>
    <UDN>uuid:denon-proxy-{serial}</UDN>
    <presentationURL>http://{advertise_ip}:{http_port}/description.xml</presentationURL>
  </device>
</root>"""


def _parse_ssdp_search_target(msg: str) -> str | None:
    """Extract ST (Search Target) from SSDP M-SEARCH request."""
    for line in msg.split("\r\n"):
        if line.upper().startswith("ST:"):
            return line[3:].strip()
    return None


def _ssdp_response(config: Config, advertise_ip: str, st: str, runtime_state: RuntimeState) -> bytes:
    """Build SSDP HTTP 200 response for M-SEARCH."""
    http_port = runtime_state.get_resolved_port(
        config, "ssdp_http_port", DEFAULT_SSDP_HTTP_PORT
    )
    location = f"http://{advertise_ip}:{http_port}/description.xml"
    avr_info = runtime_state.avr_info or AVRInfo.unknown()
    serial = avr_info.udn_serial(advertise_ip)
    usn = f"uuid:denon-proxy-{serial}::{st}"
    return "\r\n".join([
        "HTTP/1.1 200 OK",
        "CACHE-CONTROL: max-age=1800",
        "EXT:",
        f"LOCATION: {location}",
        "SERVER: Linux/1.0 UPnP/1.0 Denon-AVR-Proxy/1.0",
        f"ST: {st}",
        f"USN: {usn}",
        "", "",
    ]).encode("utf-8")


# -----------------------------------------------------------------------------
# SSDP Protocol
# -----------------------------------------------------------------------------

class SSDPProtocol(asyncio.DatagramProtocol):
    """Respond to SSDP M-SEARCH to advertise as Denon AVR."""

    MATCH_ST = (
        "ssdp:all", "upnp:rootdevice",
        "urn:schemas-upnp-org:device:MediaRenderer:1",
        "urn:schemas-upnp-org:device:MediaServer:1",
        "urn:schemas-denon-com:device:AiosDevice:1",
    )

    def __init__(self, config: Config, logger: logging.Logger, runtime_state: RuntimeState) -> None:
        self.config = config
        self.logger = logger
        self.runtime_state = runtime_state
        self.transport: asyncio.DatagramTransport | None = None
        self._advertise_ip = get_advertise_ip(config)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = cast("asyncio.DatagramTransport", transport)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if not self._advertise_ip:
            return
        try:
            msg = data.decode("utf-8", errors="ignore")
            if "M-SEARCH" not in msg:
                return
            st = _parse_ssdp_search_target(msg)
            if not st or not any(m in st for m in self.MATCH_ST):
                return
            self.logger.debug("SSDP M-SEARCH from %s (ST=%s)", addr, st[:50])
            resp = _ssdp_response(self.config, self._advertise_ip, st, self.runtime_state)
            if self.transport:
                self.transport.sendto(resp, addr)
            self.logger.debug("SSDP response sent to %s (ST=%s)", addr, st[:50])
        except OSError as e:
            self.logger.debug("SSDP error: %s", e)


# -----------------------------------------------------------------------------
# HTTP Device Description Handler
# -----------------------------------------------------------------------------

class DeviceDescriptionHandler(asyncio.Protocol):
    """
    Serves HTTP for SSDP (description.xml) and denonavr (Deviceinfo, AppCommand,
    MainZone XML). Enables Home Assistant manual add and SSDP discovery.
    """

    def __init__(
        self,
        description_xml: bytes,
        deviceinfo_xml: bytes,
        appcommand_xml: bytes,
        logger: logging.Logger,
        avr_state: AVRState,
        config: Config,
        runtime_state: RuntimeState,
    ) -> None:
        self.description_xml = description_xml
        self.deviceinfo_xml = deviceinfo_xml
        self.appcommand_xml = appcommand_xml
        self.logger = logger
        self.avr_state = avr_state
        self.config = config
        self.runtime_state = runtime_state
        self._buffer = b""
        self.transport: asyncio.Transport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = cast("asyncio.Transport", transport)

    def data_received(self, data: bytes) -> None:
        self._buffer += data
        if b"\r\n\r\n" not in self._buffer:
            return
        headers_end = self._buffer.index(b"\r\n\r\n")
        headers = self._buffer[:headers_end].decode("utf-8", errors="ignore")
        body_bytes = self._buffer[headers_end + 4:]

        if "POST" in headers.split("\r\n")[0].upper():
            cl = 0
            for line in headers.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    try:
                        cl = int(line.split(":", 1)[1].strip())
                        break
                    except ValueError:
                        pass
            if len(body_bytes) < cl:
                return

        try:
            req = self._buffer.decode("utf-8", errors="ignore")
            lines = req.split("\r\n")
            if not lines:
                self._close()
                return
            req_line = lines[0]
            parts = req_line.split()
            if len(parts) < 2:
                self.logger.debug("HTTP: malformed request line: %r", req_line[:100])
                self._close()
                return
            method, path = parts[0].upper(), parts[1].split("?")[0]
            path_lower = path.lower()

            body = None
            content_type = b"application/xml"

            if method == "GET":
                if path == "/":
                    body = b"<html><body>Denon AVR Proxy</body></html>"
                    content_type = b"text/html"
                elif path_lower == "/description.xml":
                    body = self.description_xml
                elif "/goform/deviceinfo.xml" in path_lower or path_lower.endswith("deviceinfo.xml"):
                    body = self.deviceinfo_xml
                elif "aios_device.xml" in path_lower or "upnp/desc" in path_lower:
                    body = self.description_xml
                elif "mainzonexmlstatus" in path_lower or "mainzonexml" in path_lower:
                    body = _mainzone_xml(self.avr_state, self.config, self.runtime_state)
            elif method == "POST" and "/goform/appcommand.xml" in path_lower:
                body = _appcommand_response_xml(
                    self.config, self.avr_state, body_bytes, self.logger, self.runtime_state
                )

            peername = (
                self.transport.get_extra_info("peername") if self.transport else None
            )
            client_ip = peername[0] if peername else "?"
            client_display = self.config.client_display_for_log(client_ip)

            if body is not None and self.transport is not None:
                resp = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: " + content_type + b"\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                    b"Connection: close\r\n\r\n"
                ) + body
                self.transport.write(resp)
                self.logger.debug("Client %s request: %s %s -> 200 OK", client_display, method, path)
            else:
                if path_lower == "/ws":
                    self.logger.debug("Client %s request: %s %s -> no handler", client_display, method, path)
                else:
                    self.logger.warning("Client %s request: %s %s -> no handler", client_display, method, path)
        except OSError as e:
            self.logger.warning("HTTP handler error: %s", e)
        self._close()

    def _close(self) -> None:
        if self.transport:
            self.transport.close()
            self.transport = None


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

async def run_discovery_servers(
    config: Config,
    logger: logging.Logger,
    avr_state: AVRState,
    runtime_state: RuntimeState,
) -> tuple[asyncio.DatagramTransport | None, list[asyncio.AbstractServer] | None]:
    """
    Start SSDP (UDP 1900) and HTTP device description servers.

    Returns (ssdp_transport, http_servers) or (None, None) if disabled/failed.
    Resolved port (when config has ssdp_http_port=0) is stored in runtime_state.
    """
    if not config.get("enable_ssdp"):
        return None, None

    advertise_ip = get_advertise_ip(config)
    if not advertise_ip:
        logger.warning("SSDP: Could not determine IP to advertise. Set ssdp_advertise_ip in config.")
        return None, None

    if is_docker_internal_ip(advertise_ip):
        logger.warning(
            "Proxy IP %s looks like a Docker/internal address. "
            "Clients on other hosts (e.g. Home Assistant) may not reach it. "
            "Set ssdp_advertise_ip in config to your host's LAN IP.",
            advertise_ip,
        )

    logger.info("SSDP advertising as '%s' at %s", _get_proxy_friendly_name(config, runtime_state), advertise_ip)

    ssdp_transport: asyncio.DatagramTransport | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", SSDP_MCAST_PORT))
        # Join SSDP multicast group to receive M-SEARCH from HA, UC Remote, etc.
        mreq = struct.pack("=4sI", socket.inet_aton(SSDP_MCAST_GRP), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        loop = asyncio.get_running_loop()
        ssdp_transport, _ = await loop.create_datagram_endpoint(
            lambda: SSDPProtocol(config, logger, runtime_state),
            sock=sock,
        )
        logger.info("SSDP listening on UDP %d", SSDP_MCAST_PORT)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            logger.error("SSDP port %d already in use: %s", SSDP_MCAST_PORT, e)
        else:
            logger.warning("SSDP requires port %d (may need root): %s", SSDP_MCAST_PORT, e)

    http_port = config.get("ssdp_http_port", DEFAULT_SSDP_HTTP_PORT)
    if http_port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", 0))
            http_port = s.getsockname()[1]
        runtime_state.ssdp_http_port = http_port
    avr_host = (config.get("avr_host") or "").strip()
    desc_xml_str = None
    if avr_host:
        raw = await _fetch_avr_description(avr_host, logger)
        if raw:
            desc_xml_str = _rewrite_avr_description(raw, avr_host, advertise_ip, logger)
            logger.info("Using AVR description.xml from %s (friendlyName + Proxy)", avr_host)
    if desc_xml_str is None:
        desc_xml_str = _description_xml(config, advertise_ip, runtime_state)
    desc_xml = desc_xml_str.encode("utf-8")
    devinfo_xml = _deviceinfo_xml(config, runtime_state).encode("utf-8")
    appcmd_xml = _appcommand_friendlyname_xml(config, runtime_state).encode("utf-8")

    def http_factory() -> DeviceDescriptionHandler:
        return DeviceDescriptionHandler(
            desc_xml, devinfo_xml, appcmd_xml, logger, avr_state, config, runtime_state
        )

    http_servers: list[asyncio.AbstractServer] = []
    loop = asyncio.get_running_loop()

    # Required: we advertise this port in SSDP LOCATION; binding must succeed.
    try:
        server = await loop.create_server(http_factory, "0.0.0.0", http_port, reuse_address=True)
        http_servers.append(server)
        logger.info("HTTP server on port %d (advertised)", http_port)
    except OSError as e:
        logger.error("Cannot bind ssdp_http_port %d (advertised in discovery): %s", http_port, e)
        if ssdp_transport:
            ssdp_transport.close()
        return None, None

    # Optional: also listen on 80 and 60006 for clients that probe those
    # ports, but only if they're not already listening from above.
    if http_port != DISCOVERY_HTTP_PORT:
        try:
            server = await loop.create_server(http_factory, "0.0.0.0", DISCOVERY_HTTP_PORT, reuse_address=True)
            http_servers.append(server)
            logger.info("HTTP server on port %d", DISCOVERY_HTTP_PORT)
        except OSError as e:
            logger.debug("Port %d unavailable (need root): %s", DISCOVERY_HTTP_PORT, e)

    if http_port != DENON_AIOS_HTTP_PORT:
        try:
            server = await loop.create_server(http_factory, "0.0.0.0", DENON_AIOS_HTTP_PORT, reuse_address=True)
            http_servers.append(server)
            logger.info("HTTP server on port %d", DENON_AIOS_HTTP_PORT)
        except OSError as e:
            logger.debug("Port %d unavailable: %s", DENON_AIOS_HTTP_PORT, e)

    logger.info("Device description at http://%s:%d/description.xml", advertise_ip, http_port)
    return ssdp_transport, http_servers
