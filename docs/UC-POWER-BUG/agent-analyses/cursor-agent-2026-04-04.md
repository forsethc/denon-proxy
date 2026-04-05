# Agent analysis: UC Remote power-on failures via denon-proxy

**Agent:** Cursor (Claude)  
**Date:** 2026-04-04  
**Scope:** Conversation-derived conclusions about UC Remote 3 + denon-proxy + Home Assistant; compares with neutral handoff in `docs/UC_REMOTE_POWER_ISSUE_HANDOFF.md`.

This file **does** prejudice cause: it records hypotheses, evidence, and recommended follow-ups from one analysis pass. Use it to compare with other agents’ writeups in this directory.

---

## Executive summary

Observed symptoms are consistent with **more than one** contributing layer:

1. **Proxy:** Client lines that are not valid UTF-8 (e.g. Telnet IAC bytes prepended to a command on the same line as `PWON`) were **silently dropped** in `parse_telnet_lines` before any `Client … command:` log or forward to the AVR. That matches “standby still logged, power-on vanishes from logs” when standby lines stay clean ASCII. A fix was implemented: strip IAC sequences before decode (`src/denon_proxy/avr/telnet_utils.py`).

2. **Unfolded Circle stack (`integration-denonavr` + `denonavr`):** Internal `_power` used to gate `async_power_on` / `async_power_off` is updated from **`ZM`** telnet callbacks in `denonavr`’s `foundation.py`, while the integration’s UI/state path reacts strongly to **`PW`** events in `_telnet_callback`. The proxy’s optimistic `get_status_dump()` encodes power only as **`PW{state}`** (e.g. `PWON`, `PWSTANDBY`), not `ZMON`/`ZMOFF`. Non-idempotent early returns on send can then **skip** power commands while the remote UI reflects PW-driven state — consistent with “stuck either way” and with **HA** still working (HA’s legacy Denon component sends `PWON` without that library guard pattern).

3. **Restart vs sleep/wake:** Client reconnect alone already resets the proxy↔client TCP handler and resends `get_status_dump()`. **Proxy process restart** additionally tears down **proxy↔AVR**, re-runs startup `request_state()`, and forces **all** clients through hard disconnect — often a stronger recovery path on the remote than a clean sleep/wake.

None of these mutually exclude the others; production issues may be **additive**.

---

## Evidence inventory

| Evidence | What it suggests |
|----------|------------------|
| Journal: UC `PWSTANDBY` continues; `PWON` absent over multi-day stretches; HA `PWON`/`PWSTANDBY` still present | Failure is **client-specific** or **path-specific**, not global AVR or proxy accept |
| Same grep excludes `?` lines | Query traffic omitted; power **commands** should still appear if processed |
| Double `PWON` within ~1s when working | Client retry or duplicate send when first attempt had no effect |
| `systemctl restart denon-proxy` temporarily fixes | Reset of **AVR session** and/or **all clients’** error recovery, not only UC reconnect |
| UC logs show frequent connect/disconnect (sleep/wake) | New proxy `ClientHandler` each time; **cannot** explain bug by “no fresh session” alone |
| Code review: `parse_telnet_lines` used `decode('utf-8')` and `continue` on failure after consuming a line | **Whole line dropped** including trailing command if IAC prefix makes UTF-8 invalid |
| Code review: `denonavr` `async_power_on` returns immediately if `_power == "ON"` | No Telnet write; **no proxy log line** if guard misfires |
| Code review: `_power_callback` registers on **`ZM`**, not **`PW`**, in `foundation.py` | PW-heavy feed from proxy can desync `_power` vs integration `_expected_state` |

---

## Technical conclusions (ranked confidence)

### High confidence

- **Dropped client lines (UTF-8):** Reproduced locally: `b"\xff\xfePWON\r\n"` produced **no** parsed command before IAC stripping; after strip, `PWON` parses. Explains missing INFO logs and no forward when negotiation bytes prefix a command.

- **Proxy optimistic broadcast shape:** `AVRState.get_status_dump()` always emits **`PW…`** for power, never **`ZM…`**, even when the inbound command was `ZMON`/`ZMOFF`. Factual from `state.py`.

### Medium confidence

- **`denonavr` split state (PW vs ZM):** The integration updates display/state from **`PW`** in `avr.py` `_telnet_callback`; `denonavr`’s `_power` gate for sends follows **`ZM`**-driven `_power_callback`. Factual separation; **impact** depends on live traffic mix (real AVR still emits `ZM` responses when queried or toggled).

- **Coarse send confirmation in `denonavr`:** `_send_confirmation_callback` matches on **event prefix** (`_get_event`), not full command string — any `ZM…` line can confirm a pending `ZMON` waiter. May cause ordering edge cases under multi-writer broadcast; secondary to the main symptom set.

### Lower confidence / needs capture

- Exact bytes on wire when UC “on” fails **post** IAC fix.
- Whether deployed UC build sends `PWON`/`PWSTANDBY` vs `ZMON`/`ZMOFF` (local `denonavr` const uses `ZMON`/`ZMOFF`; user journals showed `PW*`).

---

## Code touchpoints (repository paths)

**denon-proxy (this repo)**

- `src/denon_proxy/avr/telnet_utils.py` — line parsing, IAC strip, UTF-8 decode
- `src/denon_proxy/proxy/core.py` — `ClientHandler`, logging, optimistic broadcast, `connection_made` welcome dump
- `src/denon_proxy/avr/state.py` — `get_status_dump()`, `apply_command`, `update_from_message`

**integration-denonavr** (sibling / external clone)

- `intg-denonavr/avr.py` — `_telnet_callback` (`PW` → `_set_expected_state`)
- Pinned library: `henrikwidlund/denonavr` per `requirements.txt`

**denonavr**

- `denonavr/foundation.py` — `async_power_on`, `async_power_off`, `_register_callbacks` / `_power_callback` (`ZM`)
- `denonavr/api.py` — `DenonAVRTelnetProtocol`, `_process_event`, `_send_confirmation_callback`, `request_state`-style query bursts / keepalive

**Home Assistant (comparison)**

- `homeassistant/components/denon/media_player.py` — `turn_on` / `turn_off` → `PWON` / `PWSTANDBY` without the same `denonavr` `_power` short-circuit

---

## Changes made during this analysis (denon-proxy)

- **`_strip_telnet_sequences`** + use before UTF-8 decode in `parse_telnet_lines`; tests in `tests/unit/test_telnet_utils.py`.

---

## Recommended verification

1. On failure: tcpdump or proxy debug of **raw** client ingress bytes for a failed power-on tap.
2. UC integration debug logs around `async_power_on` / telnet send when UI shows off but no line hits proxy.
3. After IAC fix: re-check journals for sustained “no `PWON` from UC” windows.
4. If still failing: patch **`denonavr`** or integration (PW also drives `_power`, or remove/skip send guards for telnet, or emit `ZM…` in proxy status dump) — **outside** this repo unless vendored.

---

## Comparison note

Place sibling agent outputs in `docs/agent-analyses/` with a stable naming scheme (e.g. `<tool>-<date>.md`) so diffs in conclusions and evidence are easy to scan.
