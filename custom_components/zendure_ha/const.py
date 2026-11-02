"""Constants for Zendure."""

from datetime import timedelta
from enum import Enum

DOMAIN = "zendure_ha"

CONF_APPTOKEN = "token"
CONF_P1METER = "p1meter"
CONF_PRICE = "price"
CONF_MQTTLOG = "mqttlog"
CONF_MQTTLOCAL = "mqttlocal"
CONF_MQTTSERVER = "mqttserver"
CONF_SIM = "simulation"
CONF_MQTTPORT = "mqttport"
CONF_MQTTUSER = "mqttuser"
CONF_MQTTPSW = "mqttpsw"
CONF_WIFISSID = "wifissid"
CONF_WIFIPSW = "wifipsw"
CONF_AUTO_MQTT_USER = "auto_mqtt_user"

# power control (options menu)
CONF_SLEW_ENABLED = "slew_enabled"
CONF_SLEW_UP = "slew_up"
CONF_SLEW_DOWN = "slew_down"
CONF_FLIP_DELAY = "flip_delay"
CONF_TIMEZERO = "time_zero"
CONF_TIMEFAST = "time_fast"
CONF_P1_STDDEV_MIN = "p1_stddev_min"
CONF_POWER_START = "power_start"
CONF_POWER_TOLERANCE = "power_tolerance"

CONF_PEAK_LIMIT = "peak_limit"
CONF_MANUAL_INTERVAL = "manual_interval"
CONF_ROLE_MARGIN = "role_margin"
CONF_FILL_LEVEL = "fill_level"
CONF_FLASH_SAFE = "flash_safe_state"
CONF_MIN_RUN = "min_run"
CONF_READY_RATE = "ready_rate"
CONF_DEVICE_IPS = "device_ips"

CONF_HAKEY = "C*dafwArEOXK"


class AcMode:
    INPUT = 1
    OUTPUT = 2


class DeviceState(Enum):
    OFFLINE = 0
    SOCEMPTY = 1
    INACTIVE = 2
    SOCFULL = 3
    ACTIVE = 4


class ManagerMode(Enum):
    OFF = 0
    MANUAL = 1
    MATCHING = 2
    MATCHING_DISCHARGE = 3
    MATCHING_CHARGE = 4
    STORE_SOLAR = 5


class ManagerState(Enum):
    IDLE = 0
    CHARGE = 1
    DISCHARGE = 2
    OFF = 3


class SmartMode:
    SOCFULL = 1
    SOCEMPTY = 2
    ZENSDK = 2
    CONNECTED = 10

    TIMEFAST = 2.2  # Fast update interval after significant change
    TIMEZERO = 4  # Normal update interval
    RAMP_INTERVAL = 1.0  # s between two steps while a device is ramping (one step = at most 1 s of slew rate)
    MANUAL_INTERVAL = 15  # Update interval in manual mode: a fixed power does not need to follow the P1 meter
    OFF_INTERVAL = 60  # Update interval when the manager is off (only the sensors are refreshed)
    MQTT_ISSUE_AFTER = 3  # failed 60 s checks of a MQTT broker before a repair issue is raised

    # Standard deviation thresholds for detecting significant changes
    P1_STDDEV_FACTOR = 3.5  # Multiplier for P1 meter stddev calculation
    P1_STDDEV_MIN = 15  # Minimum stddev value for P1 changes (watts)
    P1_MIN_UPDATE = timedelta(milliseconds=400)
    SETPOINT_STDDEV_FACTOR = 5.0  # Multiplier for power average stddev calculation
    SETPOINT_STDDEV_MIN = 50  # Minimum stddev value for power average (watts)

    HEMSOFF_TIMEOUT = 60  # Seconds before HEMS state is set to OFF if no updates are received

    POWER_START = 50  # Minimum Power (W) for starting a device
    FILL_LEVEL = 70  # % of its limit a device carries before another device is started (the others stay ready)
    FILL_HYSTERESIS = 5  # % points below the fill level before a surplus charging device is released
    CROSSFLOW_WARN = 5  # cycles with one device charging and another discharging before a warning is logged
    RELEASE_HOLD = 30  # seconds a released device stays in standby (unless it is needed), against reporting lag
    RELEASE_LOOKBACK = 180  # s: a device is only released when the others can carry the highest load of this period
    CONSOLIDATE_DELAY = 30  # seconds the load must stay low before a surplus charging device is released
    POWER_TOLERANCE = 5  # Device-level power tolerance (W) before updating


class Flash:
    """Flash wear protection of the device (zenSDK: smartMode=1 keeps a write out of the flash)."""

    IDLE_PERSIST = 1800  # seconds a device must be idle at 0 W before the safe 0 W state is written to flash once
    MIN_INTERVAL = 3600  # minimum seconds between two automatic flash writes of one device
    SAME_VALUE_WINDOW = 60  # seconds in which an identical property write is not repeated
    PROPERTY_INTERVAL = 120  # minimum seconds between two persistent writes of the same property; newer values are coalesced


class Http:
    """Local zenSDK HTTP behaviour (the ESP32 of the device has very little RAM)."""

    TIMEOUT = 5  # seconds per request
    MIN_INTERVAL = 0.5  # seconds between two requests to the same device
    OFFLINE_AFTER = 3  # consecutive failures before the device is marked offline
    BACKOFF_BASE = 5  # seconds, doubled per failure
    BACKOFF_MAX = 60  # seconds
    ISSUE_AFTER = 5  # consecutive failures before a repair issue is raised
    POLL_ACTIVE = 2.0  # seconds between two reports while the device is active
    POLL_IDLE = 30.0  # seconds between two reports while the device is completely idle


# Defaults of the settings that can be changed in the options menu
CONTROL_DEFAULTS = {
    CONF_PEAK_LIMIT: 0,
    CONF_MANUAL_INTERVAL: 15,
    CONF_ROLE_MARGIN: 10,
    CONF_FILL_LEVEL: 70,
    CONF_TIMEZERO: 4,
    CONF_TIMEFAST: 2.2,
    CONF_P1_STDDEV_MIN: 15,
    CONF_POWER_START: 50,
    CONF_POWER_TOLERANCE: 5,
}
