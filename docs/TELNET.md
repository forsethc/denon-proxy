# Telnet Test

Connect to the proxy over Telnet:

```bash
telnet <proxy-ip> 23
```

Then type Denon commands and press Enter:

- `PW?` - Query power state
- `PWON` - Power on
- `PWSTANDBY` - Power off
- `MV?` - Query volume
- `MV50` - Set volume to 50
- `SI?` - Query input
- `SICD` - Set input to CD
- `MUON` / `MUOFF` - Mute on/off

## Supported Commands

The proxy forwards **all** Denon Telnet commands transparently. Common commands:

| Command | Description       | Example                       |
| ------- | ----------------- | ----------------------------- |
| PW      | Power             | PWON, PWSTANDBY, PW?          |
| MV      | Master volume     | MV50, MV?, MVUP, MVDOWN       |
| SI      | Source/input      | SICD, SITUNER, SI?            |
| MU      | Mute              | MUON, MUOFF, MU?              |
| ZM      | Main zone power   | ZMON, ZMOFF, ZM?              |
| MS      | Surround mode     | MSSTEREO, MS?                 |
| MSSMART | Smart Select slot | MSSMART ?, MSSMART0, MSSMART1 |

See Denon's protocol documentation for the full command set.
