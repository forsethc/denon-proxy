"""
Shared telnet line parsing and formatting for proxy client input and AVR stream.
"""

from __future__ import annotations

# Telnet IAC and commands (RFC 854) — strip these from line payloads so ASCII
# Denon commands survive UTF-8 decoding when a client embeds negotiation bytes
# on the same line as a command (common with buggy / long-lived clients).
_IAC = 255
_IAC_SE = 240
_IAC_SB = 250
_IAC_WILL = 251
_IAC_WONT = 252
_IAC_DO = 253
_IAC_DONT = 254


def _strip_telnet_sequences(chunk: bytes) -> bytes:
    """Remove Telnet IAC command sequences from one line before UTF-8 decode."""
    i = 0
    out = bytearray()
    n = len(chunk)
    while i < n:
        b = chunk[i]
        if b != _IAC:
            out.append(b)
            i += 1
            continue
        if i + 1 >= n:
            break
        cmd = chunk[i + 1]
        if cmd == _IAC:
            out.append(_IAC)
            i += 2
        elif cmd in (_IAC_WILL, _IAC_WONT, _IAC_DO, _IAC_DONT):
            if i + 2 < n:
                opt = chunk[i + 2]
                # Truncated or malformed negotiation: if the third byte is A–Z,
                # it is almost certainly the start of a Denon command (PWON, …),
                # not a telnet option — skip only IAC + WILL/WONT/DO/DONT.
                if 65 <= opt <= 90:
                    i += 2
                else:
                    i += 3
            else:
                i += 2
        elif cmd == _IAC_SB:
            j = i + 2
            while j < n - 1:
                if chunk[j] == _IAC and chunk[j + 1] == _IAC_SE:
                    i = j + 2
                    break
                j += 1
            else:
                i = n
        else:
            i += 2
    return bytes(out)


def telnet_line_to_bytes(line: str) -> bytes:
    """Format a single telnet line for sending (Denon uses \\r as line terminator)."""
    return (line.strip() + "\r").encode("utf-8")


def parse_telnet_lines(buffer: bytes, data: bytes) -> tuple[list[str], bytes]:
    """
    Decode incoming bytes into complete telnet command lines (split on \\r or \\n).
    Treats both \\r and \\n as line endings and splits on whichever appears first.
    Returns (list_of_commands, remaining_buffer).
    """
    buffer = buffer + data
    commands: list[str] = []
    while True:
        pos_r = buffer.find(b"\r")
        pos_n = buffer.find(b"\n")
        if pos_r < 0 and pos_n < 0:
            break
        if pos_r < 0:
            first_pos, sep_len = pos_n, 1
        elif pos_n < 0:
            first_pos, sep_len = pos_r, 1
        else:
            first_pos = min(pos_r, pos_n)
            sep_len = 1
        line = buffer[:first_pos]
        buffer = buffer[first_pos + sep_len :]
        cleaned = _strip_telnet_sequences(line)
        try:
            cmd = cleaned.decode("utf-8").strip()
        except UnicodeDecodeError:
            continue
        if cmd:
            commands.append(cmd)
    return commands, buffer
