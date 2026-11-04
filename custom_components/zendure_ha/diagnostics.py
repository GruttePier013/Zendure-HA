"""Diagnostics of the Zendure integration (Download diagnostics in Home Assistant)."""

from __future__ import annotations

from datetime import datetime
from time import monotonic
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import events
from .const import CONF_APPTOKEN, CONF_MQTTPSW, CONF_MQTTUSER, CONF_WIFIPSW, CONF_WIFISSID
from .manager import ZendureConfigEntry

TO_REDACT = {CONF_APPTOKEN, "p1_homewizard_token", CONF_MQTTPSW, CONF_MQTTUSER, CONF_WIFIPSW, CONF_WIFISSID, "sn", "snNumber", "serial_number", "deviceKey", "device_id", "ip", "ipAddress", "password", "username", "clientId"}


def _device(device: Any) -> dict[str, Any]:
    return {
        "name": device.name,
        "model": device.attr_device_info.get("model"),
        "online": device.online,
        "state": device.state.name,
        "connection": device.connection.current_option,
        "address": device.ipAddress,
        "address_source": getattr(device, "address_source", None),
        "ac_mode": getattr(getattr(device, "acMode", None), "current_option", None),
        "ac_mode_sent": {1: "input", 2: "output"}.get(getattr(device, "ac_mode_sent", None)),
        "input_limit": device.limitInput.asInt,
        "output_limit": device.limitOutput.asInt,
        "home_input": device.homeInput.asInt,
        "home_output": device.homeOutput.asInt,
        "commanded": device.slew.current,
        "kwh": device.kWh,
        "soc": device.electricLevel.asInt,
        "limits": {"charge": device.charge_limit, "discharge": device.discharge_limit},
        "direction": {"pending": device.slew.pending, "flip_delay": device.slew.flip_delay, "min_run": device.slew.min_run},
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
        "cycles": manager.cycle_total,
        "control": {
            "p1_values": getattr(manager, "p1_values", 0),
            "p1_per_minute": manager.p1_rate() if hasattr(manager, "p1_rate") else None,
            "cycle_running": getattr(manager, "zero_fast", None) == datetime.max,
            "last_cycle": manager.last_cycle.isoformat(timespec="seconds") if getattr(manager, "last_cycle", None) else None,
            "cycle_errors": getattr(manager, "cycle_errors", 0),
            "last_cycle_error": getattr(manager, "last_cycle_error", None),
            "last_cycle_stuck": getattr(manager, "last_cycle_stuck", None),
            "cycle_timeouts": getattr(manager, "cycle_timeouts", 0),
            "fast_track_n": getattr(manager, "fast_track_total", 0),
            "direction_slow_n": events.counters["dir_slow"],
            "stale_n": getattr(manager, "stale_total", 0),
            "last_direction_change_s": {d.name: getattr(d, "last_dir_change_s", None) for d in manager.devices if getattr(d, "last_dir_change_s", None) is not None},
            "direction_change_running": {
                d.name: f"{round(monotonic() - c['start'])} s" for d in manager.devices if (c := getattr(d, "dir_change", None)) is not None
            },
            "last_event": dict(events.last) or None,
            "events": dict(events.counters),
        },
        "clouds": (
            {"active": v.active, "edges": v.edges, "edge_threshold_w": v.threshold, "peak_surplus_w": v.peak_surplus}
            if (v := getattr(manager, "volatility", None)) is not None
            else None
        ),
        "p1_recording": (
            f"{rec.path} ({rec.lines} values, control state {'on' if rec.detail else 'off'}, until midnight)"
            if (rec := getattr(manager, "p1record", None)) is not None
            else None
        ),
        "p1_source": (
            f"HomeWizard {p1.host} API {p1.api} ({'ok' if p1.healthy else f'not answering, {p1.failures} failures: sensor'})"
            if (p1 := getattr(manager, "p1direct", None)) is not None
            else "sensor"
        ),
        "p1_stale": getattr(manager, "p1_stale", False),
        "sleep_after_min": round(getattr(manager, "sleep_after", 0) / 60, 1),
        "asleep": [d.name for d in manager.devices if getattr(d, "asleep", False)],
        "foreign_writes_15min": {
            d.name: n for d in manager.devices if (n := len([t for t in getattr(d, "foreign_writes", []) if monotonic() - t < 900]))
        },
        "roles": {
            "discharger": names.get(manager.roles.discharger or "", None),
            "charger": names.get(manager.roles.charger or "", None),
        },
        "devices": [async_redact_data(_device(d), TO_REDACT) for d in manager.devices],
    }
