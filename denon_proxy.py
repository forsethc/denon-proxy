#!/usr/bin/env python3
"""
Denon AVR Proxy - Virtual Denon AVR for multiple clients.

This proxy allows Home Assistant, UC Remote 3, and other Denon-compatible
clients to connect simultaneously, while maintaining a single Telnet
connection to the physical AVR.

Usage:
    python denon_proxy.py [--config config.yaml]
    
    Or with environment variables:
    AVR_HOST=192.168.1.100 PROXY_PORT=2323 python denon_proxy.py
"""

import argparse
import asyncio
import logging
import signal
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Optional, Set

try:
    import yaml
except ImportError:
    yaml = None

# Optional: use denonavr for initial state sync via HTTP (doesn't use telnet)
try:
    import denonavr
    DENONAVR_AVAILABLE = True
except ImportError:
    DENONAVR_AVAILABLE = False

# -----------------------------------------------------------------------------
# Logging setup
# -----------------------------------------------------------------------------

def setup_logging(level: str = "INFO") -> None:
    """Configure logging format and level."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Reduce noise from libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

def load_config(config_path: Optional[Path] = None) -> dict:
    """Load configuration from YAML file with environment overrides."""
    if yaml is None:
        raise ImportError("PyYAML is required. Install with: pip install pyyaml")
    defaults = {
        "avr_host": "192.168.1.100",
        "avr_port": 23,
        "proxy_host": "0.0.0.0",
        "proxy_port": 2323,
        "log_level": "INFO",
        "enable_ssdp": False,
        "ssdp_friendly_name": "Denon AVR Proxy",
        "ssdp_http_port": 8080,
        "ssdp_advertise_ip": "",
        "optimistic_state": True,
        "optimistic_broadcast_delay": 0.1,
    }

    config = defaults.copy()

    # Load config: explicit path, or config.yaml in project dir
    if config_path:
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        path = config_path
    else:
        path = Path(__file__).parent / "config.yaml"
        if not path.exists():
            raise FileNotFoundError(
                f"Config not found: {path}\n"
                "Copy config.sample.yaml to config.yaml and edit as needed."
            )
    with open(path) as f:
        config.update(yaml.safe_load(f) or {})

    # Environment overrides
    import os
    if os.getenv("AVR_HOST"):
        config["avr_host"] = os.getenv("AVR_HOST")
    if os.getenv("AVR_PORT"):
        config["avr_port"] = int(os.getenv("AVR_PORT"))
    if os.getenv("PROXY_HOST"):
        config["proxy_host"] = os.getenv("PROXY_HOST")
    if os.getenv("PROXY_PORT"):
        config["proxy_port"] = int(os.getenv("PROXY_PORT"))
    if os.getenv("LOG_LEVEL"):
        config["log_level"] = os.getenv("LOG_LEVEL")

    return config


# -----------------------------------------------------------------------------
# State tracking - parse Denon telnet responses to maintain current state
# -----------------------------------------------------------------------------

class AVRState:
    """
    Tracks the current AVR state from telnet responses.
    New clients receive this state immediately upon connection.
    """

    def __init__(self) -> None:
        self.power: Optional[str] = None       # ON, STANDBY, OFF
        self.volume: Optional[str] = None      # e.g. "50" (0-98), "MAX"
        self.input_source: Optional[str] = None  # e.g. "CD", "TUNER", "DVD"
        self.mute: Optional[bool] = None       # True = muted
        self.raw_responses: list[str] = []     # Recent responses for state dump

    def update_from_message(self, message: str) -> None:
        """Update state from an AVR telnet response message."""
        if not message or len(message) < 2:
            return

        # Power: PWON, PWSTANDBY, PW?
        if message.startswith("PW"):
            if message == "PWON":
                self.power = "ON"
            elif message == "PWSTANDBY" or "STANDBY" in message:
                self.power = "STANDBY"
            elif message == "PW?":
                pass  # Query, no state change
            elif len(message) > 2:
                param = message[2:].strip()
                if param and param != "?":
                    self.power = param

        # Main zone power: ZMON, ZMOFF
        elif message.startswith("ZM"):
            if "ON" in message.upper():
                self.power = "ON"
            elif "OFF" in message.upper() or "STANDBY" in message.upper():
                self.power = "STANDBY"

        # Master volume: MV50, MVMAX, MV?
        elif message.startswith("MV") and len(message) > 2:
            param = message[2:].strip()
            if param and param != "?":
                self.volume = param

        # Mute: MUON, MUOFF, MU?
        elif message.startswith("MU"):
            if "ON" in message.upper():
                self.mute = True
            elif "OFF" in message.upper():
                self.mute = False

        # Source/Input: SICD, SITUNER, SIDVD, etc.
        elif message.startswith("SI") and len(message) > 2:
            param = message[2:].strip()
            if param and param != "?":
                self.input_source = param

    def get_status_dump(self) -> str:
        """Return newline-separated status lines for a new client."""
        lines = []
        if self.power:
            lines.append(f"PW{self.power}")
        if self.volume:
            lines.append(f"MV{self.volume}")
        if self.input_source:
            lines.append(f"SI{self.input_source}")
        if self.mute is not None:
            lines.append("MUON" if self.mute else "MUOFF")
        return "\r\n".join(lines) + "\r\n" if lines else ""

    def snapshot(self) -> dict:
        """Snapshot current state for potential restore (optimistic updates)."""
        return {
            "power": self.power,
            "volume": self.volume,
            "input_source": self.input_source,
            "mute": self.mute,
        }

    def restore(self, snapshot: dict) -> None:
        """Restore state from snapshot after failed send."""
        self.power = snapshot.get("power")
        self.volume = snapshot.get("volume")
        self.input_source = snapshot.get("input_source")
        self.mute = snapshot.get("mute")

    def apply_command(self, command: str) -> bool:
        """
        Optimistically apply a command's effect to state. Returns True if
        the command would change state (was applied).
        """
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
        return applied


# -----------------------------------------------------------------------------
# AVR Connection - single telnet connection to physical AVR
# -----------------------------------------------------------------------------

class AVRConnection:
    """
    Maintains a single Telnet connection to the physical Denon AVR.
    Receives responses and forwards them to the proxy for broadcasting.
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_response: Callable[[str], None],
        on_disconnect: Callable[[], None],
        state: AVRState,
        logger: logging.Logger,
    ) -> None:
        self.host = host
        self.port = port
        self.on_response = on_response
        self.on_disconnect = on_disconnect
        self.state = state
        self.logger = logger
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._buffer = b""
        self._reconnect_task: Optional[asyncio.Task] = None

    async def connect(self) -> bool:
        """Establish connection to the AVR."""
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=5.0,
            )
            self.logger.info("Connected to AVR at %s:%d", self.host, self.port)
            asyncio.create_task(self._read_loop())
            return True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
            self.logger.error("Failed to connect to AVR: %s", e)
            return False

    async def _read_loop(self) -> None:
        """Read data from AVR and forward to callback."""
        try:
            while self.reader and not self.reader.at_eof():
                data = await self.reader.read(1024)
                if not data:
                    break
                self._buffer += data
                while b"\r" in self._buffer or b"\n" in self._buffer:
                    # Split on \r or \n
                    for sep in (b"\r", b"\n"):
                        if sep in self._buffer:
                            line, _, self._buffer = self._buffer.partition(sep)
                            break
                    else:
                        break
                    try:
                        msg = line.decode("utf-8").strip()
                    except UnicodeDecodeError:
                        continue
                    if msg:
                        self.state.update_from_message(msg)
                        self.on_response(msg)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.warning("AVR read error: %s", e)
        finally:
            self._handle_disconnect()

    def _handle_disconnect(self) -> None:
        """Handle AVR disconnect and schedule reconnect."""
        if self.writer:
            try:
                self.writer.close()
                asyncio.create_task(self.writer.wait_closed())
            except Exception:
                pass
            self.reader = None
            self.writer = None
        self.logger.warning("Disconnected from AVR")
        self.on_disconnect()

    async def send_command(self, command: str) -> bool:
        """
        Send a telnet command to the AVR. Returns True if sent successfully,
        False if AVR not connected or send failed.
        """
        if not self.writer or self.writer.is_closing():
            self.logger.warning("Cannot send command, AVR not connected: %s", command)
            return False
        try:
            data = (command.strip() + "\r").encode("utf-8")
            self.writer.write(data)
            await self.writer.drain()
            self.logger.debug("Sent to AVR: %s", command.strip())
            return True
        except Exception as e:
            self.logger.warning("Failed to send command to AVR: %s - %s", command, e)
            return False

    async def request_state(self) -> None:
        """Request current state from AVR (power, volume, input, mute)."""
        for cmd in ("PW?", "MV?", "SI?", "MU?", "ZM?"):
            await self.send_command(cmd)
            await asyncio.sleep(0.05)  # Brief delay between queries

    def close(self) -> None:
        """Close the AVR connection."""
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self.writer:
            self.writer.close()
            self.reader = None
            self.writer = None


# -----------------------------------------------------------------------------
# Client connection handler
# -----------------------------------------------------------------------------

class ClientHandler(asyncio.Protocol):
    """
    Handles a single client connection to the proxy.
    Receives commands from client, forwards to AVR, receives broadcasts.
    """

    def __init__(
        self,
        avr: AVRConnection,
        state: AVRState,
        clients: Set["ClientHandler"],
        logger: logging.Logger,
        config: Optional[dict] = None,
    ) -> None:
        self.avr = avr
        self.state = state
        self.clients = clients
        self.logger = logger
        self.config = config or {}
        self.transport: Optional[asyncio.Transport] = None
        self._buffer = b""
        self._peername: Optional[tuple] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Called when a client connects."""
        self.transport = transport
        self._peername = transport.get_extra_info("peername")
        self.clients.add(self)
        self.logger.info("Client connected: %s (total: %d)", self._peername, len(self.clients))

        # Send current state to new client
        status = self.state.get_status_dump()
        if status and self.transport:
            self.transport.write(status.encode("utf-8"))

    def data_received(self, data: bytes) -> None:
        """Handle data from client - parse commands and forward to AVR."""
        self._buffer += data
        while b"\r" in self._buffer or b"\n" in self._buffer:
            for sep in (b"\r", b"\n"):
                if sep in self._buffer:
                    line, _, self._buffer = self._buffer.partition(sep)
                    break
            else:
                break
            try:
                cmd = line.decode("utf-8").strip()
            except UnicodeDecodeError:
                continue
            if cmd:
                self._handle_command(cmd)

    def _handle_command(self, command: str) -> None:
        """Process and forward a client command to the AVR."""
        # Basic validation: Denon commands are typically 2-4 letter prefix + optional value
        if len(command) < 2:
            return
        # Filter out telnet negotiation bytes if any leak through
        if any(ord(c) < 32 and c not in "\r\n\t" for c in command):
            return

        self.logger.debug("Client %s command: %s", self._peername, command)
        asyncio.create_task(self._handle_command_async(command))

    def _broadcast_state(self) -> None:
        """Broadcast current state to all clients (emulates AVR confirmation)."""
        status = self.state.get_status_dump()
        if status:
            for line in status.strip().splitlines():
                if line.strip():
                    for c in list(self.clients):
                        c.broadcast(line.strip())

    async def _handle_command_async(self, command: str) -> None:
        """Apply optimistic state, send to AVR, revert only if send failed with AVR connected."""
        optimistic = self.config.get("optimistic_state", True)
        delay = max(0.0, float(self.config.get("optimistic_broadcast_delay", 0.1)))
        snapshot = None
        had_connection = self.avr.writer is not None and not self.avr.writer.is_closing()

        if optimistic:
            snapshot = self.state.snapshot()
            if not self.state.apply_command(command):
                snapshot = None  # command didn't change state, nothing to revert

        send_task = asyncio.create_task(self.avr.send_command(command))
        if delay > 0:
            await asyncio.sleep(delay)
        success = await send_task

        if optimistic and snapshot is not None and not success and had_connection:
            self.state.restore(snapshot)
            self.logger.debug("Reverted optimistic state after failed send")

        if optimistic and snapshot is not None:
            self._broadcast_state()

    def broadcast(self, message: str) -> None:
        """Send a message to this client."""
        if self.transport and not self.transport.is_closing():
            try:
                self.transport.write((message + "\r").encode("utf-8"))
            except Exception as e:
                self.logger.debug("Broadcast to client failed: %s", e)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        """Called when client disconnects."""
        self.clients.discard(self)
        self.transport = None
        self.logger.info("Client disconnected: %s (remaining: %d)", self._peername, len(self.clients))


# -----------------------------------------------------------------------------
# Proxy server
# -----------------------------------------------------------------------------

class DenonProxyServer:
    """
    Main proxy server: accepts client connections and bridges them
    to a single AVR connection.
    """

    def __init__(self, config: dict, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.state = AVRState()
        self.clients: Set[ClientHandler] = set()
        self.avr: Optional[AVRConnection] = None
        self._server: Optional[asyncio.Server] = None

    def _broadcast(self, message: str) -> None:
        """Broadcast an AVR response to all connected clients."""
        for client in list(self.clients):
            client.broadcast(message)

    def _on_avr_response(self, message: str) -> None:
        """Called when the AVR sends a response."""
        self._broadcast(message)

    def _on_avr_disconnect(self) -> None:
        """Called when AVR disconnects - schedule reconnect."""
        async def reconnect():
            await asyncio.sleep(2)
            if self.avr:
                await self.avr.connect()
                if self.avr.writer:
                    await self.avr.request_state()

        asyncio.create_task(reconnect())

    async def _sync_initial_state(self) -> None:
        """Use denonavr HTTP API to fetch initial state (if available)."""
        if not DENONAVR_AVAILABLE:
            return
        try:
            d = denonavr.DenonAVR(self.config["avr_host"])
            await asyncio.wait_for(
                d.async_setup(),
                timeout=10.0,
            )
            await asyncio.wait_for(
                d.async_update(),
                timeout=10.0,
            )
            if d.power:
                self.state.power = d.power
            if d.vol and d.vol.volume is not None:
                # denonavr uses dB like -36.5; Denon telnet uses 0-98
                vol_db = d.vol.volume
                # Approximate conversion: 80 = 0dB, scale varies by model
                vol_int = max(0, min(98, int(80 + vol_db * 2)))
                self.state.volume = str(vol_int)
            if d.input_func:
                self.state.input_source = d.input_func
            if d.muted is not None:
                self.state.mute = d.muted
            self.logger.info("Initial state from HTTP: power=%s vol=%s input=%s mute=%s",
                             self.state.power, self.state.volume,
                             self.state.input_source, self.state.mute)
        except Exception as e:
            self.logger.debug("Could not sync initial state via HTTP: %s", e)

    async def start(self) -> None:
        """Start the proxy server and AVR connection."""
        # Optional: fetch initial state via HTTP
        await self._sync_initial_state()

        # Create AVR connection
        self.avr = AVRConnection(
            host=self.config["avr_host"],
            port=self.config["avr_port"],
            on_response=self._on_avr_response,
            on_disconnect=self._on_avr_disconnect,
            state=self.state,
            logger=self.logger,
        )

        # Connect to AVR
        connected = await self.avr.connect()
        if connected:
            await asyncio.sleep(0.5)
            await self.avr.request_state()

        # Start proxy server
        def factory():
            return ClientHandler(
                avr=self.avr,
                state=self.state,
                clients=self.clients,
                logger=self.logger,
                config=self.config,
            )

        host = self.config["proxy_host"]
        port = self.config["proxy_port"]
        self._server = await asyncio.get_running_loop().create_server(
            factory,
            host,
            port,
            reuse_address=True,
        )
        self.logger.info("Proxy listening on %s:%d (AVR: %s:%d)",
                         host, port, self.config["avr_host"], self.config["avr_port"])
        self.logger.info("Connect Home Assistant and UC Remote 3 to %s:%d", host, port)

    async def stop(self) -> None:
        """Stop the proxy server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self.avr:
            self.avr.close()
        self.logger.info("Proxy stopped")


# -----------------------------------------------------------------------------
# SSDP Discovery (optional) - emulates Denon AVR for Home Assistant
# -----------------------------------------------------------------------------
#
# Home Assistant discovers Denon AVRs via SSDP:
# 1. HA sends M-SEARCH to 239.255.255.250:1900
# 2. Devices respond with HTTP 200 + LOCATION header (URL to device description XML)
# 3. HA fetches that URL and parses manufacturer, modelName, serialNumber, friendlyName
# 4. HA uses the host from LOCATION to connect (telnet port 23, HTTP 80/8080)
#
# We emulate this by: responding to M-SEARCH + serving device description XML over HTTP.

def _get_advertise_ip(config: dict) -> Optional[str]:
    """Get the IP to advertise in SSDP LOCATION."""
    ip = config.get("ssdp_advertise_ip", "").strip()
    if ip:
        return ip
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2.0)  # avoid blocking event loop / hanging on Ctrl-C
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


# Demo source list for AVR-X1600H - matches typical Denon inputs for HA testing
_DEMO_SOURCES = [
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


def _deviceinfo_xml(config: dict) -> str:
    """Deviceinfo.xml so denonavr identifies us as AVR-X with full source list for demo."""
    sources_xml = "\n".join(
        f'      <Source><FuncName>{fn}</FuncName><DefaultName>{dn}</DefaultName></Source>'
        for fn, dn in _DEMO_SOURCES
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Device_Info>
  <ModelName>AVR-X1600H</ModelName>
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


def _appcommand_friendlyname_xml(config: dict) -> str:
    """AppCommand.xml response for GetFriendlyName (denonavr setup)."""
    name = config.get("ssdp_friendly_name", "Denon AVR Proxy")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rx>
  <cmd id="1">
    <friendlyname>{name}</friendlyname>
  </cmd>
</rx>"""


def _parse_appcommand_request(body_bytes: bytes) -> list[tuple[str, str]]:
    """
    Parse AppCommand request body. denonavr sends multiple <tx> chunks (max 5
    cmds each); ET.fromstring fails on multiple roots. Extract each tx and parse.
    """
    cmds_requested = []
    body_str = body_bytes.decode("utf-8", errors="ignore")
    # Find all <tx>...</tx> blocks (denonavr chunks commands into multiple tx)
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
    config: dict, state: Optional["AVRState"], body_bytes: bytes
) -> bytes:
    """
    Build AppCommand.xml response from request body. Parses requested commands
    and returns XML with power, volume, mute, source, sound mode from state.
    """
    power = (state.power if state and state.power else "ON") or "ON"
    volume = (state.volume if state and state.volume else "50") or "50"
    mute_val = "on" if (state and state.mute) else "off"
    input_src = (state.input_source if state and state.input_source else "CD") or "CD"
    friendly_name = config.get("ssdp_friendly_name", "Denon AVR Proxy")
    sound_mode = "STEREO"

    cmds_requested = _parse_appcommand_request(body_bytes)
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
                '</listvalue></list></cmd>'
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
        else:
            cmd_responses.append(f'  <cmd id="{cid}" cmd_text="{ct}"></cmd>')

    xml_str = (
        '<?xml version="1.0" encoding="utf-8"?>\n<rx>\n'
        + "\n".join(cmd_responses)
        + "\n</rx>"
    )
    return xml_str.encode("utf-8")


def _mainzone_xml(state: Optional["AVRState"]) -> bytes:
    """
    Build MainZone XML for denonavr status polling. Demo values so HA integration
    looks fully fleshed out (sources, sound mode, etc.).
    """
    power = (state.power if state and state.power else "ON") or "ON"
    volume = (state.volume if state and state.volume else "50") or "50"
    mute_val = "on" if (state and state.mute) else "off"
    input_src = (state.input_source if state and state.input_source else "CD") or "CD"
    # FuncNames for InputFuncList (must match Deviceinfo sources)
    func_names = [fn for fn, _ in _DEMO_SOURCES]
    input_func_list = "\n".join(f"    <Value>{fn}</Value>" for fn in func_names)
    rename_source = "\n".join(f"    <Value>{fn}</Value>" for fn in func_names)
    source_delete = "\n".join("    <Value>USE</Value>" for _ in func_names)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<item>
  <Power><value>{power}</value></Power>
  <ZonePower><value>{power}</value></ZonePower>
  <MasterVolume><value>{volume}</value></MasterVolume>
  <Mute><value>{mute_val}</value></Mute>
  <InputFuncSelect><value>{input_src}</value></InputFuncSelect>
  <selectSurround><value>STEREO</value></selectSurround>
  <SurrMode><value>STEREO</value></SurrMode>
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


def _device_description_xml(config: dict, advertise_ip: str) -> str:
    """Minimal UPnP device description XML matching what Home Assistant expects."""
    friendly_name = config.get("ssdp_friendly_name", "Denon AVR Proxy")
    http_port = config.get("ssdp_http_port", 8080)
    serial = f"proxy-{advertise_ip.replace('.', '-')}"
    # xmlns required so denonavr's evaluate_scpd_xml finds device (port 60006)
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


def _parse_msearch_st(msg: str) -> Optional[str]:
    """Extract ST (Search Target) from M-SEARCH request."""
    for line in msg.split("\r\n"):
        if line.upper().startswith("ST:"):
            return line[3:].strip()
    return None


def _ssdp_response(config: dict, advertise_ip: str, st: str) -> bytes:
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
        self._advertise_ip = _get_advertise_ip(config)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if not self._advertise_ip:
            return
        try:
            msg = data.decode("utf-8", errors="ignore")
            if "M-SEARCH" not in msg:
                return
            st = _parse_msearch_st(msg)
            if not st or not any(m in st for m in self.MATCH_ST):
                return
            resp = _ssdp_response(self.config, self._advertise_ip, st)
            if self.transport:
                self.transport.sendto(resp, addr)
            self.logger.debug("SSDP response sent to %s (ST=%s)", addr, st[:50])
        except Exception as e:
            self.logger.debug("SSDP error: %s", e)


class DeviceDescriptionHandler(asyncio.Protocol):
    """
    Serves HTTP for both SSDP (description.xml) and denonavr (Deviceinfo, AppCommand).
    Enables Home Assistant manual add and SSDP discovery.
    """

    def __init__(
        self,
        description_xml: bytes,
        deviceinfo_xml: bytes,
        appcommand_xml: bytes,
        logger: logging.Logger,
        state: Optional["AVRState"] = None,
        config: Optional[dict] = None,
    ) -> None:
        self.description_xml = description_xml
        self.deviceinfo_xml = deviceinfo_xml
        self.appcommand_xml = appcommand_xml
        self.logger = logger
        self.state = state
        self.config = config or {}
        self._buffer = b""

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def data_received(self, data: bytes) -> None:
        self._buffer += data
        # Wait for headers
        if b"\r\n\r\n" not in self._buffer:
            return
        headers_end = self._buffer.index(b"\r\n\r\n")
        headers = self._buffer[:headers_end].decode("utf-8", errors="ignore")
        body_bytes = self._buffer[headers_end + 4:]

        # For POST, wait for full body (body can arrive in separate packet)
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
                self.transport.close()
                return
            req_line = lines[0]
            parts = req_line.split()
            if len(parts) < 2:
                self.logger.debug("HTTP: malformed request line: %r", req_line[:100])
                self.transport.close()
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
                    # Port 60006: denonavr fetches device info for AVR-X 2016
                    body = self.description_xml
                elif "mainzonexmlstatus" in path_lower or "mainzonexml" in path_lower:
                    # denonavr polls these for power, volume, mute, input
                    body = _mainzone_xml(self.state)
            elif method == "POST" and "/goform/appcommand.xml" in path_lower:
                body = _appcommand_response_xml(
                    self.config, self.state, body_bytes
                )

            if body is not None:
                resp = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: " + content_type + b"\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                    b"Connection: close\r\n\r\n"
                ) + body
                self.transport.write(resp)
                self.logger.debug("HTTP: %s %s -> 200 OK", method, path)
            else:
                if path_lower == "/ws":
                    # Not handling WebSocket requests is fine, don't warn
                    self.logger.debug("HTTP: %s %s -> no handler", method, path)
                else:
                    self.logger.warning("HTTP: %s %s -> no handler", method, path)
        except Exception as e:
            self.logger.warning("HTTP handler error: %s", e)
        self.transport.close()


async def run_ssdp_server(
    config: dict, logger: logging.Logger, state: Optional["AVRState"] = None
) -> tuple:
    """Start SSDP (UDP 1900) and HTTP device description server. Returns (ssdp_transport, http_server)."""
    if not config.get("enable_ssdp"):
        return None, None

    advertise_ip = _get_advertise_ip(config)
    if not advertise_ip:
        logger.warning("SSDP: Could not determine IP to advertise. Set ssdp_advertise_ip in config.")
        return None, None

    logger.info("SSDP advertising as %s at %s", config.get("ssdp_friendly_name"), advertise_ip)

    ssdp_transport = None
    try:
        import socket
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
    desc_xml = _device_description_xml(config, advertise_ip).encode("utf-8")
    devinfo_xml = _deviceinfo_xml(config).encode("utf-8")
    appcmd_xml = _appcommand_friendlyname_xml(config).encode("utf-8")

    def http_factory():
        return DeviceDescriptionHandler(
            desc_xml, devinfo_xml, appcmd_xml, logger, state, config
        )

    http_servers = []
    # denonavr uses: port 80/8080 for Deviceinfo, then 60006 for device info (AVR-X 2016)
    # Bind 80, 8080, and 60006 so we pass all checks
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

    if not http_servers:
        if ssdp_transport:
            ssdp_transport.close()
        return None, None

    logger.info("Device description at http://%s:%d/description.xml", advertise_ip, http_port)
    return ssdp_transport, http_servers


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------

async def main_async(config: dict) -> None:
    """Run the proxy server."""
    logger = logging.getLogger("denon-proxy")
    proxy = DenonProxyServer(config, logger)

    # Handle shutdown gracefully - must not block event loop or Ctrl-C won't work
    stop_event = asyncio.Event()

    def shutdown():
        stop_event.set()

    _signal_handlers_set = False
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown)
        _signal_handlers_set = True
    except NotImplementedError:
        # add_signal_handler not supported on Windows - use signal.signal
        try:
            signal.signal(signal.SIGINT, lambda s, f: shutdown())
            signal.signal(signal.SIGTERM, lambda s, f: shutdown())
        except (ValueError, OSError):
            pass
    except OSError:
        # add_signal_handler can fail (e.g. main thread check) - try signal.signal
        try:
            signal.signal(signal.SIGINT, lambda s, f: shutdown())
            signal.signal(signal.SIGTERM, lambda s, f: shutdown())
        except (ValueError, OSError):
            pass

    await proxy.start()

    # Optional SSDP
    ssdp_transport, http_server = None, None
    if config.get("enable_ssdp"):
        try:
            ssdp_transport, http_server = await run_ssdp_server(config, logger, proxy.state)
        except Exception as e:
            logger.warning("SSDP failed: %s", e)

    await stop_event.wait()

    if ssdp_transport:
        ssdp_transport.close()
    if http_server:
        servers = http_server if isinstance(http_server, list) else [http_server]
        for srv in servers:
            srv.close()
            await srv.wait_closed()
    await proxy.stop()


def main() -> int:
    """Parse arguments and run the proxy."""
    parser = argparse.ArgumentParser(
        description="Denon AVR Proxy - Virtual AVR for multiple clients"
    )
    parser.add_argument(
        "--config", "-c",
        type=Path,
        default=None,
        help="Path to config YAML (default: config.yaml in project dir)",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    setup_logging(config["log_level"])

    try:
        asyncio.run(main_async(config))
    except KeyboardInterrupt:
        pass
    except ImportError as e:
        if "yaml" in str(e).lower():
            print("Install PyYAML: pip install pyyaml", file=sys.stderr)
        else:
            print(f"Import error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
