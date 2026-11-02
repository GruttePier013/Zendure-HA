"""Diagnostics of the Zendure integration (Download diagnostics in Home Assistant)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_APPTOKEN, CONF_MQTTPSW, CONF_MQTTUSER, CONF_WIFIPSW, CONF_WIFISSID
from .manager import ZendureConfigEntry

TO_REDACT = {CONF_APPTOKEN, CONF_MQTTPSW, CONF_MQTTUSER, CONF_WIFIPSW, CONF_WIFISSID, "sn", "snNumber", "serial_number", "deviceKey", "device_id", "ip", "ipAddress", "password", "username", "clientId"}


def _device(device: Any) -> dict[str, Any]:
    return {
        "name": device.name,
        "model": device.attr_device_info.get("model"),
        "online": device.online,
        "state": device.state.name,
        "connection": device.connection.current_option,
        "address": device.ipAddress,
        "kwh": device.kWh,
        "soc": device.electricLevel.asInt,
        "limits": {"charge": device.charge_limit, "discharge": device.discharge_limit},
        "slew": {"enabled": device.slew.enabled, "up": device.slew.up, "down": device.slew.down, "pending": device.slew.pending},
        "flash_writes": device.flash_count,
        "http_failures": getattr(device, "_http_failures", 0),
        "sn": device.snNumber,
    }


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ZendureConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry, with tokens, credentials and serial numbers redacted."""
    manager = entry.runtime_data
    names = {d.deviceId: d.name for d in manager.devices}
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "operation": manager.operation.name,
        "cycles": manager.cycleCount.asInt,
        "roles": {
            "discharger": names.get(manager.roles.discharger or "", None),
            "charger": names.get(manager.roles.charger or "", None),
        },
        "devices": [async_redact_data(_device(d), TO_REDACT) for d in manager.devices],
    }
