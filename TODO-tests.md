# Tests – task list (unit + integration)

Record of approach for adding unit and integration tests. Treat as a task list. Each item has a **Prompt** you can copy to implement it.

---

## 1. SSDP discovery

- [ ] **Unit tests (pure helpers)**
  - [ ] `parse_ssdp_search_target()` – various M-SEARCH payloads, assert returned ST  
    **Prompt:** Add pytest unit tests in this repo for `parse_ssdp_search_target` from `avr_discovery`. Test with several M-SEARCH-style request strings (different ST: values, with/without other headers). Assert the returned search target string is correct.
  - [ ] `ssdp_response()` – given config + advertise_ip + st, assert response bytes (HTTP 200, LOCATION with description.xml, USN)  
    **Prompt:** Add pytest unit tests for `ssdp_response` in `avr_discovery`. Call it with sample config (e.g. ssdp_http_port 8080), an advertise_ip, and an ST value. Assert the returned bytes are HTTP 200, contain LOCATION with /description.xml, and contain the expected USN.
- [ ] **Integration: SSDP protocol**
  - [ ] Use in-memory/mock transport or bind to 127.0.0.1 (avoid real multicast in CI), send M-SEARCH, assert response.  
    **Prompt:** Add an integration test for SSDP in this repo: start the SSDP responder (or use a mock UDP transport) without real multicast. Send one UDP packet that looks like an M-SEARCH (e.g. ST: urn:schemas-denon-com:device:AiosDevice:1). Assert one response is sent back with HTTP 200, LOCATION containing description.xml, and correct ST/USN. Use pytest-asyncio.

---

## 2. Command forwarding and broadcast to clients

- [ ] Use **VirtualAVRConnection**, start proxy, open Telnet clients, test PWON and broadcast.  
  **Prompt:** Add an async integration test in this repo: start the Denon proxy with no avr_host (VirtualAVR), then open one or two Telnet clients to the proxy port with asyncio.open_connection. Send PWON from a client and assert that the client(s) receive the expected response lines and that proxy state (AVRState) shows power on. Use pytest-asyncio and short timeouts (e.g. asyncio.wait_for) when reading.
- [ ] **Volume commands** – MVUP/MVDOWN, assert state and broadcast to all clients.  
  **Prompt:** Add an integration test: proxy with VirtualAVR, connect two Telnet clients. One client sends MVUP (or MVDOWN). Assert state.volume (or level) updates and that both clients receive the broadcast response. Use asyncio.wait_for with a short timeout when reading from client streams.
- [ ] Optional: second client sends command; first client sees broadcast.  
  **Prompt:** Add an integration test: proxy with VirtualAVR, two clients. Client B sends a command (e.g. SIHDMI1). Assert client A receives the broadcast (or that state reflects the change and GET /api/state would show it).

---

## 3. HTTP discovery / denonavr-style endpoints

- [ ] Start discovery HTTP server on random port; GET /description.xml, GET /goform/deviceinfo.xml, POST /goform/appcommand.xml (GetFriendlyName); assert 200 and body content.  
  **Prompt:** Add integration tests for the discovery HTTP server in this repo: start run_discovery_servers (or the HTTP part) on a random port (e.g. 0), then (1) GET /description.xml – assert 200 and body contains device/root and presentationURL or LOCATION; (2) GET /goform/deviceinfo.xml – assert 200 and body contains Device_Info; (3) POST /goform/appcommand.xml with body `<tx><cmd id="1">GetFriendlyName</cmd></tx>` – assert 200 and body contains friendlyname with the configured name. Use aiohttp or raw asyncio TCP + HTTP bytes.

---

## 4. Web UI / JSON API

- [ ] GET /api/state and assert JSON shape (avr, clients, state; sources with func/display_name).  
  **Prompt:** Add an integration test: start the Web UI (or full proxy with Web UI) in this repo, then GET /api/state. Assert the JSON has top-level keys avr, clients, state; and that avr.sources (if present) has items with func and display_name, and state has fields like power, volume. Use aiohttp or urllib.

---

## 5. Other integration tests

- [ ] **End-to-end “discovery → connect → command”** (optional)  
  **Prompt:** Add an optional end-to-end test: start the full proxy (VirtualAVR, SSDP HTTP, Telnet, Web UI) on dynamic ports. Simulate discovery by GETting description.xml and deviceinfo.xml. Open a Telnet connection to the proxy and send a command (e.g. PWON). Assert state (e.g. via GET /api/state) shows the change.
- [ ] **Optimistic state revert**  
  **Prompt:** Add an integration test for optimistic state revert: use a mock AVR connection that implements the same interface as AVRConnection but send_command() returns False. Start the proxy with this mock, send a command from a client (e.g. PWON). Assert that after the failed send, state is reverted (e.g. power still off) and clients do not receive a bogus success broadcast.
- [ ] **Parsing / boundaries (unit)** – `parse_telnet_lines()`  
  **Prompt:** Add pytest unit tests for parse_telnet_lines in denon_proxy: test incomplete line (no trailing \\r\\n) leaves remainder in buffer; \\r\\n split across two data chunks; multiple commands in one chunk; empty data; mixed \\r and \\r\\n. Assert returned command list and remaining buffer.

---

## 6. Setup and structure

- [ ] Use pytest + pytest-asyncio; port 0; minimal config; unit tests for parse_ssdp_search_target, ssdp_response, parse_telnet_lines, build_json_state, get_sources; integration tests for SSDP, HTTP, Telnet, Web UI.  
  **Prompt:** In this repo, add a test suite using pytest and pytest-asyncio. Use port 0 where servers need a port; use minimal in-memory config (enable_ssdp True, ssdp_advertise_ip 127.0.0.1, no avr_host). Add unit tests for: parse_ssdp_search_target, ssdp_response (avr_discovery); parse_telnet_lines, build_json_state (denon_proxy); get_sources (avr_discovery). Add integration tests for: SSDP response to M-SEARCH, HTTP discovery endpoints (description.xml, deviceinfo.xml, appcommand.xml), Telnet proxy with VirtualAVR (command + broadcast), and optional GET /api/state. Keep SSDP tests off port 1900 (mock or local UDP).

---

## 7. Other unit tests

Each item below is a pure or easily mockable function; add pytest unit tests and a **Prompt** to implement.

- [ ] **avr_state.volume_to_level** – string to numeric level; half-steps (e.g. 535 → 53.5), MAX, empty, clamp to max_volume.  
  **Prompt:** Add pytest unit tests for `volume_to_level` in `avr_state`. Test: normal integer (e.g. "50" → 50), 3-digit half-step ("535" → 53.5), empty/None returns default, "MAX 60" or similar returns clamped value, values above max_volume clamp to max_volume.
- [ ] **avr_state.volume_to_db** – level to dB string (e.g. 80 → "0.0", 50 → negative).  
  **Prompt:** Add pytest unit tests for `volume_to_db` in `avr_state`. Test: volume string that maps to 80 gives "0.0", lower level gives negative dB, higher gives positive; check format is one decimal.
- [ ] **avr_state.AVRState.update_from_message** – PW, MV, SI, MU, MS, ZM messages update state.  
  **Prompt:** Add pytest unit tests for `AVRState.update_from_message` in `avr_state`. Create an AVRState, call update_from_message with "PWON", "PWSTANDBY", "MV50", "SIHDMI1", "MUON", "MUOFF", "MSSTEREO", "ZMON", "ZMSTANDBY"; assert the corresponding state attributes (power, volume, input_source, mute, sound_mode) are updated correctly.
- [ ] **avr_state.AVRState.apply_command** – PWON, MVUP/MVDOWN, SI, MU, MS; returns True when state changed.  
  **Prompt:** Add pytest unit tests for `AVRState.apply_command` in `avr_state`. Test PWON/PWSTANDBY set power; MVUP/MVDOWN change volume with step; SI and MS set input/sound_mode; MUON/MUOFF set mute. Assert return value is True when state changed, and state values are correct after each call.
- [ ] **avr_state.AVRState.get_status_dump** – returns telnet-format lines (PW, ZM, MV, SI, MU, MS).  
  **Prompt:** Add pytest unit tests for `AVRState.get_status_dump` in `avr_state`. Set state (power, volume, input_source, mute, sound_mode) then call get_status_dump; assert output contains expected lines (e.g. PWON, ZMON, MV50, SI..., MUOFF, MS...) and ends with \\r\\n.
- [ ] **avr_state snapshot/restore** – snapshot() then mutate state, restore(snapshot), assert state back to original.  
  **Prompt:** Add pytest unit tests for AVRState snapshot and restore in `avr_state`: set state, call snapshot(), change state, call restore(snapshot), assert all fields match the original values.
- [ ] **denon_proxy._is_valid_client_command** – accept 2+ letter commands, reject too short and telnet control bytes.  
  **Prompt:** Add pytest unit tests for `_is_valid_client_command` in `denon_proxy`. Test: "PWON" True, "SI" True, "X" False, "" False; reject strings containing bytes with ord < 32 (except \\r \\n \\t) if any.
- [ ] **denon_proxy._command_group** – PW/ZM→power, MV→volume, SI→input, MU→mute, MS→sound_mode, MVMAX→other.  
  **Prompt:** Add pytest unit tests for `_command_group` in `denon_proxy`. Test: PWON, ZMON → "power"; MV50, MVUP → "volume"; SIHDMI1 → "input"; MUON → "mute"; MSSTEREO → "sound_mode"; MVMAX 60 → "other"; unknown prefix → "other".
- [ ] **denon_proxy._should_log_command_info** – True when command’s group is in config log_command_groups_info.  
  **Prompt:** Add pytest unit tests for `_should_log_command_info` in `denon_proxy`. Test: config with log_command_groups_info ["power"] and "PWON" → True, "MV50" → False; empty or missing list → False.
- [ ] **denon_proxy.load_config_from_dict** – merge raw dict with defaults; no I/O.  
  **Prompt:** Add pytest unit tests for `load_config_from_dict` in `denon_proxy`. Test: empty dict returns defaults; partial dict (e.g. avr_host only) merges with defaults; overwrite existing keys.
- [ ] **denon_proxy._apply_env_overrides** – in-place override from AVR_HOST, PROXY_PORT, etc. (use mock env).  
  **Prompt:** Add pytest unit tests for `_apply_env_overrides` in `denon_proxy`. Use unittest.mock.patch.dict(os.environ, {...}) to set AVR_HOST, AVR_PORT, PROXY_HOST, PROXY_PORT, LOG_LEVEL; call _apply_env_overrides(config) and assert config values match env. Test with env unset leaves config unchanged.
- [ ] **web_ui.parse_http_request** – incomplete buffer returns None; complete buffer returns (method, path, header_bytes, body_bytes).  
  **Prompt:** Add pytest unit tests for `parse_http_request` in `web_ui`. Test: buffer without "\\r\\n\\r\\n" returns None; buffer "GET / HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n" returns method GET, path /; buffer with body "\\r\\n\\r\\nbody" returns correct body_bytes; path with query string returns path without query.
- [ ] **avr_connection._parse_mvmax** – "MAX 60", "MAX60", "MAX" (no number), invalid; returns float or None.  
  **Prompt:** Add pytest unit tests for `_parse_mvmax` in `avr_connection`. Test: "MAX 60" → 60.0, "MAX60" → 60.0, "MAX" or "MAX " → None, "X" → None; assert return is in [None, float] and value >= 0.
- [ ] **avr_discovery.get_advertise_ip** – when ssdp_advertise_ip is set in config, returns that value.  
  **Prompt:** Add pytest unit tests for `get_advertise_ip` in `avr_discovery`. Test: config {"ssdp_advertise_ip": "192.168.1.1"} returns "192.168.1.1"; empty string or missing key can be tested without socket (mock or skip the socket path and only test the config path).
- [ ] **avr_discovery.parse_appcommand_request** – parse <tx><cmd id="1">GetFriendlyName</cmd></tx> into list of (id, text).  
  **Prompt:** Add pytest unit tests for `parse_appcommand_request` in `avr_discovery`. Test: single <tx> with one cmd returns [(id, "GetFriendlyName")]; multiple <tx> chunks; malformed XML or empty body returns empty list or skips invalid parts.
- [ ] **avr_discovery._escape_xml_text** – &, <, >, " escaped for XML.  
  **Prompt:** Add pytest unit tests for `_escape_xml_text` in `avr_discovery`. Test: "a & b" → "a &amp; b", "<x>" → "&lt;x&gt;", '"' → "&quot;", no special chars unchanged.
- [ ] **denon_proxy.build_json_state** – given state, avr (or None), clients list, config; assert keys avr, clients, state and structure (sources with func/display_name if present).  
  **Prompt:** Add pytest unit tests for `build_json_state` in `denon_proxy`. Pass a minimal AVRState, optional avr (or None), empty clients list, and config with _resolved_sources or _device_sources. Assert returned dict has top-level keys "avr", "clients", "state"; assert avr.sources items have "func" and "display_name"; assert state has power, volume, etc. from AVRState.
