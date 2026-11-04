"""1.6.2: solar forecast (Solcast): batteries full today, no more sun today."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from custom_components.zendure_ha import events, solar
from custom_components.zendure_ha.const import DeviceState, ManagerMode
from custom_components.zendure_ha.manager import ZendureManager

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 3, 14, 30, tzinfo=TZ)


def _hourly(values: dict[int, float]) -> list[dict[str, Any]]:
    return [{"period_start": f"2026-11-03T{h:02d}:00:00+01:00", "pv_estimate": v, "pv_estimate10": v / 2, "pv_estimate90": v * 1.5} for h, v in values.items()]


def test_forecast_today_adds_up_the_hours_from_now() -> None:
    state = SimpleNamespace(state="4.83", attributes={"unit_of_measurement": "kWh", "detailedHourly": _hourly({13: 0.62, 14: 0.69, 15: 0.31, 16: 0.08, 17: 0.0})})
    assert solar.remaining_kwh(state, NOW) == round(0.69 / 2 + 0.31 + 0.08, 3)  # half of the current hour


def test_half_hourly_forecast() -> None:
    periods = [{"period_start": "2026-11-03T14:00:00+01:00", "pv_estimate": 1.0}, {"period_start": "2026-11-03T14:30:00+01:00", "pv_estimate": 0.6}]
    state = SimpleNamespace(state="4.8", attributes={"detailedForecast": periods})
    assert solar.remaining_kwh(state, NOW) == 0.3  # 0.6 kW for half an hour


def test_remaining_today_sensor_and_wh() -> None:
    assert solar.remaining_kwh(SimpleNamespace(state="0.17", attributes={"unit_of_measurement": "kWh"}), NOW) == 0.17
    assert solar.remaining_kwh(SimpleNamespace(state="170", attributes={"unit_of_measurement": "Wh"}), NOW) == 0.17
    assert solar.remaining_kwh(SimpleNamespace(state="unavailable", attributes={}), NOW) is None
    assert solar.remaining_kwh(None, NOW) is None


def test_hours_to_sunset() -> None:
    sun = SimpleNamespace(state="above_horizon", attributes={"next_setting": "2026-11-03T16:45:00+01:00"})
    assert solar.hours_to_sunset(sun, NOW) == 2.25
    assert solar.hours_to_sunset(SimpleNamespace(state="below_horizon", attributes={}), NOW) == 0.0


def test_batteries_fill() -> None:
    assert solar.batteries_fill(0.4, 2.25, room_wh=8000) is False  # November: far too little sun
    assert solar.batteries_fill(12.0, 6, room_wh=8000) is True  # 12 kWh - 1.8 kWh house > 8 kWh room


def _manager(fills: bool | None, sun_low: bool = False) -> ZendureManager:
    m = object.__new__(ZendureManager)
    m.devices = []
    m.export_bias = 100
    m.p1_target = -50
    m.solar_fills = fills
    m.sun_low = sun_low
    m.__dict__["volatility"] = SimpleNamespace(peak_surplus=3000, active=True)
    return m


def test_no_margins_when_the_batteries_will_not_be_full() -> None:
    m = _manager(False)
    assert m.cloud_margin == 0
    assert m.p1_band(-30) == -30  # no band: the export is corrected
    m = _manager(True)
    assert m.cloud_margin == 50  # 100 W margin, 50 W of it already from the -50 W target
    assert m.p1_band(-30) == 0  # inside the band
    m = _manager(None)  # no forecast: as before
    assert m.cloud_margin == 50 and m.p1_band(-30) == 0


async def test_no_charger_kept_ready_without_more_sun() -> None:
    class _Dev:
        def __init__(self, name: str) -> None:
            self.deviceId = name
            self.state = DeviceState.INACTIVE
            self.prepared: list[int] = []

        async def prepare(self, direction: int) -> bool:
            self.prepared.append(direction)
            return True

    for low, expected in ((False, -1), (True, 1)):
        m = _manager(None, sun_low=low)
        m.operation = ManagerMode.MATCHING
        charger, discharger = _Dev("L1"), _Dev("L2")
        m.idle = [charger, discharger]
        m.roles = SimpleNamespace(charger="L1", discharger="L2")
        await m._prepare_roles()
        assert charger.prepared == [expected] and discharger.prepared == [1]


def test_update_solar_reads_the_sensor_and_logs_changes(monkeypatch) -> None:  # noqa: ANN001
    events.reset()
    m = _manager(None)
    m.solar_sensor = "sensor.solcast_pv_forecast_forecast_remaining_today"
    dev = SimpleNamespace(online=True, socSet=SimpleNamespace(asNumber=100), electricLevel=SimpleNamespace(asNumber=40), kWh=5.28)
    m.devices = [dev]
    states = {
        m.solar_sensor: SimpleNamespace(state="0.17", attributes={"unit_of_measurement": "kWh"}),
        "sun.sun": SimpleNamespace(state="above_horizon", attributes={"next_setting": (NOW + timedelta(hours=2)).isoformat()}),
    }
    m.hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
    monkeypatch.setattr("custom_components.zendure_ha.manager.dt_util.now", lambda: NOW)
    m._update_solar()
    assert (m.solar_remaining, m.solar_fills, m.sun_low) == (0.17, False, True)
    assert events.counters["solar"] == 1
    m._update_solar()
    assert events.counters["solar"] == 1  # only changes are logged
    m.solar_sensor = ""
    m._update_solar()
    assert (m.solar_fills, m.sun_low) == (None, False)
