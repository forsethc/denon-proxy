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

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import pprint
import signal
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable, Set

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

import httpx

from denon_proxy.avr.connection import AVRConnection, VirtualAVRConnection, create_avr_connection
from denon_proxy.avr.discovery import get_advertise_ip, run_discovery_servers
from pydantic import ValidationError
from denon_proxy.runtime.config import Config, DEFAULT_AVR_PORT, DEFAULT_HTTP_PORT, DEFAULT_PROXY_PORT, DEFAULT_SSDP_HTTP_PORT
from denon_proxy.constants import (
    DENONAVR_SYNC_TIMEOUT,
    POST_CONNECT_DELAY,
    RECONNECT_DELAY,
    SHUTDOWN_PROXY_WAIT,
    SHUTDOWN_SERVER_WAIT,
)
from denon_proxy.avr.info import AVRInfo
from denon_proxy.runtime.state import RuntimeState
from denon_proxy.utils.utils import get_version, is_docker_internal_ip, is_running_in_docker, resolve_listening_port
from denon_proxy.avr.state import AVRState, volume_to_level
from denon_proxy.avr.telnet_utils import parse_telnet_lines, telnet_line_to_bytes

from denon_proxy.http.server import run_http_server


# -----------------------------------------------------------------------------
# Logging setup
# -----------------------------------------------------------------------------

def setup_logging(level: str = "INFO", denonavr_log_level: str | None = None) -> None:
    """Configure logging format and level. denonavr_log_level sets the denonavr library logger separately.
    When stdout is not a TTY (e.g. systemd captures it), we use a format without timestamp/name
    so journal/syslog doesn't duplicate them."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    if sys.stdout.isatty():
        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"
    else:
        fmt = "[%(levelname)s] %(message)s"
        datefmt = None
    logging.basicConfig(
        level=log_level,
        format=fmt,
        datefmt=datefmt,
    )
    # Reduce noise from libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if denonavr_log_level is not None:
        logging.getLogger("denonavr").setLevel(
            getattr(logging, denonavr_log_level.upper(), logging.INFO)
        )


def _load_config_dict_from_file(config_path: Path | None) -> dict:
    """
    Load raw config data (dict) from YAML file.

    Does not apply defaults or environment overrides so it can be tested
    separately from I/O.
    """
    if yaml is None:
        raise ImportError("PyYAML is required. Install with: pip install pyyaml")

    if config_path:
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        path = config_path
    else:
        # Default: config.yaml in current working directory (project root when run from there)
        path = Path.cwd() / "config.yaml"
        if not path.exists():
            raise FileNotFoundError(
                f"Config not found: {path}\n"
                "Copy config.sample.yaml to config.yaml in the project root and edit as needed."
            )

    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a mapping, got {type(data).__name__}")
    return data


def _load_dashboard_html(path: Path | None = None) -> str | None:
    """
    Load the Web UI HTML dashboard from http/web_ui.html.

    Returns the HTML string, or None if the file is missing or unreadable.
    path: optional path for tests; when None, uses http/web_ui.html in this package.
    """
    html_path = path if path is not None else Path(__file__).parent / "http" / "web_ui.html"
    try:
        return html_path.read_text(encoding="utf-8")
    except OSError:
        return None


def load_config_from_dict(raw: dict | None) -> Config:
    """
    Merge defaults with a raw config dict (no file I/O).

    This is pure and easy to unit-test.
    Environment overrides are not applied here; tests can control env explicitly
    via Config.load_from_dict if needed.
    """
    return Config.load_from_dict(raw)


def load_config(config_path: Path | None = None) -> Config:
    """Load configuration from YAML file with environment overrides."""
    raw = _load_config_dict_from_file(config_path)
    # Apply environment overrides during model construction
    return Config.load_from_dict(raw)


# Denon telnet command groups for configurable logging
_COMMAND_GROUPS = {
    "PW": "power",
    "ZM": "power",
    "MV": "volume",
    "SI": "input",
    "MU": "mute",
    "MS": "sound_mode",
}


def _command_group(cmd: str) -> str:
    """Return the group name for a Denon telnet command (e.g. PWON -> power)."""
    if not cmd or len(cmd) < 2:
        return "other"
    # MSSMART is Smart Select slot, not sound mode; must check before MS
    if cmd.upper().startswith("MSSMART"):
        return "smart_select"
    prefix = cmd[:2].upper()
    if prefix == "MV" and len(cmd) > 2 and "MAX" in cmd.upper():
        return "other"  # MVMAX is config, not state
    return _COMMAND_GROUPS.get(prefix, "other")


def _should_log_command_info(config: Config, cmd: str) -> bool:
    """True if this command's group is configured for INFO-level logging."""
    groups = config.get("log_command_groups_info") or []
    if not groups:
        return False
    return _command_group(cmd) in groups


def _client_ip_for_display(client: ClientHandler) -> str:
    """Return the client's IP string for status display, or '?' if unknown."""
    peername = getattr(client, "_peername", None)
    return peername[0] if peername else "?"


def build_json_state(
    avr_state: AVRState,
    avr: (AVRConnection | VirtualAVRConnection) | None,
    clients: Iterable[ClientHandler],
    config: Config,
    runtime_state: RuntimeState,
    client_activity_log: dict[str, Iterable[tuple[float, str]]] | None = None,
) -> dict:
    """
    Build JSON status dict for Web UI / SSE.

    Pure function for testability. Caller passes avr_state, avr, clients, config, and runtime_state.
    client_activity_log: optional dict client_id -> iterable of (timestamp, command) for UI log.
    """
    client_ips = [_client_ip_for_display(c) for c in list(clients)]
    state_dict = {
        k: v
        for k, v in vars(avr_state).items()
        if not k.startswith("_")
    }
    if "volume" in state_dict and state_dict["volume"] is not None:
        vol_max = getattr(avr_state, "volume_max", 98.0)
        state_dict["volume"] = volume_to_level(state_dict["volume"], vol_max)
    avr_dict = dict(avr.get_details()) if avr else {"type": "none"}
    avr_dict["connected"] = avr.is_connected() if avr else False
    avr_dict["volume_max"] = getattr(avr_state, "volume_max", 98.0)
    avr_dict["manufacturer"] = runtime_state.avr_info.manufacturer
    avr_dict["model_name"] = runtime_state.avr_info.model_name
    avr_dict["serial_number"] = runtime_state.avr_info.serial_number
    avr_dict["friendly_name"] = runtime_state.avr_info.raw_friendly_name
    sources = runtime_state.resolved_sources or runtime_state.avr_info.raw_sources
    if sources:
        avr_dict["sources"] = [{"func": func_name, "display_name": display_name} for func_name, display_name in sources]
    proxy_ip = get_advertise_ip(config) or None
    http_port = runtime_state.get_resolved_port(config, "ssdp_http_port", DEFAULT_SSDP_HTTP_PORT)
    discovery = {
        "http_port": int(http_port),
        "enabled": bool(config.get("enable_ssdp", False)),
        "proxy_ip": proxy_ip,
        "proxy_ip_is_internal": is_docker_internal_ip(proxy_ip),
        "is_docker": is_running_in_docker(),
    }
    client_aliases = config.get("client_aliases") or {}
    if not isinstance(client_aliases, dict):
        client_aliases = {}

    # Serialize activity log for UI: list of [timestamp, command] per client.
    # Query filtering happens at storage time (record_command) when hide_queries is on, not here.
    log_for_json: dict[str, list[list[float | str]]] = {}
    if client_activity_log:
        for cid, entries in client_activity_log.items():
            log_for_json[cid] = [[ts, cmd] for ts, cmd in entries]

    activity_log_enabled = bool(config.get("client_activity_log", True))
    return {
        "friendly_name": runtime_state.get_friendly_name(config),
        "avr": avr_dict,
        "clients": client_ips,
        "client_count": len(client_ips),
        "client_aliases": client_aliases,
        "client_activity_log": log_for_json,
        "client_activity_log_enabled": activity_log_enabled,
        "state": state_dict,
        "discovery": discovery,
        "version": runtime_state.version,
    }


def state_and_config_updates_from_denonavr(d: Any) -> tuple[dict[str, Any], AVRInfo]:
    """
    Extract state updates and AVR identity/capabilities from a denonavr instance.
    Returns (state_updates, avr_info). Caller applies to state.
    """
    state_updates: dict[str, Any] = {}

    # --- Power, volume, input, mute (direct mapping) ---
    if getattr(d, "power", None):
        state_updates["power"] = d.power
    vol = getattr(d, "vol", None)
    if vol is not None and getattr(vol, "volume", None) is not None:
        # denonavr gives volume in dB (e.g. -36.5); Denon telnet uses 0-98. Approximate: 80 = 0 dB.
        vol_db = vol.volume
        vol_int = max(0, min(98, int(80 + vol_db * 2)))
        state_updates["volume"] = str(vol_int)
    if getattr(d, "input_func", None):
        state_updates["input_source"] = d.input_func
    if getattr(d, "muted", None) is not None:
        state_updates["mute"] = d.muted

    # --- Sound mode vs Smart Select: HTTP sometimes returns "SMART0" as sound_mode ---
    raw_sound_mode = getattr(d, "sound_mode", None)
    if raw_sound_mode:
        raw = str(raw_sound_mode).strip().upper()
        if raw.startswith("SMART") and len(raw) > 5 and raw[5:].isdigit():
            state_updates["smart_select"] = raw_sound_mode
            state_updates["sound_mode"] = None  # leave for telnet MS? to fill real mode
        else:
            state_updates["sound_mode"] = raw_sound_mode
    if getattr(d, "smart_select", None) is not None:
        state_updates["smart_select"] = str(d.smart_select)

    # --- Device metadata and input sources for AVRInfo ---
    rev = getattr(getattr(d, "input", None), "_input_func_map_rev", None)
    raw_sources: list[tuple[str, str]] = []
    if rev and isinstance(rev, dict):
        raw_sources = [(func, str(display_name or func)) for func, display_name in rev.items()]

    avr_info = AVRInfo(
        manufacturer=getattr(d, "manufacturer", None),
        model_name=getattr(d, "model_name", None),
        serial_number=getattr(d, "serial_number", None),
        raw_friendly_name=getattr(d, "name", None),
        raw_sources=raw_sources,
    )
    return state_updates, avr_info


def avr_response_broadcast_lines(message: str) -> list[str]:
    """
    Return the list of lines to broadcast for an AVR response.
    HA denonavr only processes ZM (not PW) for telnet updates; we add ZM equivalents
    for power so the UI updates without needing an integration reload.
    """
    lines = [message]
    if message == "PWON":
        lines.append("ZMON")
    elif message in ("PWSTANDBY", "PWSTANDBY ") or "STANDBY" in message.upper():
        lines.extend(["ZMSTANDBY", "ZMOFF"])
    return lines


def _is_valid_client_command(command: str) -> tuple[bool, str | None]:
    """
    Basic validation for Denon telnet commands from clients.

    Denon commands are typically a 2+ letter prefix plus optional value.
    Filters out telnet negotiation bytes and obviously invalid input.
    """
    if len(command) < 2:
        return False, "Command too short"
    # Filter out telnet negotiation bytes if any leak through
    if any(ord(c) < 32 and c not in "\r\n\t" for c in command):
        return False, "Command contains telnet negotiation bytes"
    return True, None


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
        avr: AVRConnection | VirtualAVRConnection,
        avr_state: AVRState,
        clients: Set["ClientHandler"],
        logger: logging.Logger,
        runtime_state: RuntimeState,
        config: Config,
        record_command: Callable[[str, str], None] | None = None,
    ) -> None:
        self.avr = avr
        self.avr_state = avr_state
        self.clients = clients
        self.logger = logger
        self.config = config
        self.runtime_state = runtime_state
        self.record_command = record_command or (lambda _cid, _cmd: None)
        self.transport: asyncio.Transport | None = None
        self._buffer = b""
        self._peername: tuple | None = None

    def _notify_web(self) -> None:
        self.runtime_state.notify_web_state()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Called when a client connects."""
        self.transport = transport
        self._peername = transport.get_extra_info("peername")
        self.clients.add(self)
        client_display = self.config.client_display_for_log(
            self._peername[0] if self._peername else "?"
        )
        self.logger.info("Client connected: %s (total: %d)", client_display, len(self.clients))
        client_ip = self._peername[0] if self._peername else "?"
        self.record_command(client_ip, "[connected]")
        self._notify_web()

        # Send current state to new client
        status = self.avr_state.get_status_dump()
        if status and self.transport:
            self.transport.write(status.encode("utf-8"))

    def data_received(self, data: bytes) -> None:
        """Handle data from client - parse commands and forward to AVR."""
        commands, self._buffer = parse_telnet_lines(self._buffer, data)
        for cmd in commands:
            self._handle_command(cmd)

    def _handle_command(self, command: str) -> None:
        """Process and forward a client command to the AVR."""
        valid, err = _is_valid_client_command(command)
        if not valid:
            client_ip = self._peername[0] if self._peername else "?"
            client_display = self.config.client_display_for_log(client_ip)
            self.logger.debug(
                "Rejected invalid command from %s: %r (%s)",
                client_display,
                command,
                err,
            )
            return

        client_ip = self._peername[0] if self._peername else "?"
        self.record_command(client_ip, command)
        client_display = self.config.client_display_for_log(client_ip)
        if _should_log_command_info(self.config, command):
            self.logger.info("Client %s command: %s", client_display, command)
        else:
            self.logger.debug("Client %s command: %s", client_display, command)
        asyncio.create_task(self._handle_command_async(command))

    def _broadcast_state(self) -> None:
        """Broadcast current state to all clients (emulates AVR confirmation)."""
        status = self.avr_state.get_status_dump()
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
        had_connection = self.avr.is_connected()

        if optimistic:
            snapshot = self.avr_state.snapshot()
            if not self.avr_state.apply_command(
                command,
                volume_step=float(self.config["volume_step"]),
            ):
                snapshot = None  # command didn't change state, nothing to revert

        send_task = asyncio.create_task(self.avr.send_command(command))
        if delay > 0:
            await asyncio.sleep(delay)
        success = await send_task

        if optimistic and snapshot is not None and not success and had_connection:
            self.avr_state.restore(snapshot)
            self.logger.warning("Reverted optimistic state after failed send")

        if optimistic and snapshot is not None:
            self._broadcast_state()
            self._notify_web()

        # When not optimistic, clients get updated when the AVR sends a response.

        # After relative volume commands, query AVR so we update internal state from its response
        cmd_upper = command.strip().upper()
        if success and cmd_upper in ("MVUP", "MVDOWN"):
            delay = max(0.0, float(self.config.get("volume_query_delay", 0.15)))
            await asyncio.sleep(delay)
            await self.avr.send_command("MV?")

    def broadcast(self, message: str) -> None:
        """Send a message to this client."""
        if self.transport and not self.transport.is_closing():
            try:
                self.transport.write(telnet_line_to_bytes(message))
            except OSError as e:
                self.logger.debug("Broadcast to client failed: %s", e)

    def connection_lost(self, exc: Exception | None) -> None:
        """Called when client disconnects."""
        client_ip = self._peername[0] if self._peername else "?"
        self.record_command(client_ip, "[disconnected]")
        self.clients.discard(self)
        self.transport = None
        client_display = self.config.client_display_for_log(client_ip)
        self.logger.info("Client disconnected: %s (remaining: %d)", client_display, len(self.clients))
        self._notify_web()


# -----------------------------------------------------------------------------
# Proxy server
# -----------------------------------------------------------------------------

class DenonProxyServer:
    """
    Main proxy server: accepts client connections and bridges them
    to a single AVR connection.
    """

    def __init__(
        self,
        config: Config,
        logger: logging.Logger,
        avr_factory: Callable[
            [
                dict,
                AVRState,
                Callable[[str], None],
                Callable[[], None],
                logging.Logger,
                Callable[[], None] | None,
            ],
            AVRConnection | VirtualAVRConnection,
        ],
        runtime_state: RuntimeState,
    ) -> None:
        self.config = config
        self.logger = logger
        self.runtime_state = runtime_state
        self.avr_state = AVRState()
        self.clients: Set[ClientHandler] = set()
        self.avr: (AVRConnection | VirtualAVRConnection) | None = None
        self._server: asyncio.Server | None = None
        self._json_api_server: asyncio.Server | None = None
        self._notify_web_state: Callable[[], None] = lambda: None
        self._avr_factory = avr_factory
        self._reconnect_task: asyncio.Task[Any] | None = None
        # Per-client activity log for UI: one deque per client (connect, disconnect, commands).
        self._client_activity_log: dict[str, deque[tuple[float, str]]] = {}
        self._client_activity_log_max = max(
            1, int(self.config.get("client_activity_log_max_entries", 200))
        )

    def record_command(self, client_id: str, command: str) -> None:
        """Record a command or event for a client (for UI activity log). client_id is IP or 'Web UI'. No-op if disabled in config.
        When client_activity_log_hide_queries is true, query commands (ending with ?) are not stored so they cannot evict visible entries."""
        if not self.config.get("client_activity_log", True):
            return
        cid = str(client_id).strip() if client_id else "?"
        msg = command.strip() if isinstance(command, str) else str(command).strip()
        ts = time.time()
        if msg not in ("[connected]", "[disconnected]"):
            if self.config.get("client_activity_log_hide_queries", False) and msg.endswith("?"):
                return  # don't store queries when hide_queries is on; they would only evict visible entries
        if cid not in self._client_activity_log:
            self._client_activity_log[cid] = deque(maxlen=self._client_activity_log_max)
        self._client_activity_log[cid].append((ts, msg))
        self._notify_web_state()

    def _broadcast(self, message: str) -> None:
        """Broadcast an AVR response to all connected clients."""
        client_list = list(self.clients)
        for client in client_list:
            client.broadcast(message)
        if client_list:
            if _should_log_command_info(self.config, message):
                self.logger.info("Broadcast to %d client(s): %s", len(client_list), message)
            else:
                self.logger.debug("Broadcast to %d client(s): %s", len(client_list), message)

    def _on_avr_response(self, message: str) -> None:
        """Called when the AVR sends a response."""
        if _should_log_command_info(self.config, message):
            self.logger.info("AVR response: %s", message)
        else:
            self.logger.debug("AVR response: %s", message)
        for line in avr_response_broadcast_lines(message):
            self._broadcast(line)
        self.runtime_state.notify_web_state()

    def _on_avr_disconnect(self) -> None:
        """Called when AVR disconnects or send attempted while disconnected - schedule reconnect (idempotent)."""
        # Push updated connection status to Web UI / SSE
        self.runtime_state.notify_web_state()
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        async def reconnect() -> None:
            try:
                await asyncio.sleep(RECONNECT_DELAY)
                if self.avr:
                    await self.avr.connect()
                    if self.avr.is_connected():
                        await self.avr.request_state()
                        self.logger.info("Reconnected to AVR")
            finally:
                self._reconnect_task = None
        self._reconnect_task = asyncio.create_task(reconnect())

    async def _sync_initial_state(self) -> None:
        """Use denonavr HTTP API to fetch initial state and device sources (if available)."""
        if not (self.config.get("avr_host") or "").strip() or not DENONAVR_AVAILABLE:
            return
        try:
            d = denonavr.DenonAVR(self.config["avr_host"])
            await asyncio.wait_for(
                d.async_setup(),
                timeout=DENONAVR_SYNC_TIMEOUT,
            )
            await asyncio.wait_for(
                d.async_update(),
                timeout=DENONAVR_SYNC_TIMEOUT,
            )
            state_updates, avr_info = state_and_config_updates_from_denonavr(d)
            self.avr_state.apply_payload(state_updates)
            self.runtime_state.avr_info = avr_info
            if avr_info.has_sources():
                self.logger.info("Fetched %d input sources from AVR", len(avr_info.raw_sources))
            self.logger.info("Initial state from HTTP: power=%s vol=%s input=%s mute=%s sound_mode=%s smart_select=%s",
                             self.avr_state.power, self.avr_state.volume,
                             self.avr_state.input_source, self.avr_state.mute, self.avr_state.sound_mode, self.avr_state.smart_select)
        except (OSError, asyncio.TimeoutError, httpx.HTTPError) as e:
            self.logger.debug("Could not sync initial state via HTTP: %s", e)

    async def start(self) -> None:
        """Start the proxy server and AVR connection."""
        if (self.config.get("avr_host") or "").strip():
            await self._sync_initial_state()
            if self.runtime_state.avr_info is None:
                self.runtime_state.avr_info = AVRInfo.unknown()
        else:
            self.runtime_state.avr_info = AVRInfo.virtual()

        self.avr = self._avr_factory(
            self.config,
            self.avr_state,
            self._on_avr_response,
            self._on_avr_disconnect,
            self.logger,
            self._on_avr_disconnect,  # on_send_while_disconnected: trigger same reconnect
        )
        connected = await self.avr.connect()
        if connected:
            await asyncio.sleep(POST_CONNECT_DELAY)
            await self.avr.request_state()

        # Start proxy server
        def factory():
            return ClientHandler(
                avr=self.avr,
                avr_state=self.avr_state,
                clients=self.clients,
                logger=self.logger,
                runtime_state=self.runtime_state,
                config=self.config,
                record_command=self.record_command,
            )

        host = self.config["proxy_host"]
        port = self.config["proxy_port"]
        self._server = await asyncio.get_running_loop().create_server(
            factory,
            host,
            port,
            reuse_address=True,
        )
        resolve_listening_port(self._server, port, self.runtime_state, "proxy_port")
        connect_host = get_advertise_ip(self.config) or (host if host and host != "0.0.0.0" else "localhost")
        listen_port = self.runtime_state.get_resolved_port(self.config, "proxy_port", DEFAULT_PROXY_PORT)
        avr_host = (self.config.get("avr_host") or "").strip()
        if avr_host:
            self.logger.info("Proxy listening on %s:%d (AVR: %s:%d)",
                             connect_host, listen_port, avr_host, self.config.get("avr_port", DEFAULT_AVR_PORT))
        else:
            self.logger.info("Proxy listening on %s:%d (virtual AVR)", connect_host, listen_port)
        self.logger.info("Connect Home Assistant and UC Remote 3 to %s:%d", connect_host, listen_port)

        # Web UI / JSON status API
        def _get_json_state() -> dict:
            if not self.config.get("client_activity_log", True):
                log_snapshot = {}
            else:
                log_snapshot = {
                    cid: list(entries)[-self._client_activity_log_max :]
                    for cid, entries in self._client_activity_log.items()
                }
            return build_json_state(
                self.avr_state,
                self.avr,
                self.clients,
                self.config,
                self.runtime_state,
                client_activity_log=log_snapshot,
            )

        def _send_command_cb(cmd: str) -> None:
            async def _do() -> None:
                if self.avr:
                    await self.avr.send_command(cmd)
            asyncio.create_task(_do())

        def _request_state_cb() -> None:
            async def _do() -> None:
                if self.avr and self.avr.is_connected():
                    await self.avr.request_state()
            asyncio.create_task(_do())

        enable_http = bool(self.config.get("enable_http", True))

        if enable_http:
            dashboard_html = _load_dashboard_html()
            result = await run_http_server(
                self.config,
                self.logger,
                _get_json_state,
                send_command=_send_command_cb,
                request_state=_request_state_cb,
                dashboard_html=dashboard_html,
                runtime_state=self.runtime_state,
                on_command_sent=self.record_command,
            )
            if result:
                self._json_api_server, self._notify_web_state = result
                self.runtime_state.notify_web_state = self._notify_web_state
                api_host = get_advertise_ip(self.config) or "localhost"
                api_port = self.runtime_state.get_resolved_port(self.config, "http_port", DEFAULT_HTTP_PORT)
                if dashboard_html:
                    self.logger.info(
                        "Web UI at http://%s:%d (dashboard, JSON API, commands)",
                        api_host,
                        api_port,
                    )

    async def stop(self) -> None:
        """Stop the proxy server."""
        # Close all client connections first so server.wait_closed() doesn't hang
        for client in list(self.clients):
            if client.transport and not client.transport.is_closing():
                client.transport.abort()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._json_api_server:
            self._json_api_server.close()
            await self._json_api_server.wait_closed()
            self._json_api_server = None
        if self.avr:
            self.avr.close()
        self.logger.info("Proxy stopped")


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------

async def main_async(config: Config) -> None:
    """Run the proxy server."""
    logger = logging.getLogger("denon-proxy")
    logger.debug(
        "Starting proxy with config:\n%s",
        pprint.pformat(dict(config), width=88, sort_dicts=True),
    )
    runtime_state = RuntimeState()
    runtime_state.version = get_version()
    logger.info("denon-proxy %s", runtime_state.version)
    proxy = DenonProxyServer(config, logger, create_avr_connection, runtime_state)

    # Handle shutdown gracefully - must not block event loop or Ctrl-C won't work
    stop_event = asyncio.Event()
    _shutting_down = False

    def shutdown():
        nonlocal _shutting_down
        if _shutting_down:
            logger.warning("Second Ctrl-C: forcing exit")
            os._exit(1)
        _shutting_down = True
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown)
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

    ssdp_transport, http_server = None, None
    if config.get("enable_ssdp"):
        try:
            ssdp_transport, http_server = await run_discovery_servers(config, logger, proxy.avr_state, runtime_state)
        except (OSError, asyncio.TimeoutError) as e:
            logger.warning("SSDP failed: %s", e)

    await stop_event.wait()

    logger.info("Shutting down...")
    try:
        if ssdp_transport:
            ssdp_transport.close()
        if http_server:
            servers = http_server if isinstance(http_server, list) else [http_server]
            for srv in servers:
                srv.close()
            for srv in servers:
                await asyncio.wait_for(srv.wait_closed(), timeout=SHUTDOWN_SERVER_WAIT)
        await asyncio.wait_for(proxy.stop(), timeout=SHUTDOWN_PROXY_WAIT)
    except asyncio.TimeoutError:
        logger.warning("Shutdown timed out, exiting anyway")


def main() -> int:
    """Parse arguments and run the proxy."""
    parser = argparse.ArgumentParser(
        description="Denon AVR Proxy - Virtual AVR for multiple clients"
    )
    parser.add_argument(
        "--config", "-c",
        type=Path,
        default=None,
        help="Path to config YAML (default: config.yaml in current working directory)",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    except ImportError as e:
        # Handle missing PyYAML (or other imports) during config load.
        if "yaml" in str(e).lower():
            print("PyYAML dependency missing; fix with: pip install -r requirements.txt", file=sys.stderr)
        else:
            print(f"Import error while loading config: {e}", file=sys.stderr)
        return 1
    except ValidationError as e:
        print("Config validation failed:", file=sys.stderr)
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            msg = err.get("msg", "")
            print(f"  {loc}: {msg}", file=sys.stderr)
        return 1
    except Exception as e:
        if yaml is not None and isinstance(e, yaml.YAMLError):
            print("Invalid YAML in config file:", str(e), file=sys.stderr)
            return 1
        raise
    setup_logging(config["log_level"], config.get("denonavr_log_level"))

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
