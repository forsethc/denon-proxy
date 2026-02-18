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
import os
import signal
import sys
from pathlib import Path
from typing import Callable, Optional, Set, Union

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

from avr_emulator import (
    AVRConnection,
    AVRState,
    VirtualAVRConnection,
    _volume_to_level,
    create_avr_connection,
    get_advertise_ip,
    run_emulator_servers,
)

from web_ui import run_json_api

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
        "avr_host": "",
        "avr_port": 23,
        "proxy_host": "0.0.0.0",
        "proxy_port": 23,
        "log_level": "INFO",
        "enable_ssdp": True,
        "ssdp_friendly_name": "Denon AVR Proxy",
        "ssdp_http_port": 8080,
        "ssdp_advertise_ip": "",
        "optimistic_state": True,
        "optimistic_broadcast_delay": 0.1,
        "enable_web_ui": False,
        "web_ui_port": 8081,
        "log_command_groups_info": [],  # e.g. ["power", "volume"] - these groups logged at INFO
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
    prefix = cmd[:2].upper()
    if prefix == "MV" and len(cmd) > 2 and "MAX" in cmd.upper():
        return "other"  # MVMAX is config, not state
    return _COMMAND_GROUPS.get(prefix, "other")


def _should_log_command_info(config: dict, cmd: str) -> bool:
    """True if this command's group is configured for INFO-level logging."""
    groups = config.get("log_command_groups_info") or []
    if not groups:
        return False
    return _command_group(cmd) in groups


def _parse_client_data(buffer: bytes, data: bytes) -> tuple[list[str], bytes]:
    """
    Decode incoming client bytes into complete telnet command lines.

    Returns (list_of_commands, remaining_buffer).
    """
    buffer += data
    commands: list[str] = []
    while b"\r" in buffer or b"\n" in buffer:
        for sep in (b"\r", b"\n"):
            if sep in buffer:
                line, _, buffer = buffer.partition(sep)
                break
        else:
            break
        try:
            cmd = line.decode("utf-8").strip()
        except UnicodeDecodeError:
            continue
        if cmd:
            commands.append(cmd)
    return commands, buffer


def _is_valid_client_command(command: str) -> bool:
    """
    Basic validation for Denon telnet commands from clients.

    Denon commands are typically a 2+ letter prefix plus optional value.
    Filters out telnet negotiation bytes and obviously invalid input.
    """
    if len(command) < 2:
        return False
    # Filter out telnet negotiation bytes if any leak through
    if any(ord(c) < 32 and c not in "\r\n\t" for c in command):
        return False
    return True


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
        avr: Union[AVRConnection, VirtualAVRConnection],
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
        self.config.get("_notify_web_state", lambda: None)()

        # Send current state to new client
        status = self.state.get_status_dump()
        if status and self.transport:
            self.transport.write(status.encode("utf-8"))

    def data_received(self, data: bytes) -> None:
        """Handle data from client - parse commands and forward to AVR."""
        commands, self._buffer = _parse_client_data(self._buffer, data)
        for cmd in commands:
            self._handle_command(cmd)

    def _handle_command(self, command: str) -> None:
        """Process and forward a client command to the AVR."""
        if not _is_valid_client_command(command):
            return

        client_ip = self._peername[0] if self._peername else "?"
        if _should_log_command_info(self.config, command):
            self.logger.info("Client %s command: %s", client_ip, command)
        else:
            self.logger.debug("Client %s command: %s", client_ip, command)
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
        had_connection = self.avr.is_connected()

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
            # VirtualAVR already sends via on_response; skip duplicate to avoid confusing HA
            if not isinstance(self.avr, VirtualAVRConnection):
                self._broadcast_state()
            self.config.get("_notify_web_state", lambda: None)()

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
        self.config.get("_notify_web_state", lambda: None)()


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
        config: dict,
        logger: logging.Logger,
        avr_factory: Callable[
            [dict, AVRState, Callable[[str], None], Callable[[], None], logging.Logger],
            Union[AVRConnection, VirtualAVRConnection],
        ],
    ) -> None:
        self.config = config
        self.logger = logger
        self.state = AVRState()
        self.clients: Set[ClientHandler] = set()
        self.avr: Optional[Union[AVRConnection, VirtualAVRConnection]] = None
        self._server: Optional[asyncio.Server] = None
        self._json_api_server: Optional[asyncio.Server] = None
        self._notify_web_state: Callable[[], None] = lambda: None
        self._avr_factory = avr_factory

    def _set_state_and_broadcast(self, payload: dict) -> None:
        """Set state from payload (e.g. from JSON API) and broadcast to clients."""
        s = self.state
        if "power" in payload:
            v = payload["power"]
            s.power = str(v).upper() if v else None
        if "volume" in payload:
            v = payload["volume"]
            s.volume = str(v) if v is not None else None
        if "input_source" in payload:
            v = payload["input_source"]
            s.input_source = str(v) if v is not None else None
        if "mute" in payload:
            s.mute = bool(payload["mute"])
        if "sound_mode" in payload:
            v = payload["sound_mode"]
            s.sound_mode = str(v) if v is not None else None
        status = s.get_status_dump()
        if status:
            for line in status.strip().splitlines():
                if line.strip():
                    self._broadcast(line.strip())
        self._notify_web_state()

    def _broadcast(self, message: str) -> None:
        """Broadcast an AVR response to all connected clients."""
        client_list = list(self.clients)
        for client in client_list:
            client.broadcast(message)
        if client_list:
            if _should_log_command_info(self.config, message):
                self.logger.info("Broadcast to %d client(s): %s", len(client_list), message)
            elif self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug("Broadcast to %d client(s): %s", len(client_list), message)

    def _on_avr_response(self, message: str) -> None:
        """Called when the AVR sends a response."""
        if _should_log_command_info(self.config, message):
            self.logger.info("AVR response: %s", message)
        elif self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug("AVR response: %s", message)
        self._broadcast(message)
        # HA denonavr only processes ZM (not PW) for telnet updates; broadcast ZM equivalent for power
        # so the UI updates without needing an integration reload. ZMSTANDBY uses parameter "STANDBY"
        # which denonavr's _power_callback accepts; ZMOFF may use "OFF" which some versions reject.
        if message == "PWON":
            self._broadcast("ZMON")
        elif message in ("PWSTANDBY", "PWSTANDBY ") or "STANDBY" in message.upper():
            self._broadcast("ZMSTANDBY")
            self._broadcast("ZMOFF")  # Some receivers/denonavr expect ZMOFF
        self._notify_web_state()

    def _on_avr_disconnect(self) -> None:
        """Called when AVR disconnects - schedule reconnect."""
        async def reconnect():
            await asyncio.sleep(2)
            if self.avr:
                await self.avr.connect()
                if self.avr.is_connected():
                    await self.avr.request_state()

        asyncio.create_task(reconnect())

    async def _sync_initial_state(self) -> None:
        """Use denonavr HTTP API to fetch initial state and device sources (if available)."""
        if not (self.config.get("avr_host") or "").strip() or not DENONAVR_AVAILABLE:
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
            # Fetch device info and sources for JSON API and source resolution
            self.config["_avr_info"] = {
                "manufacturer": getattr(d, "manufacturer", None),
                "model_name": getattr(d, "model_name", None),
                "serial_number": getattr(d, "serial_number", None),
                "friendly_name": getattr(d, "name", None),
            }
            rev = getattr(d.input, "_input_func_map_rev", None)
            if rev and isinstance(rev, dict):
                self.config["_device_sources"] = [
                    (func, str(display or func)) for func, display in rev.items()
                ]
                self.logger.info("Fetched %d input sources from AVR", len(self.config["_device_sources"]))
            self.logger.info("Initial state from HTTP: power=%s vol=%s input=%s mute=%s",
                             self.state.power, self.state.volume,
                             self.state.input_source, self.state.mute)
        except Exception as e:
            self.logger.debug("Could not sync initial state via HTTP: %s", e)

    async def start(self) -> None:
        """Start the proxy server and AVR connection."""
        if (self.config.get("avr_host") or "").strip():
            await self._sync_initial_state()

        self.avr = self._avr_factory(
            self.config,
            self.state,
            self._on_avr_response,
            self._on_avr_disconnect,
            self.logger,
        )
        connected = await self.avr.connect()
        if connected:
            await asyncio.sleep(0.3)
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
        connect_host = get_advertise_ip(self.config) or (host if host and host != "0.0.0.0" else "localhost")
        avr_host = (self.config.get("avr_host") or "").strip()
        if avr_host:
            self.logger.info("Proxy listening on %s:%d (AVR: %s:%d)",
                             connect_host, port, avr_host, self.config.get("avr_port", 23))
        else:
            self.logger.info("Proxy listening on %s:%d (virtual AVR)", connect_host, port)
        self.logger.info("Connect Home Assistant and UC Remote 3 to %s:%d", connect_host, port)

        # Web UI / JSON status API
        def _get_json_state() -> dict:
            clients = [c._peername[0] if c._peername else "?" for c in list(self.clients)]
            state = {
                k: v
                for k, v in vars(self.state).items()
                if not k.startswith("_")
            }
            if "volume" in state and state["volume"] is not None:
                state["volume"] = _volume_to_level(state["volume"])
            avr = dict(self.avr.get_details()) if self.avr else {"type": "none"}
            avr["connected"] = self.avr.is_connected() if self.avr else False
            avr_info = self.config.get("_avr_info") or {}
            if avr_info:
                avr["manufacturer"] = avr_info.get("manufacturer")
                avr["model_name"] = avr_info.get("model_name")
                avr["serial_number"] = avr_info.get("serial_number")
                avr["friendly_name"] = avr_info.get("friendly_name")
            sources = self.config.get("_resolved_sources") or self.config.get("_device_sources")
            if sources:
                avr["sources"] = [{"func": f, "display": n} for f, n in sources]
            return {
                "avr": avr,
                "clients": clients,
                "client_count": len(clients),
                "state": state,
            }

        # Only allow set_state when using VirtualAVR (no physical AVR)
        set_state_cb = self._set_state_and_broadcast if not (self.config.get("avr_host") or "").strip() else None

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

        result = await run_json_api(
            self.config, self.logger, _get_json_state,
            set_state=set_state_cb,
            send_command=_send_command_cb,
            request_state=_request_state_cb,
        )
        if result:
            self._json_api_server, self._notify_web_state = result
            self.config["_notify_web_state"] = self._notify_web_state
            api_host = get_advertise_ip(self.config) or "localhost"
            api_port = int(self.config.get("web_ui_port", 8081))
            self.logger.info(
                "Web UI at http://%s:%d (dashboard, JSON API, commands)",
                api_host, api_port,
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

async def main_async(config: dict) -> None:
    """Run the proxy server."""
    logger = logging.getLogger("denon-proxy")
    proxy = DenonProxyServer(config, logger, create_avr_connection)

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
            ssdp_transport, http_server = await run_emulator_servers(config, logger, proxy.state)
        except Exception as e:
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
                await asyncio.wait_for(srv.wait_closed(), timeout=2.0)
        await asyncio.wait_for(proxy.stop(), timeout=5.0)
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
