"""Charging is consolidated on as few devices as possible; the others stay ready for peaks."""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.zendure_ha.const import DeviceState
from tests.test_manager_dispatch import _FakeDevice, _make_manager_harness

T0 = datetime(2026, 1, 1, 12, 0, 0)


def _charging(levels: list[int]):  # noqa: ANN202
    manager = _make_manager_harness()
    devices = []
    for level in levels:
        d = _FakeDevice(pwr_max=-2400, electric_level=level)
        d.charge_start, d.charge_optimal = -240, -600
        devices.append(d)
    manager.charge = devices
    manager.charge_limit = -2400 * len(devices)
    manager.charge_optimal = -600 * len(devices)
    return manager, devices


async def _cycle(manager, devices, total: int, now: datetime) -> None:  # noqa: ANN001
    manager.charge_weight = sum(d.pwr_max * (100 - d.electricLevel.asInt) for d in devices)
    await manager.power_charge(total, now, hysteresis=False)


async def test_surplus_charger_is_released_after_the_delay() -> None:
    manager, devices = _charging([40, 50, 60])  # the last one has the highest SoC: it is kept ready
    await _cycle(manager, devices, -3000, T0)
    assert all(d.charge_calls[-1] != 0 for d in devices)  # delay not over yet
    await _cycle(manager, devices, -3000, T0 + timedelta(seconds=31))
    assert devices[2].charge_calls[-1] == 0
    assert devices[0].charge_calls[-1] + devices[1].charge_calls[-1] == -3000


async def test_one_device_is_enough_for_1500_w() -> None:
    manager, devices = _charging([40, 70])
    await _cycle(manager, devices, -1500, T0)
    await _cycle(manager, devices, -1500, T0 + timedelta(seconds=31))
    assert devices[1].charge_calls[-1] == 0
    assert devices[0].charge_calls[-1] == -1500


async def test_no_release_when_all_devices_are_needed() -> None:
    manager, devices = _charging([40, 50, 60])
    for i in range(3):
        await _cycle(manager, devices, -4500, T0 + timedelta(seconds=40 * i))
    assert all(d.charge_calls[-1] != 0 for d in devices)


async def test_short_dip_does_not_release_a_device() -> None:
    manager, devices = _charging([40, 70])
    await _cycle(manager, devices, -1500, T0)
    await _cycle(manager, devices, -3000, T0 + timedelta(seconds=20))  # load is back: the timer restarts
    await _cycle(manager, devices, -1500, T0 + timedelta(seconds=35))
    assert devices[1].charge_calls[-1] != 0


async def test_ready_device_takes_over_a_peak_at_once() -> None:
    manager, charging = _charging([40, 50])
    ready = _FakeDevice(pwr_max=2400, electric_level=80)
    ready.discharge_limit, ready.discharge_optimal = 2400, 600
    ready.state = DeviceState.INACTIVE
    manager.idle = [ready]
    manager.roles.discharger = ready.deviceId

    await manager.power_discharge(2200)  # kettle and coffee machine

    assert ready.discharge_calls == [2200]
    assert all(d.discharge_calls == [0] for d in charging)


def _idle(level: int, ready: bool = False):  # noqa: ANN202
    d = _FakeDevice(pwr_max=2400, electric_level=level)
    d.discharge_limit, d.discharge_optimal = 2400, 600
    d.charge_limit, d.charge_optimal = -2400, -600
    return d


async def test_another_idle_device_covers_the_peak_when_the_ready_one_is_busy() -> None:
    manager = _make_manager_harness()
    other = _idle(60)
    manager.idle = [other]
    manager.roles.discharger = "someone-else"  # e.g. still charging
    await manager.power_discharge(2200)
    assert other.discharge_calls == [2200]


async def test_peak_above_one_device_starts_a_second_with_the_rest() -> None:
    manager = _make_manager_harness()
    ready, second = _idle(80), _idle(60)
    manager.idle = [second, ready]
    manager.roles.discharger = ready.deviceId
    await manager.power_discharge(3500)  # kettle + coffee machine + oven
    assert ready.discharge_calls == [2400]
    assert second.discharge_calls == [1100]


async def test_peak_above_the_running_device_adds_the_missing_part() -> None:
    manager = _make_manager_harness()
    running, second = _idle(80), _idle(60)
    manager.discharge = [running]
    manager.discharge_limit, manager.discharge_optimal = 2400, 600
    manager.discharge_weight = running.pwr_max * running.electricLevel.asInt
    manager.idle = [second]
    await manager.power_discharge(3500)
    assert running.discharge_calls == [2400]
    assert second.discharge_calls == [1100]


async def _discharging_cycle(manager, devices, total: int, now: datetime) -> None:  # noqa: ANN001
    manager.discharge_weight = sum(d.pwr_max * d.electricLevel.asInt for d in devices)
    await manager.power_discharge(total, now)


def _discharging(levels: list[int]):  # noqa: ANN202
    manager = _make_manager_harness()
    devices = [_idle(level) for level in levels]
    manager.discharge = devices
    manager.discharge_limit = 2400 * len(devices)
    manager.discharge_optimal = 600 * len(devices)
    return manager, devices


async def test_after_the_peak_the_second_device_is_released_after_the_delay() -> None:
    manager, devices = _discharging([40, 80])  # the one with the lowest SoC is released (it is ready to charge)
    await _discharging_cycle(manager, devices, 1000, T0)
    assert all(d.discharge_calls[-1] != 0 for d in devices)
    await _discharging_cycle(manager, devices, 1000, T0 + timedelta(seconds=31))
    assert devices[0].discharge_calls[-1] == 0
    assert devices[1].discharge_calls[-1] == 1000


async def test_kettle_switching_on_and_off_does_not_release_the_second_device() -> None:
    manager, devices = _discharging([40, 80])
    await _discharging_cycle(manager, devices, 1000, T0)
    await _discharging_cycle(manager, devices, 3500, T0 + timedelta(seconds=20))  # kettle on again: timer restarts
    await _discharging_cycle(manager, devices, 1000, T0 + timedelta(seconds=35))
    assert devices[0].discharge_calls[-1] != 0


async def test_full_device_passing_solar_is_never_released() -> None:
    manager, devices = _discharging([100, 80])
    devices[0].state = DeviceState.SOCFULL
    for i in range(3):
        await _discharging_cycle(manager, devices, 600, T0 + timedelta(seconds=40 * i))
    assert devices[0].discharge_calls[-1] != 0


async def test_export_above_one_charger_starts_a_second_with_the_rest() -> None:
    manager, devices = _charging([40])
    devices[0].charge_limit = -2400
    other = _idle(30)
    manager.idle = [other]
    await _cycle(manager, devices, -3500, T0)
    assert devices[0].charge_calls == [-2400]
    assert other.charge_calls == [-1100]


async def test_charger_ramps_down_in_parallel_with_the_ready_device() -> None:
    """The charger may only finish after the ready device was commanded: with sequential calls this deadlocks."""
    import asyncio

    manager, charging = _charging([40])
    ready = _FakeDevice(pwr_max=2400, electric_level=80)
    ready.discharge_limit, ready.discharge_optimal = 2400, 600
    manager.idle = [ready]
    manager.roles.discharger = ready.deviceId
    started = asyncio.Event()

    async def slow_stop(power: int) -> int:
        await started.wait()  # e.g. a slow ESP32
        charging[0].discharge_calls.append(power)
        return power

    async def start(power: int, **_kwargs: bool) -> int:
        ready.discharge_calls.append(power)
        started.set()
        return power

    charging[0].power_discharge = slow_stop
    ready.power_discharge = start
    await asyncio.wait_for(manager.power_discharge(700), timeout=2)
    assert ready.discharge_calls == [700]
    assert charging[0].discharge_calls == [0]


async def test_a_failing_device_does_not_block_the_others() -> None:
    manager, charging = _charging([40])

    async def broken(_power: int) -> int:
        raise RuntimeError("device gone")

    charging[0].power_discharge = broken
    ready = _FakeDevice(pwr_max=2400, electric_level=80)
    ready.discharge_limit, ready.discharge_optimal = 2400, 600
    manager.idle = [ready]
    manager.roles.discharger = ready.deviceId
    await manager.power_discharge(700)
    assert ready.discharge_calls == [700]


def test_crossflow_is_logged_once_after_a_few_cycles(caplog) -> None:  # noqa: ANN001
    manager, charging = _charging([40])
    other = _FakeDevice(pwr_max=2400, electric_level=80)
    charging[0].name, other.name = "A", "B"
    manager.discharge = [other]
    for k in range(20):
        manager._check_crossflow(T0 + timedelta(seconds=k))  # 20 s with charging and discharging at once
    assert sum("does not follow" in r.message for r in caplog.records) == 1
    manager.discharge = []
    manager._check_crossflow()
    assert manager.crossflow_cycles == 0


async def test_a_started_device_respects_its_fuse_group() -> None:
    from custom_components.zendure_ha.fusegroup import FuseGroup

    manager = _make_manager_harness()
    running, second = _idle(80), _idle(60)
    group = FuseGroup("group", 3000, -3000)
    running.fuseGrp = second.fuseGrp = group
    manager.discharge = [running]
    manager.discharge_limit, manager.discharge_optimal = 2400, 600
    manager.discharge_weight = running.pwr_max * running.electricLevel.asInt
    manager.idle = [second]
    await manager.power_discharge(3500)
    assert running.discharge_calls == [2400]
    assert second.discharge_calls == [600]  # 3000 W group limit - 2400 W


async def test_no_start_when_the_fuse_group_is_full() -> None:
    from custom_components.zendure_ha.fusegroup import FuseGroup

    manager = _make_manager_harness()
    running, second = _idle(80), _idle(60)
    group = FuseGroup("group", 2400, -2400)
    running.fuseGrp = second.fuseGrp = group
    manager.discharge = [running]
    manager.discharge_limit, manager.discharge_optimal = 2400, 600
    manager.discharge_weight = running.pwr_max * running.electricLevel.asInt
    manager.idle = [second]
    await manager.power_discharge(3500)
    assert second.discharge_calls == []


async def test_the_ready_device_starts_first_and_takes_the_most() -> None:
    """1.6.0: no faster ramp any more (the inverter ramps by itself), but the ready device is still the first choice."""
    manager = _make_manager_harness()
    ready, second = _idle(80), _idle(60)
    manager.idle = [second, ready]
    manager.roles.discharger = ready.deviceId
    await manager.power_discharge(3500)
    assert ready.discharge_calls[0] >= second.discharge_calls[0]
    assert ready.discharge_calls[0] + second.discharge_calls[0] == 3500


async def test_released_device_with_a_lagging_report_stays_in_standby() -> None:
    """After a release the device may still report some output; it must not get a share again (no flapping)."""
    manager, devices = _discharging([40, 80])
    await _discharging_cycle(manager, devices, 1000, T0)
    await _discharging_cycle(manager, devices, 1000, T0 + timedelta(seconds=31))
    assert devices[0].discharge_calls[-1] == 0
    # next cycles: the released device still shows up as discharging (report lag)
    for k in range(1, 4):
        manager.discharge = list(devices)
        manager.discharge_limit, manager.discharge_optimal = 4800, 1200
        manager.idle = []
        await _discharging_cycle(manager, devices, 1000, T0 + timedelta(seconds=31 + 4 * k))
    assert devices[0].discharge_calls[-1] == 0
    assert devices[1].discharge_calls[-1] == 1000


async def test_released_device_can_still_start_when_needed() -> None:
    manager, devices = _discharging([40, 80])
    await _discharging_cycle(manager, devices, 1000, T0)
    await _discharging_cycle(manager, devices, 1000, T0 + timedelta(seconds=31))
    # kettle: the load is now too high for one device, the released one is started by the start rule
    manager.discharge = [devices[1]]
    manager.discharge_limit, manager.discharge_optimal = 2400, 600
    manager.idle = [devices[0]]
    manager.discharge_weight = devices[1].pwr_max * devices[1].electricLevel.asInt
    await manager.power_discharge(3200, T0 + timedelta(seconds=35))
    assert devices[0].discharge_calls[-1] == 800


async def test_passing_clouds_do_not_release_a_charger() -> None:
    """Sun and clouds alternate every 40 s: the device is needed again soon, so it stays on (no relay wear)."""
    manager, devices = _charging([40, 50, 60])
    for k in range(12):
        await _cycle(manager, devices, -4200 if k % 2 == 0 else -500, T0 + timedelta(seconds=40 * k))
        await _cycle(manager, devices, -4200 if k % 2 == 0 else -500, T0 + timedelta(seconds=40 * k + 35))
    assert all(d.charge_calls[-1] != 0 for d in devices)


async def test_charger_is_released_after_the_clouds_stay_away() -> None:
    manager, devices = _charging([40, 50, 60])
    await _cycle(manager, devices, -4200, T0)
    for k in range(1, 6):
        await _cycle(manager, devices, -1000, T0 + timedelta(seconds=40 * k))
        assert devices[2].charge_calls[-1] != 0  # the 4200 W peak is less than 3 minutes (+30 s) ago
    await _cycle(manager, devices, -1000, T0 + timedelta(seconds=240))
    assert devices[2].charge_calls[-1] == 0


async def test_idle_role_holders_are_prepared() -> None:
    from custom_components.zendure_ha.const import ManagerMode

    manager = _make_manager_harness()
    manager.operation = ManagerMode.MATCHING
    charger, discharger = _idle(30), _idle(80)
    calls = []

    async def prep_c(direction: int) -> bool:
        calls.append(("c", direction))
        return True

    async def prep_d(direction: int) -> bool:
        calls.append(("d", direction))
        return True

    charger.prepare, discharger.prepare = prep_c, prep_d
    manager.idle = [charger, discharger]
    manager.roles.charger, manager.roles.discharger = charger.deviceId, discharger.deviceId
    await manager._prepare_roles()
    assert calls == [("c", -1), ("d", 1)]


async def test_below_about_2000_w_two_chargers_become_one() -> None:
    """Default fill level 85 %: 1800 W on two 2400 W devices is consolidated to one (better efficiency)."""
    manager, devices = _charging([40, 70])
    assert manager.fill_level == 85
    await _cycle(manager, devices, -1800, T0)
    await _cycle(manager, devices, -1800, T0 + timedelta(seconds=31))
    assert devices[1].charge_calls[-1] == 0
    assert devices[0].charge_calls[-1] == -1800


async def test_above_about_2000_w_two_chargers_stay() -> None:
    manager, devices = _charging([40, 70])
    for k in range(3):
        await _cycle(manager, devices, -2100, T0 + timedelta(seconds=40 * k))
    assert all(d.charge_calls[-1] != 0 for d in devices)


class _Sink:
    def __init__(self) -> None:
        self.value = None
        self.hass = None
        self._attr_extra_state_attributes = None

    def update_value(self, value):  # noqa: ANN001, ANN201
        changed = value != self.value
        self.value = value
        return changed


def test_role_sensors_show_what_the_device_does_now() -> None:
    from types import SimpleNamespace

    manager = _make_manager_harness()
    a, b, c = _idle(40), _idle(50), _idle(80)
    for d, name in ((a, "L1"), (b, "L2"), (c, "L3")):
        d.name = name
    manager.devices = [a, b, c]
    manager.charge, manager.discharge, manager.idle = [], [c], [b]  # a does not respond
    manager.roles.charger, manager.roles.discharger = b.deviceId, c.deviceId
    manager.roleCharge, manager.roleDischarge = _Sink(), _Sink()
    manager.hass = SimpleNamespace(config=SimpleNamespace(language="nl"))
    manager._show_roles()
    assert manager.roleCharge.value == "L2 (stand-by)"  # ready to charge, not charging: matches 'devices charging 0'
    assert manager.roleDischarge.value == "L3 (ontladen)"
    manager.roles.charger = a.deviceId
    manager._show_roles()
    assert manager.roleCharge.value == "L1 (reageert niet)"


def test_count_sensors_carry_the_device_names() -> None:
    manager = _make_manager_harness()
    a, b = _idle(40), _idle(50)
    a.name, b.name = "L1", "L2"
    sensor = _Sink()
    manager._set_devices(sensor, [b, a])
    assert sensor.value == 2
    assert sensor._attr_extra_state_attributes == {"devices": ["L1", "L2"]}


def test_every_device_gets_its_activity_and_role() -> None:
    manager = _make_manager_harness()
    a, b, c = _idle(40), _idle(50), _idle(80)
    for d, name in ((a, "L1"), (b, "L2"), (c, "L3")):
        d.name, d.activity, d.role = name, _Sink(), _Sink()
    manager.devices = [a, b, c]
    manager.charge, manager.discharge, manager.idle = [], [c], [a, b]
    manager.roles.charger, manager.roles.discharger = b.deviceId, c.deviceId
    manager.roleCharge, manager.roleDischarge = _Sink(), _Sink()
    manager._show_roles()
    assert (a.activity.value, a.role.value) == ("standby", "none")
    assert (b.activity.value, b.role.value) == ("standby", "charge")
    assert (c.activity.value, c.role.value) == ("discharging", "discharge")
