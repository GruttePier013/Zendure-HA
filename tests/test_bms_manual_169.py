"""1.6.9: BMS cap of a device, a calm manual mode, the stale watchdog at the limit, the direction of a P1 jump."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.zendure_ha import events
from custom_components.zendure_ha.const import CONF_POWER_TOLERANCE, CONTROL_DEFAULTS, DeviceState, ManagerMode, SmartMode
from custom_components.zendure_ha.manager import ZendureManager, _has_room
from tests.test_record_v2 import _device, _feed, _manager as _watch_manager


@pytest.fixture(autouse=True)
def _clean_events() -> None:
    events.reset()


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clocked(zensdk_device, monkeypatch):  # noqa: ANN001, ANN201
    clock = _Clock()
    monkeypatch.setattr("custom_components.zendure_ha.device.monotonic", clock)
    zensdk_device.slew._clock = clock
    zensdk_device.slew.min_run = 0
    zensdk_device.electricLevel = SimpleNamespace(asInt=88, asNumber=88)
    zensdk_device.setLimits(-2400, 2200)
    zensdk_device.state = DeviceState.INACTIVE
    return zensdk_device, clock


def _watch(device: Any, clock: _Clock, measured: int, seconds: float, step: float = 2.0) -> None:
    end = clock.t + seconds
    while clock.t < end:
        clock.t += step
        device._watch_bms(measured)


# ----------------------------------------------------------------------------------------------
# BMS cap
# ----------------------------------------------------------------------------------------------
async def test_a_charge_that_tapers_gets_a_cap_and_the_command_follows_it(clocked) -> None:  # noqa: ANN001
    device, clock = clocked
    await device.power_charge(-2200)
    _watch(device, clock, -1700, 20)  # 500 W short, but not yet BMS_AFTER s
    assert device.bms_cap == 0
    _watch(device, clock, -1700, 14)
    assert (device.bms_cap, device.bms_dir) == (1700, -1)
    assert events.counters["bms_limit"] == 1 and "direction=charge cap=1700 command=-2200 soc=88" in events.last["detail"]
    assert device.capped_limit(False) == -1800 and device.capped_limit(True) == 2200  # only the charge side

    await device.power_charge(-2200)
    assert device.slew.current == -1800  # at most the cap + the probe
    sent = device.command_total
    device._watch_bms(-1650)  # the taper goes on a little: within the resend band
    await device.power_charge(-2200)
    assert device.command_total == sent  # no new command for 50 W

    device._watch_bms(-1200)  # much less now
    assert device.bms_cap == 1200
    await device.power_charge(-2200)
    assert device.slew.current == -1300 and device.command_total == sent + 1


async def test_the_cap_ends_when_the_bms_gives_more_or_the_device_stops(clocked) -> None:  # noqa: ANN001
    device, clock = clocked
    await device.power_discharge(2000)
    _watch(device, clock, 900, 40)
    assert device.bms_limited(True) and device.bms_cap == 900
    device._watch_bms(980)  # a little more, but far below the command of 2000 W: the cap follows the BMS
    assert device.bms_cap == 980
    await device.power_discharge(2000)
    assert device.slew.current == 1080  # cap + probe
    _watch(device, clock, 1070, 8)  # it gives the probe, but not yet BMS_RELEASE s
    assert device.bms_cap == 980
    _watch(device, clock, 1070, 4)  # the BMS allows more again
    assert device.bms_cap == 0
    assert events.counters["bms_limit_end"] == 1 and "reason=released" in events.last["detail"]
    await device.power_discharge(2000)
    assert device.slew.current == 2000  # the full command again

    _watch(device, clock, 900, 40)
    assert device.bms_cap == 900
    await device.power_discharge(0)
    device._watch_bms(0)
    assert device.bms_cap == 0 and "reason=stopped" in events.last["detail"]


async def test_a_ramp_or_a_small_command_is_no_bms_limit(clocked) -> None:  # noqa: ANN001
    device, clock = clocked
    await device.power_discharge(2000)
    _watch(device, clock, 600, 12)  # the inverter ramps up (9-12 s from 0 W)
    _watch(device, clock, 1990, 30)
    assert device.bms_cap == 0
    await device.power_discharge(250)  # below BMS_MIN_W: not checked
    _watch(device, clock, 50, 60)
    assert device.bms_cap == 0
    assert events.counters["bms_limit"] == 0


async def test_the_fuse_group_plans_with_the_cap(clocked) -> None:  # noqa: ANN001
    device, clock = clocked
    from custom_components.zendure_ha.fusegroup import FuseGroup

    fg = FuseGroup("L1", 2400, -2400, [device])
    await device.power_charge(-2200)
    _watch(device, clock, -700, 40)
    fg.initPower = True
    assert fg.charge_limit(device) == -800
    fg.initPower = True
    assert fg.discharge_limit(device) == 2200


def test_the_optimal_power_is_capped_too() -> None:
    capped = SimpleNamespace(charge_optimal=-600, discharge_optimal=550, bms_limited=lambda discharge: not discharge, capped_limit=lambda _d: -800)
    assert ZendureManager._optimal(capped, False) == -200
    assert ZendureManager._optimal(capped, True) == 550
    assert ZendureManager._optimal(SimpleNamespace(charge_optimal=-600), False) == -600  # a device without the cap logic


# ----------------------------------------------------------------------------------------------
# stale control watchdog
# ----------------------------------------------------------------------------------------------
async def test_watchdog_quiet_when_all_devices_are_at_their_limit(hass, monkeypatch) -> None:  # noqa: ANN001
    """16 November 17:39: three inverters at 2400 / 2200 / 2200 W, 892 W import: nothing left to control."""
    devices = [_device("L1", current=2400), _device("L2", current=2200), _device("L3", current=2200)]
    devices[0].discharge_limit = 2400
    m = _watch_manager(hass, devices)
    m.charge_time = datetime.max
    assert await _feed(m, 892, 15, monkeypatch) == []
    assert m.stale_total == 0 and events.counters["stale_control"] == 0


async def test_watchdog_still_warns_with_room_left(hass, monkeypatch) -> None:  # noqa: ANN001
    devices = [_device("L1", current=2400), _device("L2", current=1500)]
    devices[0].discharge_limit = 2400
    m = _watch_manager(hass, devices)
    m.charge_time = datetime.max
    assert await _feed(m, 892, 15, monkeypatch) == ["cycle"]
    assert m.stale_total == 1


def test_room_uses_the_bms_cap() -> None:
    slew = SimpleNamespace(current=-1180)
    d = SimpleNamespace(slew=slew, capped_limit=lambda discharge: 2200 if discharge else -1200)
    assert _has_room(d, True) is True  # it can stop charging and discharge
    assert _has_room(d, False) is False  # it charges at its cap: no more
    assert _has_room(SimpleNamespace(slew=slew), False) is True  # unknown limit


# ----------------------------------------------------------------------------------------------
# manual mode
# ----------------------------------------------------------------------------------------------
def _manual(last: int = -6600, steady: bool = True) -> ZendureManager:
    m = object.__new__(ZendureManager)
    m.operation = ManagerMode.MANUAL
    m._manual_last, m._manual_steady = last, steady
    return m


def _dev(current: int) -> Any:
    return SimpleNamespace(slew=SimpleNamespace(current=current))


def test_manual_keeps_the_shares_within_five_percent() -> None:
    m = _manual()  # band 330 W at 6600 W
    assert m._manual_power(_dev(-2200), -2240) == -2200
    assert m._manual_power(_dev(-2200), -1880) == -2200
    assert m._manual_power(_dev(-2200), -1850) == -1850  # 350 W: shared out again
    assert m._manual_power(_dev(-2200), 0) == 0  # a stop always goes through
    assert m._manual_power(_dev(0), -2200) == -2200  # a start too
    assert _manual(last=600)._manual_power(_dev(300), 340) == 300  # at least MANUAL_BAND_MIN (50 W)
    assert _manual(last=600)._manual_power(_dev(300), 360) == 360


def test_manual_shares_again_after_a_change_or_outside_manual() -> None:
    assert _manual(steady=False)._manual_power(_dev(-2200), -2240) == -2240
    m = _manual()
    m.operation = ManagerMode.MATCHING
    assert m._manual_power(_dev(-2200), -2240) == -2240


# ----------------------------------------------------------------------------------------------
# P1 jump direction and the defaults
# ----------------------------------------------------------------------------------------------
def test_a_jump_tells_its_direction(monkeypatch) -> None:  # noqa: ANN001
    from custom_components.zendure_ha.dispatch import P1Tracker

    monkeypatch.setattr(SmartMode, "JUMP_W", 400)
    t0 = datetime(2026, 11, 15, 13, 20, 22)
    m = object.__new__(ZendureManager)
    m.devices, m.p1_target, m.operation, m.jump_ref, m.last_cycle = [], 0, ManagerMode.MATCHING, 46, t0
    m.zero_fast, m.p1tracker = t0 + timedelta(seconds=2.2), P1Tracker()
    assert m.jump_check(-1389, t0 + timedelta(seconds=1.5)) is True
    assert "direction=export" in events.last["detail"]
    m.jump_ref, m.last_cycle = 0, t0 + timedelta(seconds=2)
    assert m.jump_check(1500, t0 + timedelta(seconds=3.5)) is True
    assert "direction=import" in events.last["detail"]


def test_power_tolerance_default_is_15_w() -> None:
    assert CONTROL_DEFAULTS[CONF_POWER_TOLERANCE] == 15


def test_a_shared_fuse_group_plans_with_the_cap() -> None:
    from custom_components.zendure_ha.fusegroup import FuseGroup

    def dev(limit: int) -> Any:
        return SimpleNamespace(
            homeInput=SimpleNamespace(asInt=1000), homeOutput=SimpleNamespace(asInt=0), electricLevel=SimpleNamespace(asInt=50),
            charge_start=-240, discharge_start=220, pwr_max=0, capped_limit=lambda discharge, limit=limit: 2200 if discharge else limit,
        )  # fmt: skip

    capped, free = dev(-800), dev(-2400)
    fg = FuseGroup("shared", 3600, -3600, [capped, free])
    assert fg.charge_limit(capped) == -800
    assert free.pwr_max == -2400  # the other device keeps its own limit


async def test_manual_charge_at_a_fixed_power_does_not_move_the_shares_every_cycle() -> None:
    """15 November 10:00-11:25: 6600 W manual charge, the SoC of the three devices rose unevenly."""
    from tests.test_manager_dispatch import _FakeDevice, _make_manager_harness

    m = _make_manager_harness()
    m.operation = ManagerMode.MANUAL
    devices = [_FakeDevice(pwr_max=-2400, electric_level=soc) for soc in (40, 41, 39)]
    for d in devices:
        d.slew = SimpleNamespace(current=-2200)
    sent = {d.deviceId: [] for d in devices}
    t = datetime(2026, 11, 15, 10, 10)
    for cycle in range(40):
        for k, d in enumerate(devices):
            d.electricLevel.asInt = min(99, 40 + cycle // (2 + k))  # unevenly rising SoC
        m.charge = list(devices)
        m.charge_limit = -7200
        m.charge_weight = sum(d.pwr_max * (100 - d.electricLevel.asInt) for d in devices)
        m._manual_steady, m._manual_last = cycle > 0, -6600
        await m._charge_devices(-6600, t + timedelta(seconds=15 * cycle), False)
        for d in devices:
            power = d.charge_calls[-1]
            if power != d.slew.current:
                sent[d.deviceId].append(power)
            d.slew.current = power
    assert all(len(v) <= 1 for v in sent.values()), sent  # without the band: a new share almost every cycle
