# Configuration Options

Full reference for `config.yaml` options. For minimal setup, see the [README](../README.md#configuration).

## Options

| Option      | Default   | Description                                    |
|------------|-----------|------------------------------------------------|
| `avr_host` | "" (demo) | IP or hostname of the physical AVR. Empty = demo mode (no AVR) |
| `avr_port` | 23        | Telnet port on the AVR                         |
| `proxy_host` | 0.0.0.0 | Bind address (0.0.0.0 = all interfaces)       |
| `proxy_port` | 23      | Port clients connect to (Home Assistant requires 23)       |
| `log_level` | INFO     | DEBUG, INFO, WARNING, or ERROR (proxy logging)   |
| `denonavr_log_level` | INFO | Log level for the denonavr library (initial HTTP state sync); independent of `log_level` |
| `log_command_groups_info` | [] | Command groups to log at INFO: power, volume, input, mute, sound_mode |
| `enable_ssdp` | true   | SSDP discovery for Home Assistant (HTTP discovery server and SSDP responder) |
| `ssdp_friendly_name` | (optional) | Name shown in Home Assistant. If omitted: uses the physical AVR's friendly name + " Proxy" (after HTTP sync), or "Denon AVR Proxy" if unknown. |
| `ssdp_http_port` | 8080 | Port for device description XML                |
| `ssdp_advertise_ip` | "" | IP to advertise (empty = auto-detect)      |
| `sources` | (from AVR or default) | Custom input sources: dict of `func_code: "Display Name"`. Omit to use actual device sources (including custom renames) when a physical AVR is connected |
| `optimistic_state` | true | Apply changes to internal state immediately when clients send commands; revert only if sending to the AVR fails. When using the virtual AVR (no `avr_host`), the proxy never uses optimistic mode because commands can't fail. |
| `optimistic_broadcast_delay` | 0.1 | Seconds to wait before broadcasting optimistic state to clients; emulates AVR confirmation and avoids flicker when a send fails. |
| `volume_step` | 0.5 | Volume increment used for `MVUP`/`MVDOWN` when using optimistic updates or the virtual AVR. `0.5` = half-step; some AVRs use `1.0`. |
| `volume_query_delay` | 0.15 | Delay after `MVUP`/`MVDOWN` before sending `MV?` so the AVR has time to apply the change before the volume is refreshed. |
| `enable_http` | true | Enable the HTTP interface (JSON API + HTML dashboard). Set to `false` to run the Telnet proxy only (no HTTP server). |
| `http_port` | 8081 | Port for the HTTP server (JSON API and optional Web UI dashboard) |

## Environment Variables

You can override `config.yaml` values with environment variables:

| Variable | Overrides | Example |
|----------|-----------|---------|
| `AVR_HOST` | `avr_host` | `AVR_HOST=192.168.1.100` |
| `AVR_PORT` | `avr_port` | `AVR_PORT=23` |
| `PROXY_HOST` | `proxy_host` | `PROXY_HOST=0.0.0.0` |
| `PROXY_PORT` | `proxy_port` | `PROXY_PORT=23` |
| `LOG_LEVEL` | `log_level` | `LOG_LEVEL=DEBUG` |
| `DENONAVR_LOG_LEVEL` | `denonavr_log_level` | `DENONAVR_LOG_LEVEL=WARNING` |

Example:

```bash
AVR_HOST=192.168.1.100 PROXY_PORT=2323 python denon_proxy.py
```
