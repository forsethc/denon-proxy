# Agent analysis: UC Remote power-on failures via denon-proxy

**Agent:** Cursor (claude-4.6-sonnet-thinking)
**Date:** 2026-04-05 (updated same day from revised handoff)
**Sources used:** GitHub at the exact revisions named in the handoff — no local trees.

| Source | Commit / version |
|--------|-----------------|
| `unfoldedcircle/integration-denonavr` | `main` |
| `henrikwidlund/denonavr` | `7c77360db681f3c3f8c27bcf53f318adabee37a0` (pinned in integration's `requirements.txt`) |
| `denon-proxy` | current branch (`fix/clean-avr-commands`) |

---

## New evidence incorporated from updated handoff

The handoff was revised with two additions that sharpen the analysis:

**Symptom 1 clarified:** "the proxy logs show no corresponding `Client … command: PWON` (INFO-level line) from the UC Remote for those power-on attempts—the command simply never appears in journald as originating from that client." The absence is specifically at the INFO logging level, confirming the command is never processed through `ClientHandler._handle_command` at info severity.

**New symptom 5:** "Web UI 'Refresh from AVR' (`POST /api/refresh`, which triggers `request_state()` / `PW?`, `MV?`, `ZM?`, … query sequence to the physical AVR and broadcasts answers to connected Telnet clients) **does not** resolve the UC Remote power/sync problem when this failure mode is active — unlike restarting the `denon-proxy` service."

This is the single most diagnostic data point in the handoff. It eliminates the proxy's state tracking as a cause and provides a definitive test to distinguish single-zone from multi-zone as the active failure path. See "Why 'Refresh from AVR' fails to fix it" below.

**Configuration note added:** The same failure has been observed with `optimistic_state` both enabled and disabled. This rules out the optimistic broadcast path as the root cause.

---

## Executive summary

The failure is caused by **two interacting design decisions in the pinned `denonavr` library** that together make `_power` permanently stale in the denonavr object once telnet is established:

1. **`_power_callback` is a dead code path for single-zone receivers.** `PW` events arrive with `zone = ALL_ZONES`, but the callback guards on `self.zone == zone` (MAIN_ZONE ≠ ALL_ZONES). The guard never passes, so `_power` is never updated from any telnet event.

2. **`async_update_receiver_data` skips HTTP updates while telnet is healthy.** Once telnet is stable, the HTTP `async_update()` call that would refresh `_power` is bypassed. The system relies entirely on `_power_callback` to keep `_power` current — which, per (1), never fires.

The combination means `_power` freezes at the value it had when the telnet connection first became healthy. When the receiver transitions to standby (e.g. via HA's `PWSTANDBY`), `_power` stays "ON". The next call to `async_power_on()` sees `_power == "ON"`, silently returns without writing to the socket, and nothing reaches the proxy or the AVR.

---

## Evidence that the receiver is single-zone

The representative log contains:

```
Client UC Remote 3 (10.0.3.10) command: PWSTANDBY
```

`PWSTANDBY` (not `ZMOFF`) is UC Remote's power-off command. From `denonavr/const.py` (pinned commit):

```python
DENONAVR_TELNET_COMMANDS = TelnetCommands(
    ...
    command_power_on="ZMON",
    command_power_standby="ZMOFF",
    ...
)
```

And from `denonavr/foundation.py` `_register_callbacks()`:

```python
def _register_callbacks(self):
    power_event = "ZM"
    ...
    elif self.zones == 1:
        # ZM events do not always work when the receiver has only one zone
        power_event = "PW"
        self.telnet_commands = self.telnet_commands._replace(
            command_power_on="PWON", command_power_standby="PWSTANDBY"
        )
    self.telnet_api.register_callback(power_event, self._power_callback)
```

`PWSTANDBY` appears in the proxy log because `self.zones == 1` — the operator's receiver reports itself to denonavr as single-zone. In that branch, the library switches both the **send commands** and the **callback registration** from `ZM` to `PW`.

---

## Bug 1: `_power_callback` is a dead code path for single-zone receivers

`_power_callback` in `foundation.py`:

```python
def _power_callback(self, zone: str, _event: str, parameter: str) -> None:
    """Handle a power change event."""
    if self.zone == zone and parameter in POWER_STATES:
        self._power = parameter
```

`_power` is the field that gates `async_power_on()` and `async_power_off()`:

```python
async def async_power_on(self) -> None:
    """Turn on receiver."""
    if self._power == "ON":
        return  # ← silent early return; no telnet write, no proxy log
    if self.telnet_available:
        await self.telnet_api.async_send_commands(
            self.telnet_commands.command_power_on
        )
    ...
```

Now trace how `PW` events are dispatched. In `api.py` `_process_event()`:

```python
zone = MAIN_ZONE
if event in ALL_ZONE_TELNET_EVENTS:
    zone = ALL_ZONES
...
self._run_callbacks(message, event, zone, parameter)
```

And `PW` is in `ALL_ZONE_TELNET_EVENTS` (`const.py`):

```python
ALL_ZONE_TELNET_EVENTS = {
    ...
    "PW",
    ...
}
```

So every `PW` message — including `PWON` and `PWSTANDBY` — arrives at `_power_callback` with `zone = ALL_ZONES = "All"`.

But `self.zone = MAIN_ZONE = "Main"` for the main zone. The guard `self.zone == zone` becomes `"Main" == "All"` → **False**. The callback body never executes. **`_power` is never updated by any telnet event for single-zone receivers.**

This is not a hypothetical: it is structurally guaranteed by `PW` being in `ALL_ZONE_TELNET_EVENTS`.

---

## Bug 2: HTTP `async_update()` is skipped while telnet is healthy

`async_update_receiver_data()` in the UC integration's `avr.py`:

```python
# We can only skip the update if telnet was healthy after
# the last update and is still healthy now to ensure that
# we don't miss any state changes while telnet is down
# or reconnecting.
if (telnet_is_healthy := self._telnet_healthy) and self._telnet_was_healthy:
    if force:
        await receiver.async_update()
        ...
    self._notify_updated_data()
    return  # ← skips async_update() entirely
```

Once the telnet connection has been healthy across two consecutive update cycles, all calls to `async_update_receiver_data()` bypass `receiver.async_update()` (the HTTP fetch that updates `_power`). The code comment makes clear this is intentional — it is intended to reduce HTTP chatter when telnet is handling state. But since `_power_callback` never fires for PW events (Bug 1), there is no telnet path that can substitute for the skipped HTTP update.

---

## Combined failure scenario (step by step)

1. UC Remote wakes; proxy sees a new TCP connection; sends `get_status_dump()` → `PWON` (receiver is on).
2. denonavr's UC integration calls `async_telnet_connect()` and `async_update()` (HTTP).
3. HTTP fetch to proxy's `MainZoneXml` endpoint → `_power = "ON"` (correct; receiver is on).
4. Telnet connection stabilizes; `_telnet_healthy = True`, `_telnet_was_healthy = True` on the next update cycle.
5. **From this point, all `async_update_receiver_data()` calls skip `async_update()`.**
6. HA issues `PWSTANDBY` via the proxy; AVR enters standby; proxy state becomes `"STANDBY"`.
7. Proxy broadcasts `PWSTANDBY` to UC's telnet connection.
8. UC integration's `_telnet_callback` fires (it accepts `zone = ALL_ZONES`): `_set_expected_state(States.OFF)` — the UC remote UI shows the device as off. ✓
9. **`_power_callback` fires with `zone = ALL_ZONES`, but `self.zone == zone` fails — `_power` stays "ON".**
10. User presses **power on** on the UC Remote.
11. Integration calls `power_on()` → `self._receiver.async_power_on()`.
12. `async_power_on()`: `self._power == "ON"` → **returns immediately. No socket write. No proxy log line.**
13. Integration calls `_schedule_update_task()`.
14. `async_update_receiver_data()`: telnet is healthy → **skips HTTP** → `_power` unchanged → stays "ON".
15. The state is now self-reinforcing: every power-on attempt no-ops; nothing clears `_power`.

### Why proxy restart breaks the cycle

Proxy restart drops every telnet connection. UC Remote's telnet to the proxy closes. On the next reconnect:
- `_telnet_healthy = False` (connection was lost).
- UC reconnects; `async_update_receiver_data()` now calls `async_update()` (HTTP) because telnet was not healthy.
- HTTP fetches proxy's `MainZoneXml` → `_power = "STANDBY"` (correct; proxy state is "STANDBY").
- Next `async_power_on()`: `_power == "STANDBY"` → sends `PWON` → proxy logs it → AVR wakes. ✓

The proxy service restart is effectively forcing the telnet health state to reset, triggering the only code path that can refresh `_power`.

### Why HA is unaffected

The Home Assistant `denon` component (`homeassistant/components/denon/media_player.py`) does not use the denonavr library for power control. Its `turn_on()` writes `PWON` directly via telnet without any `_power` guard. There is no early-return mechanism to break.

### Why "Refresh from AVR" does not fix it — and why this confirms single-zone

`POST /api/refresh` triggers `AVRConnection.request_state()`, which among other queries sends `ZM?` to the physical AVR. The AVR responds with `ZMON` or `ZMOFF`. The proxy relays that line verbatim to all connected telnet clients, including UC Remote.

**For a multi-zone receiver** (where `_power_callback` is registered on `"ZM"`): when UC Remote receives `ZMOFF`, the denonavr event dispatch routes it to `_power_callback` with `zone = MAIN_ZONE`. The guard `self.zone == zone` → `"Main" == "Main"` → **True**. `_power` updates to `"OFF"`. The bug clears on the next power-on attempt. In other words, "Refresh from AVR" *would* fix multi-zone receivers.

**For a single-zone receiver** (where `_power_callback` is registered on `"PW"`, not `"ZM"`): no callback is registered on the `"ZM"` event. When UC Remote receives `ZMOFF`, the event is dispatched but finds no `ZM` listener that updates `_power`. `_power` stays `"ON"`. The bug is unaffected.

The operator reports that "Refresh from AVR" does **not** fix the problem. This is only consistent with the single-zone code path. It is therefore not merely supporting evidence for Bug 1 — it is a direct empirical confirmation of the mechanism.

**Why full restart does fix it:** A service restart closes the proxy–UC telnet connection. On reconnect, `_telnet_healthy` is `False`, so `async_update_receiver_data()` is forced to call `async_update()` (HTTP). The proxy's `MainZoneXml` endpoint correctly returns `power = "STANDBY"`. denonavr parses `./ZonePower/value` → `_power = "STANDBY"`. The HTTP path does not depend on `_power_callback` or any zone-event routing, so it succeeds regardless of the single/multi-zone branch. `async_power_on()` can now send `PWON`.

---

## Secondary observation: `get_status_dump()` emits only `PW*`, never `ZM*`

For multi-zone receivers (where `_power_callback` is registered on `ZM`, correctly), `_power` is updated when the AVR emits `ZMON` or `ZMOFF`. The proxy relays real AVR responses verbatim, so `ZMOFF` from the AVR reaches UC. **But when the receiver goes to standby via HA's `PWSTANDBY` command, the AVR may respond with `PWSTANDBY` only, not `ZMOFF`.** In that case, `_power_callback` never fires (no `ZM` event), and the skip-HTTP optimization (Bug 2) again prevents recovery. This explains why multi-zone users could see the same symptom.

The proxy's `AVRState.get_status_dump()` always emits `PW{state}`, not `ZMON`/`ZMOFF`. Status dumps sent on reconnect (`connection_made`) and on optimistic broadcast (`_broadcast_state`) therefore never trigger `_power_callback` for multi-zone receivers either. Adding `ZMON`/`ZMOFF` to `get_status_dump()` would partially mitigate this for multi-zone receivers, but would not fix the broken zone-check guard (Bug 1) for single-zone receivers.

---

## Summary table

| # | Where | What | Effect |
|---|-------|------|--------|
| 1 | `denonavr/api.py` `_process_event` | `PW` in `ALL_ZONE_TELNET_EVENTS` → zone set to `ALL_ZONES` | `_power_callback` zone guard always fails for single-zone receivers |
| 2 | `denonavr/foundation.py` `_power_callback` | `self.zone == zone` check | Dead code for single-zone receivers; `_power` frozen |
| 3 | `intg-denonavr/avr.py` `async_update_receiver_data` | Skip HTTP when telnet healthy | No path to refresh stale `_power`; self-reinforcing deadlock |
| 4 | `denonavr/foundation.py` `async_power_on` | `if self._power == "ON": return` | Silent no-op when `_power` is stale; nothing logged at proxy |

**Empirical discriminators (from updated handoff):**

| Observation | Consistent with single-zone Bug 1+2? | Consistent with multi-zone? |
|-------------|--------------------------------------|------------------------------|
| "Refresh from AVR" (`ZM?` → `ZMOFF` broadcast) does not fix the bug | ✓ — no ZM callback registered; `_power` unaffected | ✗ — ZMOFF would trigger ZM-registered `_power_callback` and fix it |
| Full proxy restart fixes the bug | ✓ — forces HTTP update path | ✓ |
| `PWSTANDBY` (not `ZMOFF`) in proxy log from UC Remote | ✓ — confirms `zones == 1` branch | ✗ |
| `optimistic_state` on/off makes no difference | ✓ — bug is in denonavr, not proxy broadcast | ✓ |

---

## Recommended verification

1. **Confirm single-zone**: Check whether denonavr logs `zones == 1` during setup, or capture the `GetAllZonePowerStatus` AppCommand response the integration receives from the proxy on startup.

2. **Instrument `_power`**: Add a debug log of `self._power` at the top of `async_power_on()` in the pinned denonavr fork. If it shows "ON" when the AVR is in standby, Bug 1 + 2 are confirmed.

3. **Reproduce without proxy**: Point UC Remote directly at the AVR. If the bug also appears there (HA turns off via PWSTANDBY, UC can't turn on), the bug is entirely in the denonavr library and is proxy-independent.

4. **Proposed fix — in denonavr**: In `_power_callback`, also accept `zone == ALL_ZONES` for the `PW` registration case, e.g.:
   ```python
   if (self.zone == zone or zone == ALL_ZONES) and parameter in POWER_STATES:
       self._power = parameter
   ```
   This would let `PWSTANDBY` and `PWON` correctly update `_power` for single-zone receivers.

5. **Proxy-side mitigation**: Include `ZMON`/`ZMOFF` in `get_status_dump()` alongside `PWON`/`PWSTANDBY`. This won't fix the zone-check bug but gives multi-zone receivers a ZM-family event on every reconnect, so `_power_callback` fires correctly from the welcome dump.

---

*Sources read: `henrikwidlund/denonavr` `foundation.py`, `api.py`, `const.py` at commit `7c77360`; `unfoldedcircle/integration-denonavr` `avr.py` at `main`; `denon-proxy` `src/denon_proxy/avr/state.py`, `src/denon_proxy/avr/discovery.py`, `src/denon_proxy/proxy/core.py`.*
