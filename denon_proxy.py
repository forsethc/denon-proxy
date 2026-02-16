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
import json
import logging
import signal
import sys
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

from avr_emulator import AVRState, get_advertise_ip, run_emulator_servers

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
        "proxy_port": 2323,
        "log_level": "INFO",
        "enable_ssdp": False,
        "ssdp_friendly_name": "Denon AVR Proxy",
        "ssdp_http_port": 8080,
        "ssdp_advertise_ip": "",
        "optimistic_state": True,
        "optimistic_broadcast_delay": 0.1,
        "enable_web_ui": False,
        "web_ui_port": 8081,
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
        demo_mode: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.on_response = on_response
        self.on_disconnect = on_disconnect
        self.state = state
        self.logger = logger
        self.demo_mode = demo_mode
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
            if not self.demo_mode:
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

        client_ip = self._peername[0] if self._peername else "?"
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
        self._json_api_server: Optional[asyncio.Server] = None

    def _broadcast(self, message: str) -> None:
        """Broadcast an AVR response to all connected clients."""
        client_list = list(self.clients)
        for client in client_list:
            client.broadcast(message)
        if client_list and self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug("Broadcast to %d client(s): %s", len(client_list), message)

    def _on_avr_response(self, message: str) -> None:
        """Called when the AVR sends a response."""
        self._broadcast(message)
        # HA denonavr only processes ZM (not PW) for telnet updates; broadcast ZM equivalent for power
        # so the UI updates without needing an integration reload. ZMSTANDBY uses parameter "STANDBY"
        # which denonavr's _power_callback accepts; ZMOFF may use "OFF" which some versions reject.
        if message == "PWON":
            self._broadcast("ZMON")
        elif message in ("PWSTANDBY", "PWSTANDBY ") or "STANDBY" in message.upper():
            self._broadcast("ZMSTANDBY")
            self._broadcast("ZMOFF")  # Some receivers/denonavr expect ZMOFF

    def _on_avr_disconnect(self) -> None:
        """Called when AVR disconnects - schedule reconnect."""
        async def reconnect():
            await asyncio.sleep(2)
            if self.avr:
                await self.avr.connect()
                if self.avr.writer:
                    await self.avr.request_state()

        asyncio.create_task(reconnect())

    def _is_demo_mode(self) -> bool:
        """True if avr_host is empty (no physical AVR)."""
        host = self.config.get("avr_host") or ""
        return not str(host).strip()

    async def _sync_initial_state(self) -> None:
        """Use denonavr HTTP API to fetch initial state (if available)."""
        if self._is_demo_mode() or not DENONAVR_AVAILABLE:
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
        if self._is_demo_mode():
            self.logger.warning(
                "DEMO MODE: avr_host is empty - no physical AVR. "
                "Proxy will accept clients but commands will not be sent to a receiver."
            )
            self.avr = AVRConnection(
                host="",
                port=self.config["avr_port"],
                on_response=self._on_avr_response,
                on_disconnect=self._on_avr_disconnect,
                state=self.state,
                logger=self.logger,
                demo_mode=True,
            )
            # Don't connect - no physical AVR
        else:
            await self._sync_initial_state()

            self.avr = AVRConnection(
                host=self.config["avr_host"],
                port=self.config["avr_port"],
                on_response=self._on_avr_response,
                on_disconnect=self._on_avr_disconnect,
                state=self.state,
                logger=self.logger,
                demo_mode=False,
            )

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
        connect_host = get_advertise_ip(self.config) or (host if host and host != "0.0.0.0" else "localhost")
        if self._is_demo_mode():
            self.logger.info("Proxy listening on %s:%d (DEMO - no AVR)", connect_host, port)
        else:
            self.logger.info("Proxy listening on %s:%d (AVR: %s:%d)",
                             connect_host, port, self.config["avr_host"], self.config["avr_port"])
        self.logger.info("Connect Home Assistant and UC Remote 3 to %s:%d", connect_host, port)

        # JSON status API
        def _get_json_state() -> dict:
            clients = [c._peername[0] if c._peername else "?" for c in list(self.clients)]
            avr_connected = bool(
                self.avr and self.avr.writer and not self.avr.writer.is_closing()
            )
            state = {
                k: v
                for k, v in vars(self.state).items()
                if not k.startswith("_")
            }
            return {
                "demo_mode": self._is_demo_mode(),
                "avr_connected": avr_connected,
                "clients": clients,
                "client_count": len(clients),
                "state": state,
            }

        self._json_api_server = await run_json_api(
            self.config, self.logger, _get_json_state
        )
        if self._json_api_server:
            api_host = get_advertise_ip(self.config) or "localhost"
            api_port = int(self.config.get("web_ui_port", 8081))
            self.logger.info("JSON API at http://%s:%d", api_host, api_port)

    async def stop(self) -> None:
        """Stop the proxy server."""
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

    ssdp_transport, http_server = None, None
    if config.get("enable_ssdp"):
        try:
            ssdp_transport, http_server = await run_emulator_servers(config, logger, proxy.state)
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
