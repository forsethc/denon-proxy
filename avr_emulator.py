"""
Denon AVR Emulator - HTTP/SSDP layer that emulates a Denon AVR.

Serves device discovery (SSDP), device description XML, Deviceinfo, AppCommand,
and MainZone status endpoints. Compatible with Home Assistant's denonavr
integration and UC Remote 3.

Can be used standalone (pure emulation for testing) or wired to a proxy.

Usage (standalone):
    from avr_emulator import AVRState, run_emulator_servers

    async def main():
        state = AVRState()
        state.power = "ON"
        state.volume = "50"
        await run_emulator_servers(config, logger, state)

Usage (with denon-proxy):
    from avr_emulator import run_emulator_servers
    await run_emulator_servers(config, logger, proxy.state)
"""

from __future__ import annotations

import asyncio
import logging
import socket
import xml.etree.ElementTree as ET
from typing import Any, Optional

# -----------------------------------------------------------------------------
# AVR State - canonical Denon AVR state model
# -----------------------------------------------------------------------------

class AVRState:
    """
    Tracks Denon AVR state (power, volume, input, mute, sound_mode).
    Used by the emulator for HTTP/XML responses and by the proxy for telnet.
    """

    def __init__(self) -> None:
        # Defaults for demo mode / before AVR responds
        self.power: Optional[str] = "ON"       # ON, STANDBY, OFF
        self.volume: Optional[str] = "50"      # e.g. "50" (0-98), "MAX"
        self.input_source: Optional[str] = "CD"  # e.g. "CD", "TUNER", "DVD"
        self.mute: Optional[bool] = False      # True = muted
        self.sound_mode: Optional[str] = "STEREO"  # e.g. STEREO, MULTI CH IN, DOLBY DIGITAL
        self.raw_responses: list[str] = []     # Recent responses (proxy use)

    def update_from_message(self, message: str) -> None:
        """Update state from a Denon telnet response (PW, MV, SI, MU, ZM, MS)."""
        if not message or len(message) < 2:
            return

        if message.startswith("PW"):
            if message == "PWON":
                self.power = "ON"
            elif message == "PWSTANDBY" or "STANDBY" in message:
                self.power = "STANDBY"
            elif message == "PW?":
                pass
            elif len(message) > 2:
                param = message[2:].strip()
                if param and param != "?":
                    self.power = param

        elif message.startswith("ZM"):
            if "ON" in message.upper():
                self.power = "ON"
            elif "OFF" in message.upper() or "STANDBY" in message.upper():
                self.power = "STANDBY"

        elif message.startswith("MV") and len(message) > 2:
            param = message[2:].strip()
            if param and param != "?":
                self.volume = param

        elif message.startswith("MU"):
            if "ON" in message.upper():
                self.mute = True
            elif "OFF" in message.upper():
                self.mute = False

        elif message.startswith("SI") and len(message) > 2:
            param = message[2:].strip()
            if param and param != "?":
                self.input_source = param

        elif message.startswith("MS") and len(message) > 2:
            param = message[2:].strip()
            if param and param != "?":
                self.sound_mode = param

    def get_status_dump(self) -> str:
        """Return Denon telnet-format status lines for new clients."""
        lines = []
        if self.power:
            lines.append(f"PW{self.power}")
            # ZM (Zone Main) so HA denonavr receives power updates via telnet (it ignores PW)
            if self.power == "ON":
                lines.append("ZMON")
            elif self.power in ("STANDBY", "OFF"):
                lines.append("ZMOFF")
        if self.volume:
            lines.append(f"MV{self.volume}")
        if self.input_source:
            lines.append(f"SI{self.input_source}")
        if self.mute is not None:
            lines.append("MUON" if self.mute else "MUOFF")
        if self.sound_mode:
            lines.append(f"MS{self.sound_mode}")
        return "\r\n".join(lines) + "\r\n" if lines else ""

    def snapshot(self) -> dict:
        """Snapshot for optimistic update rollback."""
        return {
            "power": self.power,
            "volume": self.volume,
            "input_source": self.input_source,
            "mute": self.mute,
            "sound_mode": self.sound_mode,
        }

    def restore(self, snapshot: dict) -> None:
        """Restore from snapshot after failed send."""
        self.power = snapshot.get("power")
        self.volume = snapshot.get("volume")
        self.input_source = snapshot.get("input_source")
        self.mute = snapshot.get("mute")
        self.sound_mode = snapshot.get("sound_mode")

    def apply_command(self, command: str) -> bool:
        """Optimistically apply a Denon telnet command. Returns True if state changed."""
        if not command or len(command) < 2:
            return False
        cmd = command.strip().upper()
        applied = False
        if cmd.startswith("PW"):
            if cmd == "PWON":
                self.power = "ON"
                applied = True
            elif "STANDBY" in cmd:
                self.power = "STANDBY"
                applied = True
            elif len(cmd) > 2 and cmd[2] not in ("?", ""):
                self.power = cmd[2:]
                applied = True
        elif cmd.startswith("ZM"):
            if "ON" in cmd:
                self.power = "ON"
                applied = True
            elif "OFF" in cmd or "STANDBY" in cmd:
                self.power = "STANDBY"
                applied = True
        elif cmd.startswith("MV") and len(cmd) > 2:
            param = cmd[2:].strip()
            if param and param != "?":
                self.volume = param
                applied = True
        elif cmd.startswith("MU"):
            if "ON" in cmd:
                self.mute = True
                applied = True
            elif "OFF" in cmd:
                self.mute = False
                applied = True
        elif cmd.startswith("SI") and len(cmd) > 2:
            param = cmd[2:].strip()
            if param and param != "?":
                self.input_source = param
                applied = True
        elif cmd.startswith("MS") and len(cmd) > 2:
            param = cmd[2:].strip()
            if param and param != "?":
                self.sound_mode = param
                applied = True
        return applied


# -----------------------------------------------------------------------------
# Demo sources - matches typical Denon AVR-X inputs for HA integration
# -----------------------------------------------------------------------------

DEMO_SOURCES = [
    ("CD", "CD"),
    ("DVD", "DVD"),
    ("BD", "Blu-ray"),
    ("GAME", "Game"),
    ("MPLAY", "Media Player"),
    ("SAT/CBL", "CBL/SAT"),
    ("TV", "TV Audio"),
    ("TUNER", "Tuner"),
    ("PHONO", "Phono"),
    ("AUX1", "AUX"),
    ("NET", "Network"),
    ("BT", "Bluetooth"),
    ("USB/IPOD", "iPod/USB"),
    ("HDMI1", "HDMI 1"),
    ("HDMI2", "HDMI 2"),
    ("HDMI3", "HDMI 3"),
    ("HDMI4", "HDMI 4"),
    ("HDMI5", "HDMI 5"),
    ("HDMI6", "HDMI 6"),
    ("HDMI7", "HDMI 7"),
]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def get_advertise_ip(config: dict) -> Optional[str]:
    """Get the IP to advertise in SSDP LOCATION."""
    ip = config.get("ssdp_advertise_ip", "").strip()
    if ip:
        return ip
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2.0)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


# -----------------------------------------------------------------------------
# XML builders
# -----------------------------------------------------------------------------

def deviceinfo_xml(config: dict) -> str:
    """Deviceinfo.xml - identify as pre-2016 AVR so denonavr uses port 8080/description.xml
    (avoids port 60006 aios_device.xml which can cause HA config flow issues)."""
    sources_xml = "\n".join(
        f'      <Source><FuncName>{fn}</FuncName><DefaultName>{dn}</DefaultName></Source>'
        for fn, dn in DEMO_SOURCES
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Device_Info>
  <ModelName>AVR-3808</ModelName>
  <CommApiVers>0100</CommApiVers>
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


def appcommand_friendlyname_xml(config: dict) -> str:
    """AppCommand.xml response for GetFriendlyName (denonavr setup)."""
    name = config.get("ssdp_friendly_name", "Denon AVR Proxy")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rx>
  <cmd id="1">
    <friendlyname>{name}</friendlyname>
  </cmd>
</rx>"""


def parse_appcommand_request(body_bytes: bytes) -> list[tuple[str, str]]:
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


def appcommand_response_xml(
    config: dict,
    state: Any,
    body_bytes: bytes,
    logger: Optional[logging.Logger] = None,
) -> bytes:
    """
    Build AppCommand.xml response from request body.
    State should have: power, volume, mute, input_source (all optional).
    """
    power = (getattr(state, "power", None) if state else None) or "ON"
    volume = (getattr(state, "volume", None) if state else None) or "50"
    mute_val = "on" if (state and getattr(state, "mute", None)) else "off"
    input_src = (getattr(state, "input_source", None) if state else None) or "CD"
    friendly_name = config.get("ssdp_friendly_name", "Denon AVR Proxy")
    sound_mode = (getattr(state, "sound_mode", None) if state else None) or "STEREO"

    cmds_requested = parse_appcommand_request(body_bytes)
    if logger and logger.isEnabledFor(logging.DEBUG):
        logger.debug("AppCommand requested: %s", [ct for _, ct in cmds_requested])
    if not cmds_requested:
        cmds_requested = [("1", "GetFriendlyName")]

    cmd_responses = []
    for cid, cmd_text in cmds_requested:
        ct = cmd_text or ""
        if ct == "GetFriendlyName":
            cmd_responses.append(
                f'  <cmd id="{cid}" cmd_text="GetFriendlyName">'
                f"<friendlyname>{friendly_name}</friendlyname></cmd>"
            )
        elif ct == "GetAllZonePowerStatus":
            cmd_responses.append(
                f'  <cmd id="{cid}" cmd_text="GetAllZonePowerStatus">'
                f"<zone1>{power}</zone1></cmd>"
            )
        elif ct == "GetAllZoneVolume":
            cmd_responses.append(
                f'  <cmd id="{cid}" cmd_text="GetAllZoneVolume">'
                f"<zone1><volume>{volume}</volume></zone1></cmd>"
            )
        elif ct == "GetAllZoneMuteStatus":
            cmd_responses.append(
                f'  <cmd id="{cid}" cmd_text="GetAllZoneMuteStatus">'
                f"<zone1>{mute_val}</zone1></cmd>"
            )
        elif ct == "GetAllZoneSource":
            cmd_responses.append(
                f'  <cmd id="{cid}" cmd_text="GetAllZoneSource">'
                f"<zone1><source>{input_src}</source></zone1></cmd>"
            )
        elif ct == "GetSurroundModeStatus":
            cmd_responses.append(
                f'  <cmd id="{cid}" cmd_text="GetSurroundModeStatus">'
                f"<surround>{sound_mode}</surround></cmd>"
            )
        elif ct == "GetAutoStandby":
            cmd_responses.append(
                f'  <cmd id="{cid}" cmd_text="GetAutoStandby">'
                '<list><listvalue><zone>Main</zone><value>OFF</value>'
                "</listvalue></list></cmd>"
            )
        elif ct == "GetDimmer":
            cmd_responses.append(
                f'  <cmd id="{cid}" cmd_text="GetDimmer">'
                '<value>Bright</value></cmd>'
            )
        elif ct == "GetECO":
            cmd_responses.append(
                f'  <cmd id="{cid}" cmd_text="GetECO">'
                "<mode>Off</mode></cmd>"
            )
        elif ct == "GetToneControl":
            cmd_responses.append(
                f'  <cmd id="{cid}" cmd_text="GetToneControl">'
                '<status>Off</status><adjust>0</adjust>'
                '<basslevel>0</basslevel><bassvalue>50</bassvalue>'
                '<treblelevel>0</treblelevel><treblevalue>50</treblevalue></cmd>'
            )
        elif ct == "GetRenameSource":
            cmd_responses.append(
                f'  <cmd id="{cid}" cmd_text="GetRenameSource">'
                "<list></list></cmd>"
            )
        elif ct == "GetDeletedSource":
            cmd_responses.append(
                f'  <cmd id="{cid}" cmd_text="GetDeletedSource">'
                "<list></list></cmd>"
            )
        else:
            cmd_responses.append(f'  <cmd id="{cid}" cmd_text="{ct}"></cmd>')

    xml_str = (
        '<?xml version="1.0" encoding="utf-8"?>\n<rx>\n'
        + "\n".join(cmd_responses)
        + "\n</rx>"
    )
    return xml_str.encode("utf-8")


def mainzone_xml(state: Any, friendly_name: str = "Denon AVR Proxy") -> bytes:
    """Build MainZone XML for denonavr status polling."""
    power = (getattr(state, "power", None) if state else None) or "ON"
    volume = (getattr(state, "volume", None) if state else None) or "50"
    mute_val = "on" if (state and getattr(state, "mute", None)) else "off"
    input_src = (getattr(state, "input_source", None) if state else None) or "CD"
    sound_mode = (getattr(state, "sound_mode", None) if state else None) or "STEREO"
    func_names = [fn for fn, _ in DEMO_SOURCES]
    input_func_list = "\n".join(f"    <Value>{fn}</Value>" for fn in func_names)
    rename_source = "\n".join(f"    <Value>{fn}</Value>" for fn in func_names)
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
</item>""".encode("utf-8")


def device_description_xml(config: dict, advertise_ip: str) -> str:
    """Minimal UPnP device description XML matching what Home Assistant expects."""
    friendly_name = config.get("ssdp_friendly_name", "Denon AVR Proxy")
    http_port = config.get("ssdp_http_port", 8080)
    serial = f"proxy-{advertise_ip.replace('.', '-')}"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>{friendly_name}</friendlyName>
    <manufacturer>Denon</manufacturer>
    <modelName>AVR-Proxy</modelName>
    <serialNumber>{serial}</serialNumber>
    <UDN>uuid:denon-proxy-{serial}</UDN>
    <presentationURL>http://{advertise_ip}:{http_port}/description.xml</presentationURL>
  </device>
</root>"""


def parse_msearch_st(msg: str) -> Optional[str]:
    """Extract ST (Search Target) from M-SEARCH request."""
    for line in msg.split("\r\n"):
        if line.upper().startswith("ST:"):
            return line[3:].strip()
    return None


def ssdp_response(config: dict, advertise_ip: str, st: str) -> bytes:
    """Build SSDP HTTP 200 response for M-SEARCH."""
    http_port = config.get("ssdp_http_port", 8080)
    location = f"http://{advertise_ip}:{http_port}/description.xml"
    serial = f"proxy-{advertise_ip.replace('.', '-')}"
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

    def __init__(self, config: dict, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.transport: Optional[asyncio.DatagramTransport] = None
        self._advertise_ip = get_advertise_ip(config)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if not self._advertise_ip:
            return
        try:
            msg = data.decode("utf-8", errors="ignore")
            if "M-SEARCH" not in msg:
                return
            st = parse_msearch_st(msg)
            if not st or not any(m in st for m in self.MATCH_ST):
                return
            resp = ssdp_response(self.config, self._advertise_ip, st)
            if self.transport:
                self.transport.sendto(resp, addr)
            self.logger.debug("SSDP response sent to %s (ST=%s)", addr, st[:50])
        except Exception as e:
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
        state: Any = None,
        config: Optional[dict] = None,
    ) -> None:
        self.description_xml = description_xml
        self.deviceinfo_xml = deviceinfo_xml
        self.appcommand_xml = appcommand_xml
        self.logger = logger
        self.state = state
        self.config = config or {}
        self._buffer = b""
        self.transport: Optional[asyncio.BaseTransport] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

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
                if path_lower == "/description.xml" or path == "/":
                    body = self.description_xml if "description" in path_lower else b"<html><body>Denon AVR Proxy</body></html>"
                    content_type = b"application/xml" if "description" in path_lower else b"text/html"
                elif "/goform/deviceinfo.xml" in path_lower or path_lower.endswith("deviceinfo.xml"):
                    body = self.deviceinfo_xml
                elif "aios_device.xml" in path_lower or "upnp/desc" in path_lower:
                    body = self.description_xml
                elif "mainzonexmlstatus" in path_lower or "mainzonexml" in path_lower:
                    body = mainzone_xml(
                        self.state,
                        self.config.get("ssdp_friendly_name", "Denon AVR Proxy"),
                    )
            elif method == "POST" and "/goform/appcommand.xml" in path_lower:
                body = appcommand_response_xml(
                    self.config, self.state, body_bytes, self.logger
                )

            peername = self.transport.get_extra_info("peername") if self.transport else None
            client_ip = peername[0] if peername else "?"

            if body is not None:
                resp = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: " + content_type + b"\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                    b"Connection: close\r\n\r\n"
                ) + body
                self.transport.write(resp)
                self.logger.debug("Client %s request: %s %s -> 200 OK", client_ip, method, path)
            else:
                if path_lower == "/ws":
                    self.logger.debug("Client %s request: %s %s -> no handler", client_ip, method, path)
                else:
                    self.logger.warning("Client %s request: %s %s -> no handler", client_ip, method, path)
        except Exception as e:
            self.logger.warning("HTTP handler error: %s", e)
        self._close()

    def _close(self) -> None:
        if self.transport:
            self.transport.close()
            self.transport = None


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

async def run_emulator_servers(
    config: dict,
    logger: logging.Logger,
    state: Any = None,
) -> tuple[Optional[asyncio.DatagramTransport], Optional[list]]:
    """
    Start SSDP (UDP 1900) and HTTP device description servers.

    Returns (ssdp_transport, http_servers) or (None, None) if disabled/failed.
    """
    if not config.get("enable_ssdp"):
        return None, None

    advertise_ip = get_advertise_ip(config)
    if not advertise_ip:
        logger.warning("SSDP: Could not determine IP to advertise. Set ssdp_advertise_ip in config.")
        return None, None

    logger.info("SSDP advertising as %s at %s", config.get("ssdp_friendly_name"), advertise_ip)

    ssdp_transport = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", 1900))
        loop = asyncio.get_running_loop()
        ssdp_transport, _ = await loop.create_datagram_endpoint(
            lambda: SSDPProtocol(config, logger),
            sock=sock,
        )
        logger.info("SSDP listening on UDP 1900")
    except OSError as e:
        logger.warning("SSDP requires port 1900 (may need root): %s", e)

    http_port = config.get("ssdp_http_port", 8080)
    desc_xml = device_description_xml(config, advertise_ip).encode("utf-8")
    devinfo_xml = deviceinfo_xml(config).encode("utf-8")
    appcmd_xml = appcommand_friendlyname_xml(config).encode("utf-8")

    def http_factory():
        return DeviceDescriptionHandler(
            desc_xml, devinfo_xml, appcmd_xml, logger, state, config
        )

    http_servers = []
    for port in (80, http_port, 60006):
        if port == http_port and (80 == http_port or 60006 == http_port):
            continue
        try:
            server = await asyncio.get_running_loop().create_server(
                http_factory, "0.0.0.0", port, reuse_address=True,
            )
            http_servers.append(server)
            logger.info("HTTP server on port %d", port)
        except OSError as e:
            if port == 80:
                logger.debug("Port 80 unavailable (need root): %s", e)
            elif port == 60006:
                logger.debug("Port 60006 unavailable: %s", e)
            else:
                logger.warning("HTTP port %d unavailable: %s", port, e)

    if not http_servers and ssdp_transport:
        ssdp_transport.close()
        return None, None

    logger.info("Device description at http://%s:%d/description.xml", advertise_ip, http_port)
    return ssdp_transport, http_servers
