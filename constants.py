"""
Shared constants for denon-proxy.

Port defaults, volume scale, and discovery/SSDP values in one place.
"""

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
