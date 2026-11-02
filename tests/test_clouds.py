"""Changing clouds and fast following: cloud detection, export margin (C), follow-up, reserve (E), fast_track."""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.zendure_ha.const import DeviceState, ManagerMode
from custom_components.zendure_ha.dispatch import VOLATILE_EDGES, Volatility
from tests.test_manager_dispatch import _FakeDevice, _make_manager_harness

T0 = datetime(2026, 6, 1, 12, 0, 0)


def _edges(vol: Volatility, start: float, count: int, sun: int = 1500, base: int = 0) -> float:
    """Feed ``count`` edges of ``sun`` W one minute apart (sun away, sun back, ...); returns the next time.

    The net demand is ``base`` without sun and ``base - sun`` with sun (negative = surplus).
    """
    t = start
    level = base - sun
    for _ in range(count):
        other = base if level != base else base - sun
        for k in range(10):
            vol.add(t + k, level if k < 5 else other)
        level = other
        t += 60
    return t


def test_volatility_needs_several_edges() -> None:
    vol = Volatility()
    t = _edges(vol, 0.0, VOLATILE_EDGES - 1)
    assert not vol.active
    t = _edges(vol, t, 1)
    assert vol.active


def test_an_appliance_on_and_off_is_not_cloud_mode() -> None:
    vol = Volatility()
    _edges(vol, 0.0, 4)  # a kettle and a coffee machine, both on and off
    assert not vol.active


def test_cloud_mode_ends_after_quiet_minutes() -> None:
    vol = Volatility()
    t = _edges(vol, 0.0, VOLATILE_EDGES)
    assert vol.active
    for k in range(0, 700, 5):
        vol.add(t + k, -1500)
    assert not vol.active


def _manager(levels: list[int]):  # noqa: ANN202
    m = _make_manager_harness()
    m.operation = ManagerMode.MATCHING
    m.devices = []
    devices = []
    for level in levels:
        d = _FakeDevice(pwr_max=-2400, electric_level=level)
        d.charge_start, d.charge_optimal = -240, -600
        d.charge_limit, d.discharge_limit, d.discharge_optimal = -2400, 2400, 600
        devices.append(d)
    m.devices = devices
    return m, devices


def _make_cloudy(m) -> None:  # noqa: ANN001
    _edges(m._volatility(), T0.timestamp() - 60 * VOLATILE_EDGES - 30, VOLATILE_EDGES)


def test_export_margin_only_in_cloud_mode_and_never_flips_direction() -> None:
    m, _ = _manager([40, 50])
    assert m._cloud_adjust(-1000, T0) == -1000
    _make_cloudy(m)
    assert m._cloud_adjust(-1000, T0) == -900  # charge 100 W less: a little export
    assert m._cloud_adjust(500, T0) == 500  # not while discharging: that would export battery energy
    assert m._cloud_adjust(-60, T0) == 0  # never turns charging into discharging


def test_followup_only_while_ramping() -> None:
    """Option A (a cycle every second at a cloud edge) was dropped: more starts, not less import."""
    m, devices = _manager([40, 50])
    for d in devices:
        d.slew = type("S", (), {"pending": False})()
    assert m.followup_needed(1400) is False
    devices[0].slew.pending = True
    assert m.followup_needed(0) is True


async def test_cloud_mode_keeps_the_discharge_device_out_of_charging() -> None:
    m, devices = _manager([40, 50, 80])
    _make_cloudy(m)
    m.roles.discharger = devices[2].deviceId
    m.charge = list(devices)
    m.charge_limit, m.charge_optimal = -7200, -1800
    m.charge_weight = sum(d.pwr_max * (100 - d.electricLevel.asInt) for d in devices)
    await m.power_charge(-4000, T0, hysteresis=False)
    assert devices[2].charge_calls[0] == 0  # the reserve is stopped (ramps down), it stays ready to discharge
    assert all(c != 0 for c in devices[0].charge_calls + devices[1].charge_calls)
    assert devices[2] in m.idle


async def test_without_clouds_all_devices_may_charge() -> None:
    m, devices = _manager([40, 50, 80])
    m.roles.discharger = devices[2].deviceId
    m.charge = list(devices)
    m.charge_limit, m.charge_optimal = -7200, -1800
    m.charge_weight = sum(d.pwr_max * (100 - d.electricLevel.asInt) for d in devices)
    await m.power_charge(-6000, T0, hysteresis=False)
    assert all(d.charge_calls[-1] != 0 for d in devices)


async def test_cloud_reserve_is_not_kept_with_one_device() -> None:
    m, devices = _manager([40])
    _make_cloudy(m)
    m.roles.discharger = devices[0].deviceId
    m.charge = list(devices)
    m.charge_limit, m.charge_optimal = -2400, -600
    m.charge_weight = devices[0].pwr_max * 60
    await m.power_charge(-1000, T0, hysteresis=False)
    assert devices[0].charge_calls[-1] != 0
    assert DeviceState  # keep import used
    assert timedelta


def _charging_manager(levels: list[int]):  # noqa: ANN202
    m, devices = _manager(levels)
    m.charge = list(devices)
    m.charge_limit = -2400 * len(devices)
    m.charge_optimal = -600 * len(devices)
    m.charge_weight = sum(d.pwr_max * (100 - d.electricLevel.asInt) for d in devices)
    return m, devices


async def test_big_surplus_uses_the_cloud_reserve_too() -> None:
    """Cloud mode, but 6000 W surplus: more than the other two can take, so the third charges as well."""
    m, devices = _charging_manager([40, 50, 80])
    _make_cloudy(m)
    m.roles.discharger = devices[2].deviceId
    await m.power_charge(-6000, T0, hysteresis=False)
    assert m.cloud_reserve == devices[2].deviceId  # a short overshoot does not release it yet
    m.measured_setpoint = -6000
    await m.power_charge(-6000, T0 + timedelta(seconds=11), hysteresis=False)
    assert m.cloud_reserve is None
    assert all(d.charge_calls[-1] != 0 for d in devices)


async def test_cloud_reserve_hysteresis() -> None:
    m, devices = _charging_manager([40, 50, 80])
    _make_cloudy(m)
    m.roles.discharger = devices[2].deviceId
    for surplus, seconds, expected in (
        (-4700, 0, devices[2].deviceId),  # above 95 % of 4800 W, but only just now
        (-4700, 11, None),  # held for 10 s: the reserve charges too
        (-4300, 20, None),  # between 85 % and 95 %: stays charging (no flapping)
        (-3900, 30, None),  # below 85 %, but only just now
        (-3900, 61, devices[2].deviceId),  # held for 30 s: kept out again
    ):
        m.measured_setpoint = surplus
        await m._keep_cloud_reserve(surplus, T0 + timedelta(seconds=seconds))
        assert m.cloud_reserve == expected, (surplus, seconds)


def test_cloud_mode_uses_a_lower_fill_level() -> None:
    m, _ = _manager([40, 50])
    assert m.effective_fill == 85
    _make_cloudy(m)
    assert m.effective_fill == 70


def _running(m, devices, direction: int, power: int) -> None:  # noqa: ANN001
    from custom_components.zendure_ha.slew import SlewLimiter

    for d in devices:
        d.slew = SlewLimiter(lambda: 0.0)
        d.slew.current = power * direction
        d.homeOutput = type("V", (), {"asInt": max(0, power * direction)})()
        d.homeInput = type("V", (), {"asInt": max(0, -power * direction)})()
        d.command_time, d.mqtt_last = 0.0, 1.0  # the last report is newer than the last command
    if direction > 0:
        m.discharge = list(devices)
    else:
        m.charge = list(devices)
    m.zero_fast = T0


async def test_fast_track_follows_p1_with_running_dischargers() -> None:
    m, devices = _manager([60, 70])
    _running(m, devices, 1, 500)
    await m.fast_track(400, T0)  # 400 W import: 200 W more each
    assert [d.discharge_calls[-1] for d in devices] == [700, 700]


async def test_fast_track_never_starts_stops_or_reverses() -> None:
    m, devices = _manager([60, 70])
    _running(m, devices, 1, 300)
    await m.fast_track(-2000, T0)  # big export: reduce, but not below 50 W and never charge
    assert all(d.discharge_calls[-1] == 50 for d in devices)
    assert all(not d.charge_calls for d in devices)
    m2, idle = _manager([60])
    m2.idle = list(idle)
    m2.zero_fast = T0
    await m2.fast_track(1500, T0)  # nothing runs: starting is for the control cycle
    assert not idle[0].discharge_calls and not idle[0].charge_calls


async def test_fast_track_counts_power_that_is_not_measured_yet() -> None:
    m, devices = _manager([60])
    _running(m, devices, 1, 500)
    devices[0].slew.current = 900  # 900 W commanded, the device still shows 500 W
    await m.fast_track(400, T0)  # the 400 W import is already on its way: no extra command
    assert not devices[0].discharge_calls


async def test_fast_track_waits_while_a_cycle_runs_or_during_a_hand_over() -> None:
    from datetime import datetime as dt

    m, devices = _manager([60, 70])
    _running(m, devices, 1, 500)
    m.zero_fast = dt.max  # a control cycle is running
    await m.fast_track(400, T0)
    assert not any(d.discharge_calls for d in devices)
    m.zero_fast = T0
    m.charge = [devices[1]]  # charging and discharging at the same time
    await m.fast_track(400, T0)
    assert not any(d.discharge_calls for d in devices)


async def test_fast_track_with_chargers() -> None:
    m, devices = _manager([30, 40])
    _running(m, devices, -1, 1000)
    await m.fast_track(-600, T0)  # 600 W export: charge 300 W more each
    assert [d.charge_calls[-1] for d in devices] == [-1300, -1300]
    for d in devices:
        d.slew.current = -1300
        d.homeInput.asInt = 1300
        d.mqtt_last = 2.0
    await m.fast_track(800, T0 + timedelta(milliseconds=500))  # within 1 s: devices are spared
    assert [d.charge_calls[-1] for d in devices] == [-1300, -1300]
    await m.fast_track(800, T0 + timedelta(seconds=3))  # import: charge less, but stay charging
    assert all(-1300 < d.charge_calls[-1] < 0 for d in devices)


async def test_fast_track_waits_for_a_report_after_the_last_command() -> None:
    m, devices = _manager([60])
    _running(m, devices, 1, 500)
    devices[0].command_time, devices[0].mqtt_last = 5.0, 4.0  # commanded after the last report
    await m.fast_track(400, T0)
    assert not devices[0].discharge_calls




def test_no_cloud_mode_without_sun() -> None:
    """In the evening appliances switching on and off are no clouds."""
    vol = Volatility()
    _edges(vol, 0.0, VOLATILE_EDGES + 2, sun=-1500, base=300)  # demand 300 <-> 1800 W, never a surplus
    assert vol.peak_surplus == 0
    assert not vol.active


def test_clouds_with_little_sun_are_detected() -> None:
    """With 750 W surplus a cloud is a change of about 750 W: below the old fixed 800 W, above half the surplus."""
    vol = Volatility()
    _edges(vol, 0.0, VOLATILE_EDGES, sun=750, base=0)
    assert vol.threshold == 375
    assert vol.active


def test_threshold_follows_the_sun() -> None:
    vol = Volatility()
    assert vol.threshold == 300  # no sun yet: the lowest threshold, but no cloud mode without surplus
    vol.add(0.0, -4000)
    assert vol.threshold == 800  # much sun: the edges must be large (appliances are smaller)
    vol.add(1000.0, -400)  # 15 minutes later the peak is forgotten
    assert vol.peak_surplus == 400
    assert vol.threshold == 300


def test_cloud_mode_starts_a_device_only_for_a_real_shortage() -> None:
    from custom_components.zendure_ha.const import SmartMode

    m, _ = _manager([40, 50])
    assert m.start_threshold == SmartMode.POWER_START
    _make_cloudy(m)
    assert m.start_threshold == SmartMode.CLOUD_POWER_START


async def test_cloud_mode_charges_again_sooner() -> None:
    from custom_components.zendure_ha.const import SmartMode

    m, devices = _manager([40, 50])
    m.charge = list(devices)
    m.charge_limit, m.charge_optimal = -4800, -1200
    m.charge_weight = sum(d.pwr_max * (100 - d.electricLevel.asInt) for d in devices)
    m.charge_last = T0 - timedelta(seconds=60)  # charged a minute ago
    m.charge_time = datetime.max
    await m.power_charge(-1000, T0)
    assert m.charge_time == T0 + timedelta(seconds=SmartMode.CHARGE_DELAY)
    _make_cloudy(m)
    m.charge_time = datetime.max
    m.charge_last = T0 - timedelta(seconds=60)
    await m.power_charge(-1000, T0)
    assert m.charge_time == T0 + timedelta(seconds=SmartMode.CLOUD_CHARGE_DELAY)
