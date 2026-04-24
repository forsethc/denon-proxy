# Consensus: UC Remote power-on failures via denon-proxy

**Date:** 2026-04-05  
**Purpose:** Synthesize the neutral handoff and three independent agent analyses in this folder. This is a meta-summary, not new primary research.

**Sources:**

- [UC Remote power handoff](../UC_REMOTE_POWER_ISSUE_HANDOFF.md) (`docs/UC-POWER-BUG/UC_REMOTE_POWER_ISSUE_HANDOFF.md`)
- [claude-4.6-opus-2026-04-05.md](./claude-4.6-opus-2026-04-05.md)
- [codex-agent-2026-04-05.md](./codex-agent-2026-04-05.md)
- [cursor-claude-sonnet-4-5-2026-04-05.md](./cursor-claude-sonnet-4-5-2026-04-05.md)

---

## Summary of findings

**Observed pattern (from handoff):** UC Remote 3 sometimes stops producing `PWON` in proxy logs while `PWSTANDBY` still appears; Home Assistant continues to control power through the same proxy; **only a full `denon-proxy` service restart** (not `POST /api/refresh`) temporarily restores behavior; the issue has been seen with **`optimistic_state` enabled and disabled**.

**Mechanistic story (two of three agents — Opus and Sonnet):**

1. For **single-zone** setups, power telnet events use **`PW`**, but **`PW`** is classified as an **all-zones** event (`zone == ALL_ZONES`) while **`_power_callback`** only updates when `zone == self.zone` (**`Main`**). The guard fails, so **`_power` is never updated from telnet**.
2. The Unfolded Circle integration **`async_update_receiver_data`** can **skip HTTP `async_update()`** while telnet is “healthy,” assuming telnet callbacks keep `_power` current — which fails given (1).
3. **`async_power_on()`** does `if self._power == "ON": return` with **no** telnet write → **no `PWON` in proxy logs**. **`async_power_off()`** is not symmetrically gated in the same way, so **`PWSTANDBY` can still be sent**.
4. **`POST /api/refresh`** broadcasts real AVR answers but **does not** clear the bad `_power` for the single-zone path (details differ slightly between Opus vs Sonnet in how `PW` vs `ZM` refresh paths interact with callbacks, but both agree refresh is an insufficient recovery). **Restart** drops telnet → integration treats telnet as unhealthy → **HTTP runs again** → **`_power` realigns**.

**Codex** agrees at the narrative level: UC / pinned **`denonavr`** gets into **stale internal power state**, **`async_power_on()`** suppresses **`PWON`**, and **hard reconnect** (proxy restart) resets that; it **does not** walk the **`ALL_ZONES` / `_power_callback`** line-by-line proof chain.

---

## Consensus

| Point | Agreement |
|--------|-----------|
| Primary failure mode is **no `PWON` emitted** (suppression on the UC / `denonavr` path), not “proxy always drops power-on lines” | **Strong** — all three |
| Involves **`denonavr` `_power` and `async_power_on()` early return** | **Strong** — all three (Codex narrative; Opus + Sonnet exact guards) |
| **`optimistic_state` / proxy optimistic broadcasts** are **not** the root cause | **Strong** — handoff + all three |
| **`POST /api/refresh` ≠ restart** implies the issue is **not** merely “stale AVR facts in the proxy”; aligns with **client/library state** or **paths only cleared by reconnect / HTTP** | **Strong** |
| **HA** keeps working because it **does not** use the same **`_power`-gated `denonavr` power-on path** | **Strong** — Opus + Sonnet explicit; Codex consistent |
| **`_power_callback` zone mismatch** (`Main` vs `All`) **plus** **HTTP skip while telnet healthy** as the **specific** mechanism | **Two of three** (Opus + Sonnet; Codex does not dispute, does not prove) |

**Bottom line:** **Consensus** that the **dominant** issue lives in the **UC stack (`unfoldedcircle/integration-denonavr` + pinned `henrikwidlund/denonavr`)**, especially **silent suppression of `PWON`** via stale **`_power`**. **Full consensus on the exact zone-dispatch bug** is between **Opus and Sonnet**; **Codex** matches **symptoms and recovery** without that proof depth.

---

## Most common local cause (fixable in denon-proxy)

Across analyses that name in-repo changes, the recurring **denon-proxy** mitigation is **`get_status_dump()` (and related snapshots) emitting `ZMON` / `ZMOFF` (ZM-family) alongside `PW*`** so multi-zone-style callbacks can see zone power on connect or broadcast. **Opus** and **Sonnet** both describe this as **defensive** and **insufficient by itself** to fix the **single-zone `_power_callback` zone mismatch**.

**Opus** also recommends **verifying IAC stripping** on the deployed revision — secondary, not repeated in the other two.

**Codex** still allows **proxy dropping or mangling `PWON` before INFO logging** as **possible** but **lower confidence** after the updated handoff.

---

## Most common upstream cause

**Direction (unanimous):** **`unfoldedcircle/integration-denonavr`** + **pinned `henrikwidlund/denonavr`** (e.g. commit pinned in that integration’s `requirements.txt`): internal **`_power`** becomes **stale**, **`async_power_on()`** **short-circuits**, **`PWON` never reaches the wire**.

**Specific pairing (Opus + Sonnet):** **`_power_callback` never updates `_power` for single-zone `PW` events** (zone guard + `PW` ∈ `ALL_ZONE_TELNET_EVENTS`) **and** **`async_update_receiver_data` skipping HTTP** while telnet looks healthy.

**Preferred upstream fixes (per those writeups):** adjust **`_power_callback`** to treat **`ALL_ZONES`** (or equivalent), and/or **relax or remove** the **`_power == "ON"`** guard in **`async_power_on()`**; integration may need to **avoid relying solely on telnet** to refresh `_power` if that path is broken.

---

## `fix/clean-avr-commands` branch validity

The analyses **do not** conclude that **`fix/clean-avr-commands`** is invalid or should be abandoned for this bug. Root cause is **upstream**; the issue appears with **`optimistic_state` on and off**. Proxy changes discussed are **additive** (e.g. **`ZM*` in status dump**, **IAC** verification), not “revert the branch.”

**Consensus-consistent stance:** **keep the branch**; treat UC power-on as **primarily an integration / `denonavr` fix**, with **optional denon-proxy hardening**.

---

*This document is a synthesis only; re-verify pins, commits, and production branches before acting on upstream or deployment decisions.*
