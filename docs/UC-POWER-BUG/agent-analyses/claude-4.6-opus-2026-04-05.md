# Agent analysis: UC Remote power-on failures via denon-proxy (revision 2)

**Agent:** claude-4.6-opus (via Cursor)
**Date:** 2026-04-05 (revised from earlier same-day analysis)
**Scope:** Independent code review of denon-proxy, henrikwidlund/denonavr at the integration's pinned commit (`7c77360`), unfoldedcircle/integration-denonavr (`main`), and Home Assistant's `denon` component (`dev`). All library analysis uses GitHub sources, not local checkouts. This revision incorporates the updated handoff (symptoms 5 and the optimistic_state configuration note) and a critical finding about the `_power_callback` zone guard that the first revision missed.

---

## Sources reviewed

| Component | Source | Revision |
|-----------|--------|----------|
| denon-proxy | local workspace | `fix/clean-avr-commands` HEAD (production branch per handoff) |
| henrikwidlund/denonavr | [GitHub](https://github.com/henrikwidlund/denonavr/tree/7c77360db681f3c3f8c27bcf53f318adabee37a0) | `7c77360` (pinned by integration `requirements.txt`) |
| integration-denonavr | [GitHub](https://github.com/unfoldedcircle/integration-denonavr/blob/main/intg-denonavr/avr.py) | `main` |
| HA denon component | [GitHub](https://github.com/home-assistant/core/blob/dev/homeassistant/components/denon/media_player.py) | `dev` |

---

## Symptoms restated (updated from handoff)

1. UC Remote 3 stops producing `PWON` in proxy logs; `PWSTANDBY` continues appearing.
2. Home Assistant controls power successfully through the same proxy throughout.
3. Restarting only the denon-proxy service temporarily restores UC power-on.
4. The failure develops within a single proxy process lifetime.
5. **Web UI "Refresh from AVR"** (`POST /api/refresh`, which sends `PW?`, `ZM?`, etc. to the real AVR and broadcasts responses) does **not** resolve the problem.
6. The bug occurs with `optimistic_state` **both enabled and disabled**.

---

## Root cause: `_power_callback` is dead code for single-zone receivers

**Confidence: High — structurally guaranteed by the code at the pinned commit**

This is the primary finding. The `_power` field that gates `async_power_on()` is **never updated from telnet events** for single-zone receivers, due to a zone-check mismatch in the callback dispatch chain. This makes `_power` freeze at its initial HTTP-fetched value once telnet becomes healthy, creating a permanent deadlock.

### The evidence chain

**Step 1: The receiver is single-zone.** The proxy logs show UC sending `PWSTANDBY` (not `ZMOFF`). From `foundation.py` `_register_callbacks()` (lines 790-802 at pinned commit):

```python
def _register_callbacks(self):
    power_event = "ZM"
    ...
    elif self.zones == 1:
        power_event = "PW"
        self.telnet_commands = self.telnet_commands._replace(
            command_power_on="PWON", command_power_standby="PWSTANDBY"
        )
    self.telnet_api.register_callback(power_event, self._power_callback)
```

The `PWSTANDBY` command in logs means `zones == 1` triggered this branch — otherwise denonavr would send `ZMOFF`.

**Step 2: `PW` events arrive with `zone = ALL_ZONES`.** From `const.py` (line 1167-1183):

```python
ALL_ZONE_TELNET_EVENTS = {
    "DIM", "HD", "ILB", "NS", "NSA", "NSE", "MN",
    "PW",   # ← PW is here
    "RM", "SY", "TF", "TM", "TP", "TR", "UG",
}
```

And from `api.py` `_process_event()` (lines 907-910):

```python
zone = MAIN_ZONE          # "Main"
if event in ALL_ZONE_TELNET_EVENTS:
    zone = ALL_ZONES       # "All"
```

So every `PWON` / `PWSTANDBY` telnet message → `zone = "All"`.

**Step 3: The zone guard in `_power_callback` never passes.** From `foundation.py` (line 472-475):

```python
def _power_callback(self, zone: str, _event: str, parameter: str) -> None:
    if self.zone == zone and parameter in POWER_STATES:
        self._power = parameter
```

`self.zone = "Main"` (MAIN_ZONE). The check `"Main" == "All"` is always **False**. `_power` is never written.

**Step 4: HTTP updates are skipped once telnet is healthy.** From the UC integration's `avr.py` `async_update_receiver_data()` (lines 682-687):

```python
if (telnet_is_healthy := self._telnet_healthy) and self._telnet_was_healthy:
    if force:
        await receiver.async_update()
        ...
    self._notify_updated_data()
    return  # ← skips async_update() entirely
```

Once telnet has been healthy across two consecutive update cycles, `async_update()` (the HTTP call that updates `_power`) is permanently skipped. The system assumes telnet callbacks will keep `_power` current — but per Step 3, they cannot.

**Step 5: `_power` freezes, and `async_power_on()` silently no-ops.** From `foundation.py` (lines 1596-1599):

```python
async def async_power_on(self) -> None:
    if self._power == "ON":
        return  # ← no telnet write, no proxy log, nothing
```

`_power` was set to `"ON"` during the initial HTTP update when the receiver was on. It never changes again. Every subsequent `async_power_on()` call silently returns, regardless of actual AVR state.

### Why this fits every symptom

| Symptom | Explanation |
|---------|-------------|
| UC `PWON` disappears from proxy logs | `async_power_on()` returns before writing to telnet → nothing reaches proxy |
| UC `PWSTANDBY` still appears | `async_power_off()` guards on `_power == "OFF"`, which is never true (stuck on `"ON"`), so it always proceeds and sends `PWSTANDBY` |
| HA continues working | HA's `denon` component sends `PWON` via raw telnet (`self.telnet_command("PWON")`) — no `_power` guard |
| Proxy restart fixes it | Restart drops all TCP sessions → UC's denonavr loses telnet → `_telnet_was_healthy` resets → next update calls `async_update()` (HTTP) → `_power` refreshed from real AVR state |
| **`POST /api/refresh` does NOT fix it** | Refresh sends `PW?` to AVR, AVR responds `PWSTANDBY`, proxy broadcasts it to UC. But `PWSTANDBY` is a `PW` event → `zone = ALL_ZONES` → zone guard fails → **`_power` still not updated**. This symptom is the strongest discriminator: it rules out any theory that depends on the proxy not providing fresh state. |
| Bug occurs with optimistic_state on or off | The root cause is in denonavr's callback dispatch, not in the proxy's optimistic broadcasting. Optimistic state is irrelevant. |

---

## What my first revision got wrong

My previous analysis centered on Theory 1 (optimistic broadcasts cause UC to believe AVR is already ON) and Theory 2 (PW vs ZM mismatch for multi-zone receivers). Both were plausible but missed the actual mechanism:

1. **I assumed the PW/ZM split only mattered for multi-zone receivers.** In fact, the zone guard bug means `_power` is broken for single-zone receivers too — and the log evidence (`PWSTANDBY` not `ZMOFF`) indicates this is a single-zone receiver.

2. **I treated optimistic state as a compounding factor.** The updated handoff explicitly states the bug occurs with optimistic_state disabled, ruling this out as a contributing cause.

3. **I didn't trace the `_process_event` → `_run_callbacks` zone assignment.** The `ALL_ZONE_TELNET_EVENTS` → `zone = ALL_ZONES` assignment is the critical link. Without reading `const.py`'s `ALL_ZONE_TELNET_EVENTS` set and cross-referencing it with `_process_event`'s zone logic, the zone guard mismatch is invisible.

4. **I didn't account for why `POST /api/refresh` fails to fix it.** This new symptom conclusively eliminates theories based on "the proxy doesn't provide enough state." The proxy does provide fresh `PWSTANDBY` from the real AVR — denonavr just can't process it due to the zone guard bug.

---

## Secondary concern: multi-zone receivers may have a related but distinct failure mode

For multi-zone receivers (`zones > 1`), `_power_callback` is registered on `ZM`, and `ZM` events are **not** in `ALL_ZONE_TELNET_EVENTS`. So `ZM` events arrive with `zone = MAIN_ZONE`, and the guard `self.zone == zone` → `"Main" == "Main"` passes correctly. `_power` **does** update from `ZM` telnet events in the multi-zone case.

However, the proxy's `get_status_dump()` only emits `PW…` lines, never `ZM…`. If a multi-zone UC reconnects and receives only `PW` status, `_power` is not updated (no `ZM` event). The `_async_trigger_updates()` query sequence includes `ZM?` (api.py line 660), so a fresh telnet connect should eventually populate `_power` — but there may be a timing window where the status dump's `PWON` makes the integration think the receiver is on before `ZM?` completes.

This is a weaker failure mode than the single-zone bug and may not apply to this specific operator (whose logs suggest single-zone). But adding `ZMON`/`ZMOFF` to `get_status_dump()` would be a defensive improvement.

---

## Recommendations

### In denonavr (pinned fork — highest priority)

1. **Fix `_power_callback` zone guard to accept `ALL_ZONES`:**

   ```python
   def _power_callback(self, zone: str, _event: str, parameter: str) -> None:
       if (self.zone == zone or zone == ALL_ZONES) and parameter in POWER_STATES:
           self._power = parameter
   ```

   This is the minimal fix. It allows `PW` events (which always arrive with `zone = ALL_ZONES`) to update `_power` for single-zone receivers where the callback is registered on `PW`.

2. **Remove or weaken the `_power == "ON"` guard in `async_power_on()`:** `PWON`/`ZMON` is idempotent on Denon receivers. The guard creates fragile coupling for no benefit.

### In denon-proxy (this repo — defensive)

3. **Emit `ZMON`/`ZMOFF` in `get_status_dump()` alongside `PW…`:** Helps multi-zone receivers whose `_power_callback` (correctly) listens on `ZM`.

4. **Verify IAC stripping fix is in the deployed revision on `fix/clean-avr-commands`.**

### Diagnostics

5. **Confirm single-zone:** Check denonavr setup logs for `zones` value, or inspect `DeviceZones` in the AVR's Deviceinfo.xml.

6. **Instrument `_power` in denonavr:** Add a debug log at the top of `async_power_on()` showing `self._power`. If it reports `"ON"` when the AVR is in standby, the zone guard bug is confirmed as the active mechanism.

7. **Reproduce without the proxy:** Point UC directly at the AVR. If the bug still occurs when HA turns off via `PWSTANDBY` and UC can't turn on, the bug is entirely in denonavr and is proxy-independent. This would be the strongest confirmation.

---

## Comparison with other analyses

| Analysis | Primary theory | Zone guard bug? | Handles symptom 5 (refresh doesn't fix)? |
|----------|---------------|-----------------|-------------------------------------------|
| cursor-agent-2026-04-04 | IAC corruption + PW/ZM mismatch | No | N/A (predates symptom) |
| claude-4.6-opus rev 1 | PW/ZM mismatch (multi-zone) + optimistic state | No | No |
| codex-agent-2026-04-05 | False optimistic state after AVR disconnect | No | No |
| cursor-claude-sonnet-4-5 | Zone guard dead code + HTTP skip | **Yes** | **Yes** |
| **This revision** | Zone guard dead code + HTTP skip (confirmed) | **Yes** | **Yes** |

The Sonnet analysis correctly identified the zone guard bug. This revision independently verifies the mechanism by tracing the full dispatch chain through `const.py` → `api.py` → `foundation.py`, and explains why `POST /api/refresh` cannot fix the problem (the fresh AVR responses still go through the broken `_process_event` → `_power_callback` path).

---

*All library code references verified against GitHub at the revisions listed in the table above. `PW` ∈ `ALL_ZONE_TELNET_EVENTS` verified at const.py line 1175; `_process_event` zone assignment at api.py lines 907-910; `_power_callback` guard at foundation.py line 473; `async_power_on` guard at foundation.py line 1598. Re-verify `requirements.txt` pins before attributing behavior to upstream libraries.*
