"""1.7.0: a change of direction straight through 0 W (select *Direction change: direct*, only the AC mode changes)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from custom_components.zendure_ha import events
from custom_components.zendure_ha.device import DIRECTION_CHANGE
from custom_components.zendure_ha.entity import CONFIG_KEYS
from custom_components.zendure_ha.slew import FLIP_MIN
from tests.test_manager_dispatch import _FakeDevice, _make_manager_harness


@pytest.fixture(autouse=True)
def _clean_events() -> None:
    events.reset()


def _props(device) -> list[dict]:  # noqa: ANN001
    return [c["properties"] for c in device.mqtt_commands]


def test_the_pause_at_0_w_stays_2_s() -> None:
    """0.25 s was simulated in 1.7.0: no gain with three inverters, so the margin for the relay stays."""
    assert FLIP_MIN == 2.0


async def test_the_select_switches_the_limiter(zensdk_device) -> None:  # noqa: ANN001
    assert DIRECTION_CHANGE == ("via_zero", "direct")
    assert "direction_change" not in CONFIG_KEYS  # in Controls, next to the AC mode and the limits
    assert zensdk_device.slew.direct is False
    zensdk_device.update_direction_change(None, 1)  # the select passes the key ...
    assert zensdk_device.slew.direct is True
    zensdk_device.update_direction_change(None, "via_zero")  # ... or the name after a restore
    assert zensdk_device.slew.direct is False
    zensdk_device.update_direction_change(None, "bogus")
    assert zensdk_device.slew.direct is False


async def test_direct_switches_only_the_ac_mode_in_one_command(zensdk_device) -> None:  # noqa: ANN001
    """As tested on 18 November: both limits set, only the AC mode changes; no 0 W steps, no pause."""
    zensdk_device.update_direction_change(None, "direct")
    zensdk_device.setLimits(-2400, 2400)
    zensdk_device.slew.min_run = 0
    await zensdk_device.discharge(1000)
    zensdk_device.slew.current = 1000
    zensdk_device.homeOutput._attr_native_value = 1000  # still delivering
    n = len(zensdk_device.mqtt_commands)
    seen: list[str] = []
    events.set_writer(lambda event, _device, detail: seen.append(f"{event} {detail}"))
    await zensdk_device.power_charge(-1400)
    events.set_writer(None)
    assert _props(zensdk_device)[n:] == [{"smartMode": 1, "acMode": 1, "outputLimit": 1000, "inputLimit": 1400}]
    assert events.counters["dir_direct"] == 1 and any(x.startswith("dir_direct from=discharge to=charge") for x in seen)
    zensdk_device.homeOutput._attr_native_value, zensdk_device.homeInput._attr_native_value = 0, 1300
    await zensdk_device.power_charge(-1500)  # running in the new direction: the old limit is cleared
    assert _props(zensdk_device)[-1] == {"smartMode": 1, "acMode": 1, "outputLimit": 0, "inputLimit": 1500}


async def test_via_zero_still_stops_first(zensdk_device) -> None:  # noqa: ANN001
    zensdk_device.setLimits(-2400, 2400)
    zensdk_device.slew.min_run = 0
    await zensdk_device.discharge(1000)
    zensdk_device.slew.current = 1000
    zensdk_device.homeOutput._attr_native_value = 1000
    n = len(zensdk_device.mqtt_commands)
    await zensdk_device.power_charge(-1400)  # held at 0 W first: only a stop goes out
    assert [(p["outputLimit"], p["inputLimit"]) for p in _props(zensdk_device)[n:]] == [(0, 0)]
    assert events.counters["dir_direct"] == 0


# ----------------------------------------------------------------------------------------------
# manager: a direct inverter that runs the other way turns instead of a start of another one
# ----------------------------------------------------------------------------------------------
def _device(level: int, current: int, direct: bool) -> _FakeDevice:
    d = _FakeDevice(pwr_max=-2400, electric_level=level)
    d.slew = SimpleNamespace(current=current, direct=direct)
    d.charge_limit, d.discharge_limit, d.charge_optimal, d.discharge_optimal = -2400, 2400, -600, 600
    d.fuseGrp = None

    async def power_charge(power: int) -> int:
        d.charge_calls.append(power)
        d.slew.current = power
        return power

    async def power_discharge(power: int) -> int:
        d.discharge_calls.append(power)
        d.slew.current = power
        return power

    d.power_charge, d.power_discharge = power_charge, power_discharge
    return d


def _manager(running, idle) -> object:  # noqa: ANN001
    m = _make_manager_harness()
    m.discharge, m.idle, m.devices = list(running), list(idle), [*running, *idle]
    m.idle_lvlmax = max((d.electricLevel.asInt for d in idle), default=0)
    m.idle_lvlmin = min((d.electricLevel.asInt for d in idle), default=100)
    m.cloud_reserve = None
    return m


async def test_sun_while_discharging_turns_the_direct_inverter() -> None:
    running, ready = _device(80, 900, direct=True), _device(40, 0, direct=False)
    m = _manager([running], [ready])
    m.roles.charger = ready.deviceId
    await m.power_charge(-1500, datetime(2026, 11, 18, 12, 0), hysteresis=False)
    assert running.charge_calls and running.charge_calls[-1] < 0  # turned straight to charging
    assert running.discharge_calls == []  # not stopped first
    assert ready.charge_calls == []  # no start of the standby inverter (9-11 s)


async def test_without_direct_the_running_inverter_stops_and_the_charger_starts() -> None:
    running, ready = _device(80, 900, direct=False), _device(40, 0, direct=False)
    m = _manager([running], [ready])
    m.roles.charger = ready.deviceId
    await m.power_charge(-1500, datetime(2026, 11, 18, 12, 0), hysteresis=False)
    assert running.discharge_calls == [0]
    assert ready.charge_calls and ready.charge_calls[-1] < 0


async def test_a_peak_while_charging_turns_the_direct_charger() -> None:
    charger, ready = _device(40, -1200, direct=True), _device(80, 0, direct=False)
    m = _manager([], [ready])
    m.charge = [charger]
    m.devices.append(charger)
    m.roles.discharger = ready.deviceId
    await m.power_discharge(1800, datetime(2026, 11, 18, 12, 0))
    assert charger.discharge_calls and charger.discharge_calls[-1] > 0
    assert ready.discharge_calls == [] or ready.discharge_calls[-1] >= 0


async def test_a_direct_inverter_that_is_not_needed_stops() -> None:
    running, other = _device(80, 900, direct=True), _device(40, -800, direct=False)
    m = _manager([running], [])
    m.charge, m.devices, m.idle_lvlmax, m.idle_lvlmin = [other], [running, other], 0, 100
    m.charge_limit, m.charge_weight, m.charge_optimal = -2400, other.pwr_max * (100 - 40), -600
    await m.power_charge(-700, datetime(2026, 11, 18, 12, 0), hysteresis=False)  # the running charger takes it
    assert running.discharge_calls == [0]


async def test_the_charge_delay_stops_the_direct_inverter_and_then_it_starts() -> None:
    """Discharging -> charging keeps the charge delay (2 s the first time): the inverter stops as before."""
    from datetime import timedelta

    t0 = datetime(2026, 11, 18, 12, 0)
    running = _device(80, 900, direct=True)
    m = _manager([running], [])
    m.charge_last = t0 - timedelta(minutes=10)  # no charge in the last 5 minutes: the short first delay
    await m.power_charge(-1500, t0)
    assert running.discharge_calls == [0] and running.charge_calls == []


async def test_a_long_charge_delay_stops_the_direct_inverter() -> None:
    from datetime import timedelta

    t0 = datetime(2026, 11, 18, 12, 0)
    running = _device(80, 900, direct=True)
    m = _manager([running], [])
    m.charge_last = t0 - timedelta(seconds=60)  # charged a minute ago: the 60 s delay
    await m.power_charge(-500, t0)
    assert running.discharge_calls == [0]
