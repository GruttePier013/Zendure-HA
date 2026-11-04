"""1.6.0: a big P1 jump starts a control cycle at once, also inside the fast window (A),
and optionally a big jump is shared by two inverters (B, JUMP_SPLIT_W)."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.zendure_ha import events
from custom_components.zendure_ha.const import DeviceState, ManagerMode, SmartMode
from custom_components.zendure_ha.dispatch import P1Tracker
from custom_components.zendure_ha.manager import ZendureManager
from tests.test_manager_dispatch import _FakeDevice, _make_manager_harness

T0 = datetime(2026, 11, 3, 18, 0, 0)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch) -> None:  # noqa: ANN001
    events.reset()
    monkeypatch.setattr(SmartMode, "JUMP_W", 400)
    monkeypatch.setattr(SmartMode, "JUMP_SPLIT_W", 0)


def _manager(ref: int = 0, last: datetime | None = T0, operation: ManagerMode = ManagerMode.MATCHING) -> ZendureManager:
    m = object.__new__(ZendureManager)
    m.devices = []
    m.p1_target = 0
    m.operation = operation
    m.jump_ref = ref
    m.last_cycle = last
    m.zero_fast = T0 + timedelta(seconds=2.2)  # inside the fast window after the last cycle
    m.p1tracker = P1Tracker()
    return m


def test_kettle_inside_the_fast_window_starts_a_cycle() -> None:
    m = _manager(ref=20)
    assert m.jump_check(1500, T0 + timedelta(seconds=1.2)) is True
    assert list(m.p1tracker.history) == [1500]  # the new reference of the fast change detection
    assert m.jump_total == 1 and events.counters["p1_jump"] == 1


def test_a_building_load_is_seen_early_and_again_when_it_keeps_rising() -> None:
    m = _manager(ref=10)
    assert m.jump_check(300, T0 + timedelta(seconds=1.2)) is False  # 300 W more: not yet
    assert m.jump_check(800, T0 + timedelta(seconds=2.2)) is True  # 800 W: at once, not waiting for 2000 W
    m.jump_ref, m.last_cycle = 800, T0 + timedelta(seconds=2.2)  # the cycle corrects 800 W
    assert m.jump_check(2000, T0 + timedelta(seconds=3.2)) is True  # still rising: one more cycle


def test_the_response_of_the_batteries_is_never_a_jump() -> None:
    m = _manager(ref=2000)  # the last cycle corrected a kettle
    for k, p1 in enumerate((2000, 1800, 1300, 700, 150, -250)):
        assert m.jump_check(p1, T0 + timedelta(seconds=1.1 + k)) is False


def test_kettle_off_while_discharging_is_a_jump_too() -> None:
    m = _manager(ref=40)
    assert m.jump_check(-1900, T0 + timedelta(seconds=1.5)) is True  # stored energy would go to the grid


def test_not_twice_within_a_second_and_not_while_a_cycle_runs() -> None:
    m = _manager(ref=0)
    assert m.jump_check(1500, T0 + timedelta(seconds=0.5)) is False  # less than JUMP_GAP after the last cycle
    m.zero_fast = datetime.max  # a cycle runs
    assert m.jump_check(1500, T0 + timedelta(seconds=1.5)) is False
    assert m.jump_pending is True  # the next cycle starts right after it


def test_only_in_the_smart_modes_and_not_when_switched_off(monkeypatch) -> None:  # noqa: ANN001
    assert _manager(operation=ManagerMode.MANUAL).jump_check(2000, T0 + timedelta(seconds=2)) is False
    monkeypatch.setattr(SmartMode, "JUMP_W", 0)
    assert _manager().jump_check(2000, T0 + timedelta(seconds=2)) is False


def test_the_target_band_is_the_reference() -> None:
    m = _manager(ref=0)
    m.p1_target = -100
    m.devices = [SimpleNamespace(slew=SimpleNamespace(current=-500))]  # charging: the band applies
    assert m.jump_check(-480, T0 + timedelta(seconds=2)) is False  # 480 W export is 380 W outside the band
    assert m.jump_check(-560, T0 + timedelta(seconds=2)) is True


async def test_handle_p1_runs_the_cycle_inside_the_fast_window(monkeypatch) -> None:  # noqa: ANN001
    m = _manager(ref=0)
    m.zero_fast = datetime.now() + timedelta(seconds=2)  # inside the window: the tracker only collects
    m.zero_next = datetime.now() + timedelta(seconds=4)
    m.last_cycle = datetime.now() - timedelta(seconds=1.5)
    m.p1_stale, m._net_history, m.p1record = False, None, None
    m.p1_values = 0
    m.simulation = False
    ran: list[tuple[int, bool]] = []

    async def run(p1: int, fast: bool, _time: datetime) -> None:
        ran.append((p1, fast))

    async def nothing(*_args: Any) -> None:
        return None

    monkeypatch.setattr(m, "_run_power_update", run)
    monkeypatch.setattr(m, "fast_track", nothing)
    monkeypatch.setattr(m, "_watch_stale", nothing)
    await m._handle_p1(100)
    assert ran == []  # a small change: only the fast adjustment
    await m._handle_p1(1800)
    assert ran == [(1800, True)]


# ---------------------------------------------------------------------------------------------
# B: a big jump shared by two inverters
# ---------------------------------------------------------------------------------------------
def _idle(level: int) -> _FakeDevice:
    d = _FakeDevice(pwr_max=2400, electric_level=level)
    d.discharge_limit, d.discharge_optimal = 2400, 600
    d.charge_limit, d.charge_optimal = -2400, -600
    d.state = DeviceState.INACTIVE
    return d


async def test_a_big_jump_from_standby_starts_two_inverters() -> None:
    m = _make_manager_harness()
    ready, second = _idle(80), _idle(60)
    m.idle = [second, ready]
    m.roles.discharger = ready.deviceId
    m.jump_split = 2000
    await m.power_discharge(2000)
    assert ready.discharge_calls == [1000] and second.discharge_calls == [1000]
    assert m.jump_split == 0  # once


async def test_without_a_jump_one_inverter_takes_it() -> None:
    m = _make_manager_harness()
    ready, second = _idle(80), _idle(60)
    m.idle = [second, ready]
    m.roles.discharger = ready.deviceId
    await m.power_discharge(2000)
    assert ready.discharge_calls == [2000] and second.discharge_calls == []


async def test_a_big_jump_next_to_a_running_inverter_starts_a_second_one() -> None:
    m = _make_manager_harness()
    running, ready = _FakeDevice(pwr_max=2400, electric_level=70), _idle(80)
    m.discharge = [running]
    m.discharge_limit, m.discharge_optimal = 2400, 600
    m.discharge_weight = running.pwr_max * running.electricLevel.asInt
    m.idle = [ready]
    m.roles.discharger = ready.deviceId
    m.jump_split = 2000
    await m.power_discharge(2300)  # 300 W base load + a 2000 W kettle
    assert running.discharge_calls[-1] == 1300 and ready.discharge_calls == [1000]


# ---------------------------------------------------------------------------------------------
# 3 November: manual -6000 W -> smart gave 0 W, a little charging, 0 W, charging again
# ---------------------------------------------------------------------------------------------
def _charging_devices():  # noqa: ANN202
    from tests.test_manager_reserve import _charging

    m, devs = _charging([40, 50, 60])
    for d in devs:
        d.homeInput = SimpleNamespace(asInt=2000)
        d.slew = SimpleNamespace(current=-2000)
    m.devices = devs
    m.charge_weight = sum(d.pwr_max * (100 - d.electricLevel.asInt) for d in devs)
    m.charge_last = T0 - timedelta(seconds=100)  # charged in smart mode a little earlier: 60 s delay
    m.p1meterEvent = None
    m.zero_fast = T0
    m.operation = ManagerMode.MANUAL
    return m, devs


async def test_charging_goes_on_after_manual_charging() -> None:
    m, devs = _charging_devices()
    await m.update_operation(SimpleNamespace(value=ManagerMode.MATCHING.value), None)
    await m.power_charge(-200, datetime.now())  # the first smart cycle: 200 W surplus
    assert sum(d.charge_calls[-1] for d in devs) == -200  # charging goes on, no stop and no delay


async def test_without_the_fix_the_charge_delay_stopped_everything() -> None:
    m, devs = _charging_devices()
    await m.power_charge(-200, T0)  # charge delay still "never started"
    assert [d.charge_calls[-1] for d in devs] == [0, 0, 0]


async def test_after_manual_discharging_the_charge_delay_still_applies() -> None:
    m, devs = _charging_devices()
    for d in devs:
        d.homeInput = SimpleNamespace(asInt=0)
        d.slew = SimpleNamespace(current=800)
    await m.update_operation(SimpleNamespace(value=ManagerMode.MATCHING.value), None)
    assert m.charge_time == datetime.max  # it did not charge: a new start waits as before


def test_the_jump_detection_is_not_involved() -> None:
    m = _manager(ref=5700)  # the last manual cycle saw 5700 W import (charging 6000 W)
    for k, p1 in enumerate((5700, 3000, 200, -200, -400)):  # the batteries stop: the import goes away
        assert m.jump_check(p1, T0 + timedelta(seconds=2 + k)) is False


async def test_mode_change_runs_the_first_cycle_now_with_a_fresh_p1(monkeypatch) -> None:  # noqa: ANN001
    from time import monotonic

    m = _manager(operation=ManagerMode.MANUAL)
    m.p1meterEvent = object()
    m.devices = [SimpleNamespace(online=True, homeInput=SimpleNamespace(asInt=0), slew=SimpleNamespace(current=0))]
    m.hass = SimpleNamespace(is_running=True)
    m.zero_fast = T0
    m.p1_last, m.p1_time = -350, monotonic() - 0.4
    ran: list[int] = []

    async def run(p1: int, _fast: bool, _time: datetime) -> None:
        ran.append(p1)

    monkeypatch.setattr(m, "_run_power_update", run)
    await m.update_operation(SimpleNamespace(value=ManagerMode.MATCHING.value), None)
    assert ran == [-350]
    m.operation, m.p1_time = ManagerMode.MANUAL, monotonic() - 10  # an old value: wait for the next one
    await m.update_operation(SimpleNamespace(value=ManagerMode.MATCHING.value), None)
    assert ran == [-350] and m.zero_next == datetime.min
