"""
Track telnet *query* lines (commands ending with '?') the physical AVR does not
answer, and optionally suppress their client / outbound logs after a configured
number of timeouts. Non-query commands are not tracked or suppressed.

Matching a response to a pending command uses Denon-style stems (including MV vs
MVMAX and MS vs MSSMART). Pending entries are cleared without counting when the
AVR disconnects.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging


def normalize_command_key(cmd: str) -> str:
    """Stable key for a telnet line (strip, upper, collapse whitespace)."""
    return " ".join(cmd.strip().upper().split())


def is_avr_query_command(cmd: str) -> bool:
    """True if the line is treated as a Denon telnet query (trimmed line ends with '?')."""
    s = cmd.strip()
    return len(s) > 0 and s.endswith("?")


def command_stem(cmd_key: str) -> str:
    """
    Leading command token without trailing '?' for response matching.
    "PSIMAX ?" -> PSIMAX, "MV?" -> MV, "PWON" -> PWON.
    """
    first = cmd_key.split(maxsplit=1)[0] if cmd_key else ""
    if "?" in first:
        return first.split("?", 1)[0]
    return first


def response_matches_command(cmd_key: str, response: str) -> bool:
    """True if an AVR line likely answers this outbound command."""
    r = response.strip().upper()
    if not r or len(r) < 2:
        return False
    stem = command_stem(cmd_key)
    if not stem:
        return False
    if stem.startswith("MSSMART"):
        return r.startswith("MSSMART")
    if stem == "MS":
        return r.startswith("MS") and not r.startswith("MSSMART")
    if stem == "MV":
        return r.startswith("MV") and not r.startswith("MVMAX")
    return r.startswith(stem)


@dataclass
class _Pending:
    uid: int
    key: str
    task: asyncio.Task[None]


class UnansweredCommandTracker:
    """
    For each successful physical send of a *query* line (see is_avr_query_command),
    wait for a matching AVR response within timeout. If none arrives, increment
    per-command count; at threshold, mark the query suppressed for should_suppress().
    A later matching AVR line (for a pending send or unsolicited) clears
    suppression and the unanswered count. cancel_all_pending() drops waiters
    without counting (e.g. AVR disconnect).
    """

    def __init__(
        self,
        logger: logging.Logger,
        *,
        suppress_after: int,
        response_timeout: float,
    ) -> None:
        if suppress_after < 1:
            raise ValueError("suppress_after must be at least 1")
        self._logger = logger
        self._suppress_after = suppress_after
        self._timeout = response_timeout
        self._pending: list[_Pending] = []
        self._next_uid = 0
        self._unanswered: dict[str, int] = {}
        self._suppressed: set[str] = set()

    def should_suppress(self, cmd: str) -> bool:
        if not is_avr_query_command(cmd):
            return False
        return normalize_command_key(cmd) in self._suppressed

    def note_sent(self, cmd: str) -> None:
        if not is_avr_query_command(cmd):
            return
        key = normalize_command_key(cmd)
        uid = self._next_uid
        self._next_uid += 1

        async def waiter() -> None:
            try:
                await asyncio.sleep(self._timeout)
            except asyncio.CancelledError:
                return
            self._complete_timeout(uid)

        p = _Pending(uid=uid, key=key, task=asyncio.create_task(waiter()))
        self._pending.append(p)

    def on_response(self, message: str) -> None:
        for i, p in enumerate(self._pending):
            if response_matches_command(p.key, message):
                p.task.cancel()
                self._pending.pop(i)
                self._unanswered.pop(p.key, None)
                self._suppressed.discard(p.key)
                return
        self._unsuppress_if_suppressed_key_matches(message)

    def cancel_all_pending(self) -> None:
        for p in self._pending:
            p.task.cancel()
        self._pending.clear()

    def _complete_timeout(self, uid: int) -> None:
        for i, p in enumerate(self._pending):
            if p.uid == uid:
                self._pending.pop(i)
                self._bump(p.key)
                return

    def _unsuppress_if_suppressed_key_matches(self, message: str) -> None:
        """Clear suppression when the AVR emits a line matching a suppressed command (no pending)."""
        candidates = [k for k in self._suppressed if response_matches_command(k, message)]
        if not candidates:
            return
        key = max(candidates, key=lambda k: len(command_stem(k)))
        self._suppressed.discard(key)
        self._unanswered.pop(key, None)

    def _bump(self, key: str) -> None:
        n = self._unanswered.get(key, 0) + 1
        self._unanswered[key] = n
        if n >= self._suppress_after and key not in self._suppressed:
            self._suppressed.add(key)
            self._logger.info(
                "AVR query %r had no matching response within %.2fs "
                "%d time(s); suppressing logs until a matching response or "
                "process restart (set dynamic_command_filtering: false to disable).",
                key,
                self._timeout,
                n,
            )
