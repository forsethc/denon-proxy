"""Unit tests: AVR unanswered-command tracking and log suppression."""

from __future__ import annotations

import asyncio
import logging

import pytest

from denon_proxy.avr.unanswered_tracker import (
    UnansweredCommandTracker,
    is_avr_query_command,
    normalize_command_key,
    response_matches_command,
)


def test_normalize_command_key_collapses_whitespace() -> None:
    assert normalize_command_key("  PSIMAX  ?  ") == "PSIMAX ?"


@pytest.mark.parametrize(
    ("cmd", "expect"),
    [
        ("MV?", True),
        ("  PSIMAX ? ", True),
        ("MSSMART ?", True),
        ("PWON", False),
        ("PWON?", True),
        ("", False),
        ("  ", False),
    ],
)
def test_is_avr_query_command(cmd: str, expect: bool) -> None:
    assert is_avr_query_command(cmd) is expect


@pytest.mark.asyncio
async def test_non_query_never_tracked_or_suppressed() -> None:
    log = logging.getLogger("test.unanswered.nonquery")
    t = UnansweredCommandTracker(log, suppress_after=1, response_timeout=0.05)
    t.note_sent("PWON")
    await asyncio.sleep(0.08)
    assert not t.should_suppress("PWON")


def test_tracker_rejects_non_positive_suppress_after() -> None:
    log = logging.getLogger("test.unanswered.reject")
    with pytest.raises(ValueError, match="at least 1"):
        UnansweredCommandTracker(log, suppress_after=0, response_timeout=0.05)


@pytest.mark.parametrize(
    ("cmd", "resp", "expect"),
    [
        ("MV?", "MV45", True),
        ("MV?", "MVMAX MAX 60", False),
        ("MVMAX?", "MVMAX MAX 60", True),
        ("MS?", "MSSTEREO", True),
        ("MS?", "MSSMART0", False),
        ("MSSMART ?", "MSSMART0", True),
        ("PSIMAX ?", "PSIMAX50", True),
        ("PW?", "PWON", True),
    ],
)
def test_response_matches_command_cases(cmd: str, resp: str, expect: bool) -> None:
    key = normalize_command_key(cmd)
    assert response_matches_command(key, resp) is expect


@pytest.mark.asyncio
async def test_suppress_after_n_timeouts() -> None:
    log = logging.getLogger("test.unanswered.suppress")
    t = UnansweredCommandTracker(log, suppress_after=2, response_timeout=0.05)
    assert not t.should_suppress("PSIMAX ?")
    t.note_sent("PSIMAX ?")
    await asyncio.sleep(0.08)
    assert not t.should_suppress("PSIMAX ?")
    t.note_sent("PSIMAX ?")
    await asyncio.sleep(0.08)
    assert t.should_suppress("PSIMAX ?")


@pytest.mark.asyncio
async def test_matching_response_cancels_timeout() -> None:
    log = logging.getLogger("test.unanswered.match")
    t = UnansweredCommandTracker(log, suppress_after=1, response_timeout=0.2)
    t.note_sent("MV?")
    t.on_response("MV45")
    await asyncio.sleep(0.25)
    assert not t.should_suppress("MV?")


@pytest.mark.asyncio
async def test_cancel_all_pending_does_not_count() -> None:
    log = logging.getLogger("test.unanswered.cancel")
    t = UnansweredCommandTracker(log, suppress_after=1, response_timeout=0.05)
    t.note_sent("XYZ?")
    t.cancel_all_pending()
    await asyncio.sleep(0.08)
    assert not t.should_suppress("XYZ?")


@pytest.mark.asyncio
async def test_suppression_cleared_when_response_matches_pending() -> None:
    log = logging.getLogger("test.unanswered.unsuppress_pending")
    t = UnansweredCommandTracker(log, suppress_after=1, response_timeout=0.05)
    t.note_sent("PSIMAX ?")
    await asyncio.sleep(0.08)
    assert t.should_suppress("PSIMAX ?")
    t.note_sent("PSIMAX ?")
    t.on_response("PSIMAX50")
    assert not t.should_suppress("PSIMAX ?")


@pytest.mark.asyncio
async def test_suppression_cleared_when_matching_response_has_no_pending() -> None:
    log = logging.getLogger("test.unanswered.unsuppress_solo")
    t = UnansweredCommandTracker(log, suppress_after=1, response_timeout=0.05)
    t.note_sent("PSIMAX ?")
    await asyncio.sleep(0.08)
    assert t.should_suppress("PSIMAX ?")
    t.on_response("PSIMAX50")
    assert not t.should_suppress("PSIMAX ?")
