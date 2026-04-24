# UC Remote 3 — HTTP Custom Entity Setup

This guide explains how to use the proxy's built-in HTTP control API with the
[ucr2-integration-requests](https://github.com/kennymc-c/ucr2-integration-requests)
integration to create a fully-featured Denon AVR custom entity on your
Unfolded Circle Remote 3 (UCR3).

## Why use this instead of the Denon AVR integration?

The official Denon AVR integration on the UCR3 uses a persistent Telnet
connection. If something else (Home Assistant, the proxy's virtual AVR, etc.)
is also connected, the remote may not see live state updates reliably. The
HTTP approach is stateless and connectionless — each button press fires a single
GET request that the proxy translates to a Telnet command, with no shared
connection to fight over.

## Prerequisites

1. The proxy is running and reachable from the remote (same network).
2. The `enable_http` option is `true` (it is by default).
3. The [ucr2-integration-requests](https://github.com/kennymc-c/ucr2-integration-requests)
   integration is installed on the remote (version 0.10+ recommended, which supports
   custom entities). Follow the installation instructions in that project's README.

## Step 1 — Get the generated YAML

Open the proxy's Web UI at `http://<proxy_ip>:8081` (or whatever port you have
`http_port` set to). You will see a **UC Remote 3 Custom Entity** panel. Click
**Copy YAML** to copy the configuration to your clipboard.

Alternatively, fetch it directly:

```
GET http://<proxy_ip>:8081/api/uc/custom_entities.yaml
```

Or download it from the Web UI's **Download** button.

The YAML looks like this (source list varies by your AVR):

```yaml
_vars:
  denon_proxy_url: http://192.168.1.10:8081

Denon AVR Proxy:
  Features:
    'On':
      Type: get
      Parameter: ${denon_proxy_url}/api/avr/power/on
    'Off':
      Type: get
      Parameter: ${denon_proxy_url}/api/avr/power/off
    Toggle:
      Type: get
      Parameter: ${denon_proxy_url}/api/avr/power/toggle
  Simple Commands:
    VOLUME_UP:
      Type: get
      Parameter: ${denon_proxy_url}/api/avr/volume/up
    VOLUME_DOWN:
      Type: get
      Parameter: ${denon_proxy_url}/api/avr/volume/down
    MUTE_ON:
      Type: get
      Parameter: ${denon_proxy_url}/api/avr/mute/on
    MUTE_OFF:
      Type: get
      Parameter: ${denon_proxy_url}/api/avr/mute/off
    MUTE_TOGGLE:
      Type: get
      Parameter: ${denon_proxy_url}/api/avr/mute/toggle
    SRC_CD:
      Type: get
      Parameter: ${denon_proxy_url}/api/avr/source/CD
    SRC_HDMI1:
      Type: get
      Parameter: ${denon_proxy_url}/api/avr/source/HDMI1
    # ... one entry per source configured in the proxy
  Selects:
    Source:
    - SRC_CD: CD
    - SRC_HDMI1: HDMI 1
    # ...
```

The source list is generated from whatever inputs your AVR reports (or from the
`sources` override in `config.yaml`). If you add or rename sources, refresh the
page and copy the YAML again.

## Step 2 — Paste into the UC integration

1. In the UCR3 web configurator, go to **Integrations → ucr2-integration-requests → Settings**.
2. Open the **Custom entity configuration** text area.
3. Paste the YAML (or merge it with any existing entries — the `_vars` block
   must appear only once, at the top of the file).
4. Save. The integration will create a **Denon AVR Proxy** remote entity and a
   **Source** select entity.

> **Tip:** The integration requires 2-space indentation (no tabs). The generated
> YAML uses 2 spaces throughout.

## Step 3 — Assign buttons on the remote

Once the entity appears in the configurator, add it to an activity or assign its
commands to physical buttons. Recommended mappings:

| Button | Command |
|---|---|
| Power | `On` / `Off` / `Toggle` feature |
| Vol + | `VOLUME_UP` |
| Vol − | `VOLUME_DOWN` |
| Mute | `MUTE_TOGGLE` |
| Source | `Source` select entity |

## Volume control note

The volume endpoints use the AVR's `MVUP` / `MVDOWN` telnet commands, which step
by the AVR's configured increment (0.5 dB by default when using the proxy). For
an absolute volume level you can use:

```
GET http://<proxy_ip>:8081/api/avr/volume/set?level=45
```

This sends `MV45` (level 0–98, rounded to the nearest 0.5 step).

## Updating the proxy address

If the proxy moves to a different IP or port, you only need to change the single
`denon_proxy_url` line in the `_vars` block — no other lines need editing.

## Capabilities endpoint

For programmatic use, the proxy also exposes a structured JSON description of all
available endpoints and the current source list:

```
GET http://<proxy_ip>:8081/api/uc/capabilities
```

This is useful if you want to build tooling that auto-generates or validates your
custom-entity configuration.
