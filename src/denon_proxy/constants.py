"""
Shared constants for denon-proxy.

Port defaults, volume scale, discovery/SSDP values, and timeouts in one place.
"""

# Proxy identity: used in SSDP SERVER header, USN, UDN, and discover CLI display.
# Change these to update how the proxy advertises and how discovery identifies it.
PROXY_NAME = "denon-proxy"  # USN/UDN prefix (uuid:denon-proxy-...), display brand in discover
PROXY_SERVER_PRODUCT = "Denon-AVR-Proxy"  # SERVER header product token (Linux/1.0 UPnP/1.0 .../1.0)

# Timeouts and delays (seconds)
# Telnet connect to AVR and HTTP fetch of AVR description.xml.
AVR_NETWORK_TIMEOUT = 5.0
# Socket timeout when probing for local IP (get_advertise_ip UDP to 8.8.8.8).
SOCKET_TIMEOUT = 2.0
# denonavr async_setup and async_update (initial state sync via HTTP).
DENONAVR_SYNC_TIMEOUT = 10.0
# Delay before attempting reconnect after AVR disconnect.
RECONNECT_DELAY = 2.0
# Delay after AVR connect before sending request_state.
POST_CONNECT_DELAY = 0.3
# Delay between individual commands in request_state (PW?, MV?, etc.).
REQUEST_STATE_INTERVAL = 0.05
# Max wait for discovery/HTTP servers to close during shutdown.
SHUTDOWN_SERVER_WAIT = 2.0
# Max wait for proxy.stop() during shutdown.
SHUTDOWN_PROXY_WAIT = 5.0

# Default port numbers (config defaults)
DEFAULT_AVR_PORT = 23
DEFAULT_PROXY_PORT = 23
DEFAULT_SSDP_HTTP_PORT = 8080
DEFAULT_HTTP_PORT = 8081

# Denon volume: 0–98 scale; 80 = 0 dB reference; (vol - 80) * 0.5 ≈ dB.
VOLUME_REFERENCE_LEVEL = 80.0
# Default state/display level when we have no AVR value (e.g. demo, discovery). 50 ≈ -15 dB.
VOLUME_DEFAULT_LEVEL = 50
# Default max volume when AVR has not sent MVMAX; many Denon/Marantz use 98.
DEFAULT_MAX_VOLUME = 98.0

# SSDP discovery
SSDP_MCAST_GRP = "239.255.255.250"
SSDP_MCAST_PORT = 1900
# Discovery HTTP ports: 80 (standard HTTP), 60006 (Denon aios_device.xml)
DISCOVERY_HTTP_PORT = 80
DENON_AIOS_HTTP_PORT = 60006

# Demo input sources - matches typical Denon AVR-X inputs for HA integration
DEMO_SOURCES = [
    ("CD", "CD"),
    ("DVD", "DVD"),
    ("BD", "Blu-ray"),
    ("GAME", "Game"),
    ("MPLAY", "Media Player"),
    ("SAT/CBL", "CBL/SAT"),
    ("TV", "TV Audio"),
    ("TUNER", "Tuner"),
    ("PHONO", "Phono"),
    ("AUX1", "AUX"),
    ("NET", "Network"),
    ("BT", "Bluetooth"),
    ("USB/IPOD", "iPod/USB"),
    ("HDMI1", "HDMI 1"),
    ("HDMI2", "HDMI 2"),
    ("HDMI3", "HDMI 3"),
    ("HDMI4", "HDMI 4"),
    ("HDMI5", "HDMI 5"),
    ("HDMI6", "HDMI 6"),
    ("HDMI7", "HDMI 7"),
]
