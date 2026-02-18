"""
AVR connection layer - physical telnet and in-process virtual AVR.

Provides a single abstraction used by the proxy: AVRConnection (physical device)
or VirtualAVRConnection (no hardware). Both share the same interface so the
proxy does not need to know which is in use.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from avr_state import AVRState


# -----------------------------------------------------------------------------
# AVR Connection - telnet connection to physical AVR
# -----------------------------------------------------------------------------


class AVRConnection:
    """
    Maintains a single Telnet connection to a physical Denon AVR.
    Receives responses and forwards them via on_response for broadcasting.
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

    def is_connected(self) -> bool:
        """Return True if connected to the AVR."""
        return self.writer is not None and not self.writer.is_closing()

    def get_details(self) -> dict[str, Any]:
        """Return details about this AVR connection (physical)."""
        return {"type": "physical", "host": self.host, "port": self.port}

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
        """Send a telnet command to the AVR."""
        if not self.is_connected():
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
            await asyncio.sleep(0.05)

    def close(self) -> None:
        """Close the AVR connection."""
        if self.writer:
            self.writer.close()
            self.reader = None
            self.writer = None


# -----------------------------------------------------------------------------
# Virtual AVR - in-process AVR that processes commands and emits responses
# -----------------------------------------------------------------------------


class VirtualAVRConnection:
    """
    Simulates a Denon AVR in-process. Accepts telnet commands, updates state,
    and emits responses via on_response - so the proxy treats it like a real AVR.
    """

    def __init__(
        self,
        state: AVRState,
        on_response: Callable[[str], None],
        on_disconnect: Callable[[], None],
        logger: logging.Logger,
    ) -> None:
        self.state = state
        self.on_response = on_response
        self.on_disconnect = on_disconnect
        self.logger = logger
        self._connected = False

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
        self.state.apply_command(cmd)
        # Emit the response(s) a real AVR would send for this command
        dump = self.state.get_status_dump().strip()
        prefix = cmd[:2].upper()
        for line in dump.splitlines():
            line = line.strip()
            if not line or len(line) < 2:
                continue
            line_prefix = line[:2].upper()
            if line_prefix == prefix or (
                prefix in ("PW", "ZM") and line_prefix in ("PW", "ZM")
            ):
                self.state.update_from_message(line)
                self.on_response(line)
        return True

    async def request_state(self) -> None:
        if not self._connected:
            return
        for line in self.state.get_status_dump().strip().splitlines():
            if line.strip():
                self.on_response(line.strip())

    def close(self) -> None:
        self._connected = False
        self.logger.info("Virtual AVR closed")
        try:
            self.on_disconnect()
        except Exception:
            # Keep shutdown robust; mirror AVRConnection behaviour without failing hard
            self.logger.debug("Virtual AVR on_disconnect callback raised", exc_info=True)


# -----------------------------------------------------------------------------
# Factory
# -----------------------------------------------------------------------------


def create_avr_connection(
    config: dict,
    state: AVRState,
    on_response: Callable[[str], None],
    on_disconnect: Callable[[], None],
    logger: logging.Logger,
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
            state=state,
            logger=logger,
        )
    return VirtualAVRConnection(
        state=state,
        on_response=on_response,
        on_disconnect=on_disconnect,
        logger=logger,
    )
