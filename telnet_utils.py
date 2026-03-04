"""
Shared telnet line parsing and formatting for proxy client input and AVR stream.
"""


def telnet_line_to_bytes(line: str) -> bytes:
    """Format a single telnet line for sending (Denon uses \\r as line terminator)."""
    return (line.strip() + "\r").encode("utf-8")


def parse_telnet_lines(buffer: bytes, data: bytes) -> tuple[list[str], bytes]:
    """
    Decode incoming bytes into complete telnet command lines (split on \\r or \\n).
    Treats both \\r and \\n as line endings and splits on whichever appears first.
    Returns (list_of_commands, remaining_buffer).
    """
    buffer += data
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
        try:
            cmd = line.decode("utf-8").strip()
        except UnicodeDecodeError:
            continue
        if cmd:
            commands.append(cmd)
    return commands, buffer
