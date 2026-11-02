"""Tests for the pure decision logic (P1 tracking, peak shaving, roles of multiple devices)."""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.zendure_ha.dispatch import P1Tracker, RoleSelector, peak_setpoint
from custom_components.zendure_ha.fusegroup import FuseGroup


def _evaluate(tracker: P1Tracker, p1: int, now: datetime, zero_fast: datetime, zero_next: datetime):  # noqa: ANN202
    return tracker.evaluate(p1, now, zero_fast, zero_next, 3.5, 15)


def test_p1_tracker_replays_a_trace() -> None:
    """A recorded P1 trace: steady noise does not trigger a fast update, a large step does."""
    tracker = P1Tracker()
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    never = datetime.min
    # warm up the history with a steady import (collected while an update is running)
    for _ in range(8):
        tracker.evaluate(100, t0, t0 + timedelta(seconds=30), t0, 3.5, 15)
    # steady import around 100 W with +/- 10 W noise: no fast update, regular update after zero_next
    for i, p1 in enumerate([100, 105, 95, 102, 98, 101, 99, 103]):
        run, fast = _evaluate(tracker, p1, t0 + timedelta(seconds=i), never, t0 + timedelta(seconds=60))
        assert not fast and not run
    # the regular interval has passed
    run, fast = _evaluate(tracker, 100, t0 + timedelta(seconds=61), never, t0 + timedelta(seconds=60))
    assert run and not fast
    # a 1500 W step is fast and runs immediately
    run, fast = _evaluate(tracker, 1600, t0 + timedelta(seconds=62), never, t0 + timedelta(seconds=600))
    assert run and fast


def test_p1_tracker_only_collects_while_an_update_is_running() -> None:
    tracker = P1Tracker()
    now = datetime(2026, 1, 1, 12, 0, 0)
    run, fast = _evaluate(tracker, 5000, now, now + timedelta(seconds=2), now)
    assert (run, fast) == (False, False)
    assert tracker.history[-1] == 5000


def test_peak_setpoint() -> None:
    assert peak_setpoint(800, 0) == 800  # off
    assert peak_setpoint(800, 500) == 300  # only the part above the limit
    assert peak_setpoint(300, 500) == 0  # import below the limit is accepted
    assert peak_setpoint(300, 500, produced=200) == 200  # solar pass-through is always delivered
    assert peak_setpoint(-300, 500) == -300  # charging is untouched


def _soc(**values: int) -> list[tuple[str, int]]:
    return list(values.items())


def test_roles_follow_soc_and_charger_is_another_device() -> None:
    clock = [0.0]
    roles = RoleSelector(margin=10, hold=300, clock=lambda: clock[0])
    roles.update(_soc(a=80, b=40), _soc(a=80, b=40))
    assert roles.discharger == "a" and roles.charger == "b"


def test_roles_do_not_swap_before_the_margin_and_hold_time() -> None:
    clock = [0.0]
    roles = RoleSelector(margin=10, hold=300, clock=lambda: clock[0])
    roles.update(_soc(a=80, b=60), _soc(a=80, b=60))
    clock[0] += 1000
    # b leads by 5 points: not enough
    roles.update(_soc(a=60, b=65), _soc(a=60, b=65))
    assert roles.discharger == "a"
    # b leads by 12 points, but the role was just assigned (hold time)
    roles.update(_soc(a=50, b=62), _soc(a=50, b=62))
    assert roles.discharger == "b"  # the hold time (300 s) had passed after the first 1000 s


def test_roles_hold_time_blocks_a_quick_swap() -> None:
    clock = [0.0]
    roles = RoleSelector(margin=10, hold=300, clock=lambda: clock[0])
    roles.update(_soc(a=50, b=50), _soc(a=50, b=50))
    first = roles.discharger
    other = "b" if first == "a" else "a"
    clock[0] += 10
    roles.update(_soc(**{first: 40, other: 60}), _soc(**{first: 40, other: 60}))
    assert roles.discharger == first  # 20 points difference, but only 10 s since the assignment
    clock[0] += 400
    roles.update(_soc(**{first: 40, other: 60}), _soc(**{first: 40, other: 60}))
    assert roles.discharger == other


def test_roles_with_a_single_device_have_no_charger() -> None:
    """One device online: no role split, the charge role shows '-' instead of the same device."""
    roles = RoleSelector()
    roles.update(_soc(a=50), _soc(a=50))
    assert roles.discharger == "a" and roles.charger is None


def test_roles_skip_devices_that_cannot_take_part() -> None:
    roles = RoleSelector()
    roles.update(_soc(b=40), _soc(a=100, b=40))  # a is full: it cannot charge, empty list for it is not given
    assert roles.discharger == "b"


def test_fusegroup_has_separate_charge_and_discharge_flags() -> None:
    group = FuseGroup("g", 2400, -2400)
    assert group.initCharge and group.initDischarge
    group.initCharge = False
    assert group.initPower  # discharge still needs initialising
    group.initPower = True
    assert group.initCharge and group.initDischarge


def test_discovery_matches_the_serial_in_the_txt_records() -> None:
    from custom_components.zendure_ha.discovery import match_device

    serials = {"HEC4NENCN521191": "L2"}
    name = "Zendure-solarFlow2400ACPlus-A1B2C3D4E5F6._zendure._tcp.local."
    assert match_device(name, serials) is None  # the suffix is not the serial
    assert match_device(name, serials, {b"sn": b"HEC4NENCN521191"}) == "HEC4NENCN521191"
