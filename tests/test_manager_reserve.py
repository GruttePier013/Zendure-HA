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
    for _ in range(7):
        manager._check_crossflow()
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


async def test_only_the_ready_device_gets_the_fast_start() -> None:
    manager = _make_manager_harness()
    ready, second = _idle(80), _idle(60)
    manager.idle = [second, ready]
    manager.roles.discharger = ready.deviceId
    await manager.power_discharge(3500)
    assert ready.ready_calls == [True]
    assert second.ready_calls == [False]


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
