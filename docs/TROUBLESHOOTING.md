# Troubleshooting

## "Failed to connect to AVR"

- Ensure the AVR is on and connected to the network
- Enable **Network Remote** in the AVR settings
- Verify you can ping the AVR: `ping 192.168.1.100`
- Try telnet directly: `telnet 192.168.1.100 23`

## Clients don't see updates

- Ensure the proxy is running and connected to the AVR
- Check logs with `log_level: DEBUG` in config

## Power state not updating in Home Assistant (2026.x)

- The denonavr integration has **Use telnet** off by default. With telnet disabled, power updates come from HTTP polling (~10 seconds).
- For **instant** power updates: Integration → Configure → enable **Use telnet**. The proxy broadcasts ZM/ZMSTANDBY so Home Assistant receives power changes via telnet.

## Running on Port 23

The Home Assistant Denon integration only connects to port 23. On some systems (especially many Linux distros), binding to low ports (<1024) may require elevated privileges or special capabilities. If you see a "permission denied" error when starting the proxy on port 23, re-run it with `sudo`, grant `cap_net_bind_service`, or use Docker with `--cap-add=NET_BIND_SERVICE`.

## Port 23 binding issues

- If you see a "permission denied" error when binding to port 23, run the proxy with `sudo` (or Docker with `cap_net_bind_service`), or choose a different port and use manual Home Assistant configuration if supported by your setup.

## Manual add fails instantly

- Set `log_level: "DEBUG"` in config and check logs for `HTTP: GET /goform/Deviceinfo.xml` — if you don't see it, Home Assistant isn't reaching the proxy
- Verify the HTTP server: `curl http://<proxy_ip>:8080/goform/Deviceinfo.xml` — should return XML
- Ports 80 and 60006: denonavr needs these for AVR-X 2016. The proxy binds 80, 8080, and 60006 when possible. Run with `sudo` if 80 or 60006 fail to bind
- Ensure `enable_ssdp` is true (the HTTP server only runs when SSDP is enabled; it defaults to true)

## "Unknown Error" or Timeout in Home Assistant

If adding the device in Home Assistant fails with "Unknown error" or "Timeout":

1. **Check Home Assistant logs** – Settings → System → Logs. The actual exception (e.g. `AvrIncompleteResponseError`) will appear there and pinpoint the issue.
2. **Ensure port 60006 is available** – denonavr fetches device info from port 60006 for AVR-X models. The proxy binds 80, 8080, and 60006. If 60006 is in use, restart the proxy.
3. **Use the proxy IP** – When adding manually, enter the **proxy's** IP (the machine running denon-proxy), not the physical AVR's IP.
4. **Run proxy with DEBUG** – `log_level: DEBUG` in config will log which AppCommand requests denonavr sends.

## SSDP discovery not working

- UDP 1900 often requires root on Linux: if binding fails with a permission error, run with `sudo` or adjust capabilities
- Set `ssdp_advertise_ip` to your proxy's LAN IP if auto-detect fails
- Ensure port 8080 is free for the device description HTTP server (or set `ssdp_http_port`)
- Home Assistant requires port 23 — make sure the proxy is listening on 23 and reachable from Home Assistant. If your OS refuses to bind to port 23 with a "permission denied" error, run the proxy with `sudo` or via Docker with `--cap-add=NET_BIND_SERVICE`.
