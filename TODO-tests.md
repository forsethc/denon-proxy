# Tests – task list (unit + integration)

Record of approach for adding unit and integration tests. Treat as a task list. Each item has a **Prompt** you can copy to implement it.

---

## 1. SSDP discovery

- [ ] **Integration: SSDP protocol**
  - [ ] Use in-memory/mock transport or bind to 127.0.0.1 (avoid real multicast in CI), send M-SEARCH, assert response.
    **Prompt:** Add an integration test for SSDP in this repo: start the SSDP responder (or use a mock UDP transport) without real multicast. Send one UDP packet that looks like an M-SEARCH (e.g. ST: urn:schemas-denon-com:device:AiosDevice:1). Assert one response is sent back with HTTP 200, LOCATION containing description.xml, and correct ST/USN. Use pytest-asyncio.

---

## 2. Command forwarding and broadcast to clients

- [ ] **Volume commands** – MVUP/MVDOWN, assert state and broadcast to all clients.
  **Prompt:** Add an integration test: proxy with VirtualAVR, connect two Telnet clients. One client sends MVUP (or MVDOWN). Assert state.volume (or level) updates and that both clients receive the broadcast response. Use asyncio.wait_for with a short timeout when reading from client streams.
- [ ] Optional: second client sends command; first client sees broadcast.
  **Prompt:** Add an integration test: proxy with VirtualAVR, two clients. Client B sends a command (e.g. SIHDMI1). Assert client A receives the broadcast (or that state reflects the change and GET /api/state would show it).

---

## 3. HTTP discovery / denonavr-style endpoints

- [ ] Start discovery HTTP server on random port; GET /description.xml, GET /goform/deviceinfo.xml, POST /goform/appcommand.xml (GetFriendlyName); assert 200 and body content.
  **Prompt:** Add integration tests for the discovery HTTP server in this repo: start run_discovery_servers (or the HTTP part) on a random port (e.g. 0), then (1) GET /description.xml – assert 200 and body contains device/root and presentationURL or LOCATION; (2) GET /goform/deviceinfo.xml – assert 200 and body contains Device_Info; (3) POST /goform/appcommand.xml with body `<tx><cmd id="1">GetFriendlyName</cmd></tx>` – assert 200 and body contains friendlyname with the configured name. Use aiohttp or raw asyncio TCP + HTTP bytes.
- [ ] **MainZone XML endpoints** – GET MainZoneXML / MainZoneXMLStatus returns XML matching AVRState.
  **Prompt:** Add integration tests for MainZone XML in this repo: with a known AVRState (power, volume, mute, input_source, sound_mode, sources), GET the paths that map to MainZoneXML/MainZoneXMLStatus (e.g. those containing `mainzonexmlstatus` or `mainzonexml`). Assert HTTP 200 and that the XML body has fields consistent with the state (Power, MasterVolume in dB, Mute, InputFuncSelect, SurrMode, InputFuncList, RenameSource, SourceDelete).

---

## 4. Web UI / JSON API

  **Prompt:** Add an integration test: start the Web UI (or full proxy with Web UI) in this repo, then GET /api/state. Assert the JSON has top-level keys avr, clients, state; and that avr.sources (if present) has items with func and display_name, and state has fields like power, volume and smart_select if present. Use aiohttp or urllib.
- [ ] **GET /api/status** – returns same JSON structure as /api/state.
  **Prompt:** Add integration tests for the Web UI status endpoints: start the Web UI, call GET /api/status, and assert it returns HTTP 200 JSON with the same structure and fields as /api/state (avr, clients, state).
- [ ] **POST /api/command** – success when send_command is configured; 501 when not; 400 on bad body.
  **Prompt:** Add integration tests for WebUIHandler._handle_post_command: (1) when send_command is None, POST /api/command returns 501 with an error JSON; (2) when send_command is a mock, POST with body {\"command\": \"PWON\"} returns 200 with {\"ok\": true, \"command\": \"PWON\"} and the mock is called; (3) POST with invalid body (non-JSON or missing command) returns 400.
- [ ] **POST /api/refresh** – success when request_state is configured; 501 when not.
  **Prompt:** Add integration tests for WebUIHandler._handle_post_refresh: (1) when request_state is None, POST /api/refresh returns 501 with an error JSON; (2) when request_state is a mock, POST /api/refresh returns 200 with {\"ok\": true} and the mock is called.
- [ ] **POST /state (virtual AVR only)** – only works when set_state is provided; returns updated state.
  **Prompt:** Add integration tests for WebUIHandler._handle_post_state: (1) when set_state is None, POST /state returns 501 with an error JSON; (2) when set_state is a mock and get_state returns a dict with a \"state\" key, POST /state with a valid JSON body returns 200 with {\"ok\": true, \"state\": ...} and set_state is called with the payload; (3) invalid JSON or types return 400.
- [ ] **GET /events (SSE)** – opens stream and receives state updates on notify_state_changed.
  **Prompt:** Add an integration test for the Web UI SSE endpoint: start the Web UI, connect to /events and read from the stream, then call the notify_state_changed callback returned by run_web_ui; assert the SSE client receives a \"data: ...\\n\\n\" message containing the JSON from get_state().

---

## 5. Other integration tests

- [ ] **End-to-end “discovery → connect → command”** (optional)
  **Prompt:** Add an optional end-to-end test: start the full proxy (VirtualAVR, SSDP HTTP, Telnet, Web UI) on dynamic ports. Simulate discovery by GETting description.xml and deviceinfo.xml. Open a Telnet connection to the proxy and send a command (e.g. PWON). Assert state (e.g. via GET /api/state) shows the change.
- [ ] **Optimistic state revert**
  **Prompt:** Add an integration test for optimistic state revert: use a mock AVR connection that implements the same interface as AVRConnection but send_command() returns False. Start the proxy with this mock, send a command from a client (e.g. PWON). Assert that after the failed send, state is reverted (e.g. power still off) and clients do not receive a bogus success broadcast.
- [ ] **Virtual AVR request_state** – request_state pushes full status dump via on_response.
  **Prompt:** Add an integration-style test for `VirtualAVRConnection.request_state` in `avr_connection`: create a VirtualAVRConnection with a test AVRState and an on_response mock that records lines; set some state fields, call request_state(), and assert that on_response is called with the expected telnet lines (PW, MV, SI, MU, MS/ZM) matching AVRState.get_status_dump().

---

## 6. Setup and structure

- [ ] Use pytest + pytest-asyncio; port 0; minimal config; unit tests for parse_ssdp_search_target, ssdp_response, parse_telnet_lines, build_json_state, get_sources; integration tests for SSDP, HTTP, Telnet, Web UI.
  **Prompt:** In this repo, add a test suite using pytest and pytest-asyncio. Use port 0 where servers need a port; use minimal in-memory config (enable_ssdp True, ssdp_advertise_ip 127.0.0.1, no avr_host). Add unit tests for: parse_ssdp_search_target, ssdp_response (avr_discovery); parse_telnet_lines, build_json_state (denon_proxy); get_sources (avr_discovery). Add integration tests for: SSDP response to M-SEARCH, HTTP discovery endpoints (description.xml, deviceinfo.xml, appcommand.xml), Telnet proxy with VirtualAVR (command + broadcast), and optional GET /api/state. Keep SSDP tests off port 1900 (mock or local UDP).

---

## 7. Other unit tests

Each item below is a pure or easily mockable function; add pytest unit tests and a **Prompt** to implement.

- [ ] **web_ui: parse_command_request (refactor + unit)** – Extract POST /api/command body validation into a pure function, e.g. `parse_command_request(body_bytes: bytes) -> tuple[Optional[str], Optional[dict]]` (command or None, error body or None). Use it in _handle_post_command. Add unit tests: valid body `{"command": "PWON"}` → (command, None); invalid JSON or missing/non-string command → (None, error_dict); command too short → (None, error_dict). Optional: same pattern for POST /state body validation if desired.
