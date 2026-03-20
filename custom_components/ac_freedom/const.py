"""Constants for the AC Freedom integration."""

DOMAIN = "ac_freedom"
PLATFORMS = ["climate", "fan", "switch"]  # fan = preset modes, switch = Display only

CONF_MAC = "mac"

# Polling interval in seconds
POLL_INTERVAL = 5

# Communication & discovery ports
PORT_COMM = 80              # UDP communication port
PORT_DISCOVERY_1 = 15001    # Discovery port (primary)
PORT_DISCOVERY_2 = 2415     # Discovery port (secondary)
DISCOVERY_PORTS = [PORT_COMM, PORT_DISCOVERY_1, PORT_DISCOVERY_2]

# Connection modes
CONN_LOCAL = "local"            # Classic Broadlink UDP (local network)
CONN_CLOUD = "cloud"            # AUX Cloud API (remote)
CONF_CONN_MODE = "connection_mode"

# Cloud config keys
CONF_CLOUD_EMAIL = "cloud_email"
CONF_CLOUD_PASSWORD = "cloud_password"
CONF_CLOUD_REGION = "cloud_region"
CONF_CLOUD_DEVICES = "cloud_devices"
CONF_CLOUD_FAMILY = "cloud_family"

# Known response packet sizes (bytes)
PACKET_SIZE_SHORT = 72      # Short status response
PACKET_SIZE_MEDIUM = 88     # Medium response (state)
PACKET_SIZE_LONG = 136      # Full response (state + info)
KNOWN_PACKET_SIZES = [PACKET_SIZE_SHORT, PACKET_SIZE_MEDIUM, PACKET_SIZE_LONG]

# Temperature range
TEMP_MIN = 16
TEMP_MAX = 32
TEMP_STEP_HALF = 0.5
TEMP_STEP_FULL = 1.0

# Device modes
class AcMode:
    AUTO = 0
    COOLING = 1
    DRY = 2
    HEATING = 4
    FAN_ONLY = 6


# Fan speed
class FanSpeed:
    HIGH = 1
    MEDIUM = 2
    LOW = 3
    AUTO = 5


# Fixation (swing)
class Fixation:
    ON = 0   # Swinging
    OFF = 7  # Fixed


# Swing config options
SWING_HORIZONTAL = 1
SWING_VERTICAL = 2
SWING_BOTH = 3

CONF_SWING = "swing"
CONF_TEMP_STEP = "temp_step"
