# Agent analysis: UC Remote power issue via denon-proxy

**Agent:** Codex  
**Date:** 2026-04-05  
**Scope:** Updated analysis from the latest `docs/UC_REMOTE_POWER_ISSUE_HANDOFF.md`, using the deployed `denon-proxy` branch named in that handoff plus the GitHub-pinned UC dependencies it references.

---

## Executive summary

The new handoff materially changes my prior ranking.

Two fresh facts are especially important:

1. The same failure has been observed with **`optimistic_state` both enabled and disabled**.
2. **`POST /api/refresh` does not recover the problem**, while a full `denon-proxy` service restart does.

Because of those two points, I no longer think a proxy-only stale optimistic state is the best primary theory.

My current leading explanation is that the UC stack itself gets into a **stale or poisoned power/session state** in which it stops emitting `PWON` at all, even though it can still emit `PWSTANDBY`. The proxy restart likely helps not because of the refresh queries it performs, but because it forces a **hard peer-side socket reset and full reconnect path** in the UC telnet client/library.

---

## Ranked theories

### 1. Most likely cause: UC integration / pinned `denonavr` gets into a stale state where `PWON` is suppressed before it ever reaches the proxy

This is now my top theory because it best matches the updated handoff's strongest evidence:

- when the bug is active, the proxy logs show **no UC `PWON` line at all**
- UC can still produce `PWSTANDBY`
- Home Assistant still controls power successfully
- `POST /api/refresh` does not fix it
- a full proxy restart does fix it temporarily

The GitHub-pinned UC stack still has send-suppression behavior:

- `denonavr.async_power_on()` returns immediately if internal `_power == "ON"`
- `denonavr.async_power_off()` returns immediately if internal `_power == "OFF"`

The UC integration also keeps long-lived receiver state and expected state across its own reconnect logic, and it has explicit workaround code for state-reporting mismatch.

That creates a plausible failure mode:

1. UC's internal power state becomes wrong or stale.
2. A user presses power on.
3. The UC stack decides the receiver is already on and does not actually send `PWON`.
4. The proxy never logs `Client ... command: PWON` because nothing was sent.

Why this theory moved to the top:

- It directly explains the missing proxy-side `PWON` log lines.
- It remains compatible with `PWSTANDBY` still appearing.
- It is consistent with `optimistic_state` being irrelevant.
- It is consistent with `POST /api/refresh` being insufficient if the UC-side session/state machine is already wedged or not incorporating the refresh correctly.

Confidence: **high**

---

### 2. Strong companion theory: the UC telnet session or callback machinery gets wedged, and only a hard peer disconnect fully resets it

The pinned UC stack has a meaningful distinction between:

- routine updates / telnet health checks
- telnet reconnect
- full integration connect/disconnect lifecycle

Relevant code behavior:

- the integration keeps `_expected_state` and receiver objects across reconnects
- `power_on()` calls `_receiver.async_power_on()` and then only schedules an update if telnet is not healthy
- the telnet layer has its own `connected` / `healthy` state, keepalive, reconnect task, send confirmation event, and callback registration

This makes the handoff's new restart-vs-refresh fact especially important:

- `POST /api/refresh` proves that **fresh AVR state queries alone are not sufficient**
- service restart does more than refresh: it tears down the client-facing socket from the proxy side and forces the UC stack through a harder reconnect path

So I think there is a real possibility that the UC-side telnet protocol/callback state gets into a bad state where:

- power-on sends are suppressed or never reach the transport
- ordinary state refreshes do not unwind the condition
- only a full peer disconnect from the proxy side recovers the session

This is distinct from theory 1 but compatible with it; theory 1 is about stale power state, while this one is about a wider poisoned telnet/session state.

Confidence: **medium-high**

---

### 3. Lower-confidence proxy-side theory: a `PWON` line may still be getting lost or rejected before INFO logging

I would now rank this below the UC-side theories, but it is still possible.

The handoff is careful to say that absence of `PWON` in the logs means either:

- the client never emitted `PWON`, or
- the proxy never treated it as a logged command

Even on the deployed `fix/clean-avr-commands` branch, a client line could still in principle fail to become an INFO `command:` log if:

- it is malformed in a way the parser or validator drops
- it decodes into something unexpected
- it contains control bytes that cause `_is_valid_client_command()` rejection
- it lands in a log path that never emits the expected INFO line

What weakens this theory now:

- the failure also occurs with `optimistic_state` disabled
- `POST /api/refresh` does not help
- the symptom pattern feels more like "UC stopped sending `PWON`" than "proxy keeps mangling one exact command forever"

So I still consider it possible, but no longer likely enough to lead with.

Confidence: **medium-low**

---

### 4. Why restart helps: restart is not just a refresh, it is a forced peer-side teardown

The new handoff sharply improves this distinction.

`POST /api/refresh` does this:

- asks the proxy to query the AVR
- obtains fresh `PW?`, `ZM?`, and other state
- broadcasts answers to connected telnet clients

Yet that does **not** resolve the UC issue.

A full proxy restart additionally:

- drops the UC socket from the server side
- rebuilds the proxy process and its client handlers
- rebuilds the proxy-to-AVR session
- forces every client to reconnect to a new process

That makes restart evidence point much more strongly toward:

- a UC connection/session reset effect, or
- some state in the proxy-client relationship that is only cleared by actual socket teardown

and less strongly toward:

- ordinary stale AVR state inside the proxy

Confidence: **high**

---

## What changed from my prior analysis

I am explicitly downgrading the earlier "false optimistic proxy power state" theory because the updated handoff says:

- the same failure appears with `optimistic_state` off
- `POST /api/refresh` still does not recover it

If optimistic proxy state were the main root cause, I would expect at least one of those facts to cut strongly against the bug. Instead, both observations point upstream of optimistic broadcast behavior.

I still think proxy state can be a contributing factor in some edge cases, but it is no longer my primary conclusion.

---

## Best-fit narrative

The best fit to the updated handoff is now:

1. UC works normally for a while and emits `PWON` / `PWSTANDBY`.
2. At some point, the UC integration or pinned `denonavr` telnet/session state becomes stale or wedged.
3. UC can still emit some commands, including `PWSTANDBY`, but later "power on" actions stop resulting in an outbound `PWON`.
4. Because no `PWON` is actually emitted, the proxy logs no `Client ... command: PWON`.
5. Home Assistant continues to work because it is a different client with a simpler power-control path.
6. `POST /api/refresh` does not help because the bad state is not merely "proxy has stale AVR data."
7. Restarting `denon-proxy` helps because it forces a true client-facing disconnect and a full session rebuild.

That is the explanation I would currently bet on first.

---

## What would most quickly prove or disprove this

1. On a failed UC power-on attempt, capture whether **any** bytes arrive at the proxy socket from the UC client.
2. If bytes arrive, capture the exact line content before validation/log filtering.
3. Correlate the first disappearance of UC `PWON` with any UC-side telnet reconnect, keepalive, or send-confirmation anomalies in UC logs.
4. Compare two recovery actions side by side:
   - `POST /api/refresh`
   - full proxy restart
   If only restart helps, that strongly favors a session-reset explanation over a pure state-refresh explanation.
5. Test UC pointed directly at the AVR. If the same "no `PWON` emitted" behavior appears there, the proxy becomes much less likely as the primary cause.

---

## Confidence summary

- **Top theory:** UC integration / pinned `denonavr` suppresses or fails to emit `PWON` after entering a stale state
- **Strong secondary theory:** a wedged UC telnet/session state is only cleared by hard proxy-side disconnect and reconnect
- **Lower-confidence proxy theory:** `PWON` is emitted but lost or rejected before proxy INFO logging
- **What the new handoff most strongly rules out:** optimistic proxy state as the sole primary cause

