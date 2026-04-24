# Handoff: UC Remote 3 power control via denon-proxy

This document summarizes **observed behavior**, **evidence**, and **system context** so another reviewer can analyze the issue without inheriting prior hypotheses about root cause.

---

## Role of denon-proxy

Repository: **[https://github.com/forsethc/denon-proxy](https://github.com/forsethc/denon-proxy)** — **production** runs Git branch **`fix/clean-avr-commands`** (see **Source of truth** below).

Denon AV receivers typically allow **one** Telnet (TCP) session to the control port. The proxy:

- Listens for **multiple** inbound Telnet clients (e.g. Home Assistant, Unfolded Circle “UC Remote 3”).
- Maintains **one** outbound Telnet session to the physical AVR.
- Forwards client commands to the AVR (serialized on the single AVR link).
- **Broadcasts** each line received from the AVR to **all** currently connected clients.
- Tracks internal AVR-like state (power, volume, input, mute, etc.) and, when a new client connects, sends an immediate **status snapshot** to that client (implementation: `AVRState.get_status_dump()` in `src/denon_proxy/avr/state.py`, invoked from `ClientHandler.connection_made` in `src/denon_proxy/proxy/core.py`).
- Optional **optimistic** application of client commands to internal state before/after forwarding, with configurable delay and broadcast of derived status lines (`ClientHandler._handle_command_async`, `_broadcast_state` in `core.py`).

Configuration surface includes HTTP API (e.g. `POST /api/refresh` triggers `AVRConnection.request_state()`, which sends a fixed sequence of query commands such as `PW?`, `MV?`, `ZM?`, etc., to the physical AVR).

---

## Erratic behavior reported

1. **UC Remote 3** (Unfolded Circle Remote, Denon AVR integration) sometimes **fails to turn the receiver on** when the user expects it to. While this failure mode is present, **the proxy logs show no corresponding `Client … command: PWON`** (INFO-level line) from the UC Remote for those power-on attempts—the command simply never appears in journald as originating from that client, even though the user is pressing power on.
2. **Power-off** (`PWSTANDBY` in proxy logs) **continues to appear** in logs when the user powers off.
3. **Home Assistant** (second client; same proxy) **continues to control power successfully** when UC Remote misbehaves.
4. **Restarting only the denon-proxy service** (not the UC device or the AVR) **restores** normal UC Remote power-on behavior for a time.
5. **Web UI “Refresh from AVR”** (HTTP **`POST /api/refresh`**, which triggers `request_state()` / the usual `PW?`, `MV?`, **`ZM?`**, … query sequence to the physical AVR and broadcasts answers to connected Telnet clients) **does not** resolve the UC Remote power / sync problem when this failure mode is active—**unlike** restarting the `denon-proxy` service (operator observation).

**Configuration note:** The same class of UC Remote power-on failures has been observed with **`optimistic_state` enabled and disabled** in denon-proxy config. Analysts should not assume the bug is confined to optimistic broadcasts alone.

---

## Evidence: proxy logs (journal), command filtering

Example extraction pattern used on the deployment host:

```bash
journalctl -u denon-proxy --since "7 days ago" | grep -vi "debug" | grep -v "?" | grep "command: PW"
```

That pipeline **removes** lines containing `?` (so **query** traffic such as `PW?` is excluded from the excerpt).

### Pattern observed over ~7 days

- **Home Assistant** (`10.0.1.5`): `PWON` and `PWSTANDBY` appear when expected.
- **UC Remote 3** (`10.0.3.10`):
  - For a period, **both** `PWON` and `PWSTANDBY` appear, sometimes **two `PWON` lines within one second**.
  - Later in the same period, **only** `PWSTANDBY` appears from UC Remote; **no** `PWON` lines appear in the filtered log **even when the user attempts to turn the unit on**.
- **Process restarts** appear as changes in the logged `python[PID]` (deployments or service restarts correlate with renewed `PWON` from UC Remote in subsequent log segments).

Representative log lines (as provided by the operator; abbreviated):

```
Mar 29 … Client UC Remote 3 (10.0.3.10) command: PWON
Mar 29 … Client UC Remote 3 (10.0.3.10) command: PWON
…
Apr 01 … Client UC Remote 3 (10.0.3.10) command: PWSTANDBY
Apr 01 … (no UC PWON in this stretch; multiple PWSTANDBY only)
…
Apr 02 … Client Home Assistant (10.0.1.5) command: PWON
…
Apr 04 … Client UC Remote 3 (10.0.3.10) command: PWSTANDBY / PWON patterns resume after service restarts in the log
```

**Interpretation note for the analyst:** absence of `PWON` in this grep means **either** the client did not emit `PWON`, **or** the proxy did not treat/process a line as a logged command (e.g. parsing, validation, logging level, or line format). The logs alone do not distinguish those without additional instrumentation or packet capture.

---

## Evidence: UC Remote connect/disconnect cadence

The proxy logs **per-telnet-client** connect/disconnect. UC Remote does not keep a permanent Telnet session; it disconnects and reconnects regularly (e.g. device sleep/wake).

Example sequence (single day, same proxy process):

```
Client disconnected: UC Remote 3 (10.0.3.10) (remaining: 1)
Client connected: UC Remote 3 (10.0.3.10) (total: 2)
```

So from the **proxy’s perspective**, each wake cycle is already a **new** TCP connection and a new `ClientHandler`; `connection_made` runs again and sends the current `get_status_dump()` snapshot to that client.

---

## Source of truth: use GitHub (and deployed revisions), not informal local trees

Cross-repo analysis should rely on **authoritative sources**: the repositories and **exact commits / release tags** that match what runs in production. Ad-hoc or partial local checkouts (any machine) can be **wrong branch, wrong fork, or wrong pin** and should **not** be treated as evidence about upstream behavior.

1. **Confirm what is deployed** where possible: UC Remote integration version or channel, HA core version, `denon-proxy` package/git revision on the Pi/host, and whether the AVR integration points at the proxy or at the receiver.
2. **Open the same revision on GitHub** (or equivalent tagged release artifacts) when reading `requirements.txt`, library code, and HA components.
3. **Pinned Python dependencies** must be taken from the integration’s **`requirements.txt` at the Git revision that matches the device**, not from another checkout.

### Unfolded Circle Denon AVR integration (“UC Remote” driver)

- **Repository:** [https://github.com/unfoldedcircle/integration-denonavr](https://github.com/unfoldedcircle/integration-denonavr)
- **Dependency pins** (example from **`requirements.txt` on `main` at time this doc was refreshed; always re-read live on GitHub for the branch/tag you care about):  
  [https://github.com/unfoldedcircle/integration-denonavr/blob/main/requirements.txt](https://github.com/unfoldedcircle/integration-denonavr/blob/main/requirements.txt)

Current pinned `denonavr` install (VCS line from that file):

```text
denonavr@git+https://github.com/henrikwidlund/denonavr.git@7c77360db681f3c3f8c27bcf53f318adabee37a0
```

For UC behavior, the relevant **`denonavr` source is that repository at commit `7c77360db681f3c3f8c27bcf53f318adabee37a0`** (until the integration bumps the pin). Direct link:  
[https://github.com/henrikwidlund/denonavr/tree/7c77360db681f3c3f8c27bcf53f318adabee37a0](https://github.com/henrikwidlund/denonavr/tree/7c77360db681f3c3f8c27bcf53f318adabee37a0)

Other dependencies from the same file: `pyee~=13.0.1`, `ucapi==0.5.2` (verify on GitHub when analyzing).

### denonavr (historical upstream, reference only)

- **Repository:** [https://github.com/olivergrant/denonavr](https://github.com/olivergrant/denonavr)  
  Many forks diverge; **the UC integration does not necessarily track this tree** for runtime behavior—compare only when investigating shared ancestry or porting fixes.

### Home Assistant Denon integration (comparison client)

- **Repository:** [https://github.com/home-assistant/core](https://github.com/home-assistant/core)  
- **Typical path:** `homeassistant/components/denon/`  
- Use the **tag matching the operator’s Home Assistant release** (e.g. `2024.x.y`) or `dev` if explicitly analyzing tip; do not assume an unlabeled local `core` checkout matches production.

### denon-proxy

- **Repository:** [https://github.com/forsethc/denon-proxy](https://github.com/forsethc/denon-proxy)  
- **Production deployment (operator):** branch **`fix/clean-avr-commands`** — [https://github.com/forsethc/denon-proxy/tree/fix/clean-avr-commands](https://github.com/forsethc/denon-proxy/tree/fix/clean-avr-commands). Correlate logs and symptoms with the **commit** at the tip of that branch (or the exact revision installed on the proxy host), not only `main`.

---

## Component map (for navigation)

| Component | Role |
|-----------|------|
| **denon-proxy** | Multiplexes Telnet clients to one AVR; logs `Client … command:` for lines accepted from clients; relays AVR lines. |
| **Unfolded Circle Denon AVR integration** | Python integration on UC Remote; Telnet/HTTP to AVR **or** to proxy as host; pins `denonavr` and `ucapi` per its `requirements.txt`. |
| **henrikwidlund/denonavr (pinned commit)** | Library used by that integration at runtime for UC-controlled behavior. |
| **olivergrant/denonavr** | Historical upstream reference. |
| **Home Assistant `denon` component** | Alternative client; may use different command patterns; compare at the HA version in use. |

---

## Facts about proxy client command path (for reproduction / instrumentation)

- Incoming bytes from a Telnet client are split into lines in `parse_telnet_lines` (`src/denon_proxy/avr/telnet_utils.py`).
- Each non-empty decoded line is passed to `ClientHandler._handle_command` in `core.py`.
- Invalid lines (per `_is_valid_client_command`, e.g. certain control characters) are rejected with **debug** logging, not necessarily **info** `command:` logs.
- Client `command:` **info** logging is subject to `log_command_groups_info` in config (`command_log.py`); power-related groups must be enabled for `PWON` / `PWSTANDBY` to appear at INFO.

---

## What this handoff intentionally omits

- Causal theories (protocol negotiation, optimistic vs AVR-relay semantics, client internal power state, idempotency of library methods, etc.).
- Presumed fixes.

Those should be derived from independent analysis of the **GitHub revisions above**, captures of raw bytes on the wire if possible, and UC integration / `denonavr` traces at appropriate log levels.

---

## Suggested next steps for an analyst

1. Confirm **exact** Telnet payloads from UC Remote at the proxy when power-on **fails** (tcpdump / proxy debug logging of raw client reads).
2. Compare behavior when UC points **directly** at the AVR (single client) vs **via** proxy.
3. Compare **`henrikwidlund/denonavr` at the integration’s pinned commit** plus the integration’s Telnet usage with **Home Assistant’s `denon` component at the operator’s HA version** for the same operations.
4. Correlate proxy **service restart** with **AVR-side** connection teardown (proxy reconnects to AVR on start) vs **client-only** reconnect patterns.

---

*Document purpose: external handoff. Use GitHub (and deployment-matching revisions) as the analysis source of truth. **denon-proxy** production runs branch **`fix/clean-avr-commands`** on [forsethc/denon-proxy](https://github.com/forsethc/denon-proxy). The `denonavr` Git commit and `ucapi` version above match [unfoldedcircle/integration-denonavr `main` `requirements.txt`](https://github.com/unfoldedcircle/integration-denonavr/blob/main/requirements.txt) as of the last edit of this file—re-verify before attributing bugs upstream.*
