"""
AVR connection layer - physical telnet and in-process virtual AVR.

Provides a single abstraction used by the proxy: AVRConnection (physical device)
or VirtualAVRConnection (no hardware). Both share the same interface so the
proxy does not need to know which is in use.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from denon_proxy.avr.telnet_utils import parse_telnet_lines, telnet_line_to_bytes
from denon_proxy.command_log import should_log_command_info
from denon_proxy.constants import AVR_NETWORK_TIMEOUT, REQUEST_STATE_INTERVAL
from denon_proxy.runtime.config import Config

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from denon_proxy.avr.state import AVRState

# -----------------------------------------------------------------------------
# AVR Connection - telnet connection to physical AVR
# -----------------------------------------------------------------------------


class AVRConnection:
    """
    Maintains a single Telnet connection to a physical Denon AVR.
    Receives responses and forwards them via on_response for broadcasting.

    close(): Releases the connection only; does not call on_disconnect.
    on_disconnect is invoked when the connection is lost (e.g. socket closed
    by peer or I/O error), from _handle_disconnect() when the read loop exits.
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_response: Callable[[str], None],
        on_disconnect: Callable[[], None],
        avr_state: AVRState,
        logger: logging.Logger,
        on_send_while_disconnected: Callable[[], None] | None = None,
        config: Config | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.on_response = on_response
        self.on_disconnect = on_disconnect
        self.on_send_while_disconnected = on_send_while_disconnected
        self.avr_state = avr_state
        self.logger = logger
        self._config: Mapping[str, Any] = config if config is not None else {}
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._buffer = b""

    def is_connected(self) -> bool:
        """Return True if connected to the AVR."""
        return self.writer is not None and not self.writer.is_closing()

    def get_details(self) -> dict[str, Any]:
        """Return details about this AVR connection (physical)."""
        return {"type": "physical", "host": self.host, "port": self.port}

    async def connect(self) -> bool:
        """Establish connection to the AVR. Idempotent if already connected."""
        if self.is_connected():
            return True
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=AVR_NETWORK_TIMEOUT,
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
                messages, self._buffer = parse_telnet_lines(self._buffer, data)
                for msg in messages:
                    payload = msg[2:] if len(msg) > 2 else ""
                    # AVR responses that contain "?" in the payload are invalid
                    # (e.g. buggy echoes like "MSQUICK ?"); don't update state or echo to clients.
                    if not (payload and "?" in payload):
                        self.avr_state.update_from_message(msg)
                        self.on_response(msg)
        except asyncio.CancelledError:
            pass
        except OSError as e:
            self.logger.warning("AVR read error: %s", e)
        finally:
            self._handle_disconnect()

    def _handle_disconnect(self) -> None:
        """Handle AVR disconnect and schedule reconnect."""
        if self.writer:
            try:
                self.writer.close()
                asyncio.create_task(self.writer.wait_closed())
            except OSError as e:
                self.logger.debug("Error closing writer on disconnect: %s", e)
            self.reader = None
            self.writer = None
        self.logger.warning("Disconnected from AVR")
        self.on_disconnect()

    async def send_command(self, command: str) -> bool:
        """Send a telnet command to the AVR."""
        writer = self.writer
        if writer is None or writer.is_closing():
            if self.on_send_while_disconnected:
                self.on_send_while_disconnected()
            self.logger.warning("Cannot send command, AVR not connected: %s", command)
            return False
        try:
            data = telnet_line_to_bytes(command)
            writer.write(data)
            await writer.drain()
            stripped = command.strip()
            if should_log_command_info(self._config, stripped):
                self.logger.info("Sent to AVR: %s", stripped)
            else:
                self.logger.debug("Sent to AVR: %s", stripped)
            return True
        except OSError as e:
            self.logger.warning("Failed to send command to AVR: %s - %s", command, e)
            return False

    async def request_state(self) -> None:
        """Request current state from AVR (power, volume, max volume, input, mute, zone, sound mode, smart select)."""
        for cmd in ("PW?", "MV?", "MVMAX?", "SI?", "MU?", "ZM?", "MS?", "MSSMART ?"):
            await self.send_command(cmd)
            await asyncio.sleep(REQUEST_STATE_INTERVAL)

    def close(self) -> None:
        """Release the AVR connection. Does not call on_disconnect (see class docstring)."""
        if self.writer:
            try:
                self.writer.close()
            except OSError as e:
                self.logger.debug("Error closing writer: %s", e)
            self.reader = None
            self.writer = None


# -----------------------------------------------------------------------------
# Virtual AVR - in-process AVR that processes commands and emits responses
# -----------------------------------------------------------------------------


class VirtualAVRConnection:
    """
    Simulates a Denon AVR in-process. Accepts telnet commands, updates state,
    and emits responses via on_response - so the proxy treats it like a real AVR.

    The proxy never uses optimistic state for VirtualAVRConnection because
    commands can't fail (there is no hardware to reject them).

    close(): Releases the connection only; does not call on_disconnect.
    There is no "connection lost" event for a virtual AVR, so on_disconnect
    is never invoked (same contract as AVRConnection: only connection loss
    triggers the callback, not explicit close).
    """

    def __init__(
        self,
        avr_state: AVRState,
        on_response: Callable[[str], None],
        on_disconnect: Callable[[], None],
        logger: logging.Logger,
        volume_step: float,
    ) -> None:
        self.avr_state = avr_state
        self.on_response = on_response
        self.on_disconnect = on_disconnect
        self.logger = logger
        self._connected = False
        self.volume_step = volume_step

    def is_connected(self) -> bool:
        return self._connected

    def get_details(self) -> dict[str, Any]:
        """Return details about this AVR connection (virtual)."""
        return {"type": "virtual"}

    async def connect(self) -> bool:
        self._connected = True
        self.logger.info("Virtual AVR ready (demo mode)")
        return True

    async def send_command(self, command: str) -> bool:
        if not self._connected:
            return False
        cmd = (command or "").strip()
        if not cmd or len(cmd) < 2:
            return True
        self.logger.debug("Virtual AVR received: %s", cmd)
        self.avr_state.apply_command(cmd, volume_step=self.volume_step)
        # Emit the response(s) a real AVR would send for this command
        dump = self.avr_state.get_status_dump().strip()
        prefix = cmd[:2].upper()
        for line in dump.splitlines():
            line = line.strip()
            if not line or len(line) < 2:
                continue
            line_prefix = line[:2].upper()
            if line_prefix == prefix or (prefix in ("PW", "ZM") and line_prefix in ("PW", "ZM")):
                self.avr_state.update_from_message(line)
                self.on_response(line)
        return True

    async def request_state(self) -> None:
        if not self._connected:
            return
        for line in self.avr_state.get_status_dump().strip().splitlines():
            if line.strip():
                self.on_response(line.strip())

    def close(self) -> None:
        """Release the virtual AVR. Does not call on_disconnect (see class docstring)."""
        self._connected = False
        self.logger.info("Virtual AVR closed")


# -----------------------------------------------------------------------------
# Factory
# -----------------------------------------------------------------------------


def create_avr_connection(
    config: Config,
    avr_state: AVRState,
    on_response: Callable[[str], None],
    on_disconnect: Callable[[], None],
    logger: logging.Logger,
    on_send_while_disconnected: Callable[[], None] | None = None,
) -> AVRConnection | VirtualAVRConnection:
    """
    Create an AVR connection based on config. Returns AVRConnection for a
    physical AVR (when avr_host is set), or VirtualAVRConnection otherwise.
    The caller uses the same interface either way - opaque to the proxy.
    """
    host = (config.get("avr_host") or "").strip()
    port = int(config.get("avr_port", 23))
    if host:
        return AVRConnection(
            host=host,
            port=port,
            on_response=on_response,
            on_disconnect=on_disconnect,
            avr_state=avr_state,
            logger=logger,
            on_send_while_disconnected=on_send_while_disconnected,
            config=config,
        )
    return VirtualAVRConnection(
        avr_state=avr_state,
        on_response=on_response,
        on_disconnect=on_disconnect,
        logger=logger,
        volume_step=float(config["volume_step"]),
    )
