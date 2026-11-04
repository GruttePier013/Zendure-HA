"""Solar forecast (1.6.2): how much sun is still expected today, from a Solcast (or similar) sensor.

Two kinds of sensors work:

* a "forecast today" sensor with the attribute ``detailedHourly`` (or ``detailedForecast``, per half hour), like
  ``sensor.solcast_pv_forecast_forecast_today``: the hours from now on are added up (the current one pro rata);
* a "remaining today" sensor, like ``sensor.solcast_pv_forecast_forecast_remaining_today``: its state is used.

The values are in kWh (a state in Wh is converted). Nothing here imports Home Assistant, so it is easy to test.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

SUN_LOW_KWH = 0.2  # kWh: less sun than this still expected today counts as "no more sun"
HOUSE_W = 300  # W: what the house uses on average while the sun still shines (for "will the batteries be full")


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def remaining_kwh(state: Any, now: datetime) -> float | None:
    """kWh of sun still expected today, or None when the sensor has no usable value."""
    if state is None or str(getattr(state, "state", "")) in ("", "unknown", "unavailable", "None"):
        return None
    attrs = getattr(state, "attributes", {}) or {}
    for key, hours in (("detailedForecast", 0.5), ("detailedHourly", 1.0)):
        periods = attrs.get(key)
        if not isinstance(periods, list) or not periods:
            continue
        total = 0.0
        valid = False
        for item in periods:
            if not isinstance(item, dict):
                continue
            start = _parse(item.get("period_start"))
            try:
                kw = float(item.get("pv_estimate", 0) or 0)
            except (TypeError, ValueError):
                continue
            if start is None:
                continue
            if start.tzinfo is None and now.tzinfo is not None:
                start = start.replace(tzinfo=now.tzinfo)
            valid = True
            end = start + timedelta(hours=hours)
            if end <= now:
                continue
            part = 1.0 if start >= now else (end - now).total_seconds() / (hours * 3600)
            total += kw * hours * part  # pv_estimate is the average power (kW) of the period
        if valid:
            return round(total, 3)
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    return value / 1000 if attrs.get("unit_of_measurement") == "Wh" else value


def hours_to_sunset(sun: Any, now: datetime) -> float:
    """Hours until sunset from the sun.sun entity (0 when the sun is down or unknown)."""
    if sun is None or getattr(sun, "state", None) != "above_horizon":
        return 0.0
    setting = _parse((getattr(sun, "attributes", {}) or {}).get("next_setting"))
    if setting is None:
        return 0.0
    if setting.tzinfo is None and now.tzinfo is not None:
        setting = setting.replace(tzinfo=now.tzinfo)
    return max(0.0, (setting - now).total_seconds() / 3600)


def batteries_fill(remaining: float, hours: float, room_wh: float, house_w: float = HOUSE_W) -> bool:
    """True when the sun still expected today can fill the free room of the batteries (after the house took its part)."""
    return remaining * 1000 - house_w * hours >= room_wh
