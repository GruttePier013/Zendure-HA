"""Tests for the SlewLimiter (direction change protection; no ramp limit since 1.6.0)."""

from __future__ import annotations

from custom_components.zendure_ha.slew import SlewLimiter


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _limiter(clock: _Clock, flip: float = 5, min_run: float = 0) -> SlewLimiter:
    lim = SlewLimiter(clock)
    lim.flip_delay = flip
    lim.min_run = min_run
    return lim


def test_no_rate_limit_any_more() -> None:
    """Since 1.6.0 the inverter ramps by itself: the target is commanded at once, in both directions."""
    clock = _Clock()
    lim = _limiter(clock)
    assert lim.limit(2400) == 2400  # never commanded: no ramp
    assert lim.pending is False
    clock.now += 0.1
    assert lim.limit(300) == 300
    clock.now += 0.1
    assert lim.limit(2200) == 2200


def test_start_from_standby_goes_straight_to_the_target() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    lim.limit(0)
    clock.now += 3600
    assert lim.limit(1100) == 1100


def test_direction_change_goes_through_zero_and_waits() -> None:
    """Charging 2400 W -> manual 2000 W discharge: 0 W first, hold at 0 W, then the target."""
    clock = _Clock()
    lim = _limiter(clock)
    lim.current, lim._updated, lim._last_dir = -2400, clock.now, -1  # noqa: SLF001
    seen = []
    for _ in range(20):
        clock.now += 1
        seen.append(lim.limit(2000))
    assert seen[0] == 0  # down to 0 W first, never straight to +
    assert lim.pending is False
    first_positive = next(i for i, v in enumerate(seen) if v > 0)
    assert all(v == 0 for v in seen[:first_positive])
    assert first_positive >= 5  # released only flip_delay (5 s) after reaching 0 W
    assert seen[first_positive] == 2000  # then at once


def test_held_back_command_is_pending() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    lim.current, lim._updated, lim._last_dir = -800, clock.now, -1  # noqa: SLF001
    clock.now += 1
    assert lim.limit(2000) == 0
    assert lim.pending is True  # the manager runs the next cycle after RAMP_INTERVAL


def test_same_direction_restart_is_not_delayed() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    lim.limit(-500)
    clock.now += 1
    lim.limit(0)
    clock.now += 1
    assert lim.limit(-500) == -500


def test_keep_alive_values_survive() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    assert lim.limit(10) == 10
    clock.now += 2
    assert lim.limit(-10) == -10 or lim.limit(-10) == 0  # direction rule may hold the sign change, never exceeds 10 W
    assert abs(lim.current) <= 10


def test_sync_uses_measured_power_after_restart() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    lim.sync(-2400)
    clock.now += 1
    assert lim.limit(800) == 0  # it was charging: the direction change pause applies


def test_min_run_keeps_a_fresh_device_running_at_low_power() -> None:
    clock = _Clock()
    lim = _limiter(clock, min_run=10)
    assert lim.limit(-300) == -300
    clock.now += 1
    # demand disappears after one second: hold -50 W instead of switching the relay off
    assert lim.limit(0) == -50
    clock.now += 5
    assert lim.limit(0) == -50
    clock.now += 6  # more than 10 s since the start
    assert lim.limit(0) == 0


def test_min_run_is_skipped_when_switching_off_on_purpose() -> None:
    clock = _Clock()
    lim = _limiter(clock, min_run=10)
    lim.limit(-300)
    clock.now += 1
    assert lim.limit(0, hold=False) == 0


def test_min_run_ignores_keep_alive_values() -> None:
    clock = _Clock()
    lim = _limiter(clock, min_run=10)
    assert lim.limit(10) == 10
    clock.now += 1
    assert lim.limit(0) == 0


def test_follow_a_full_battery_that_stopped_charging() -> None:
    """The device stopped charging because it is full: after the short minimum the other direction is free."""
    clock = _Clock()
    lim = _limiter(clock)
    lim.current, lim._updated, lim._last_dir = -1200, clock.now, -1  # noqa: SLF001
    lim.follow(0)
    assert lim.current == 0 and not lim.pending
    clock.now += 60
    assert lim.limit(2000) == 2000


def test_follow_keeps_the_pause_right_after_stopping() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    lim.current, lim._updated, lim._last_dir = -1200, clock.now, -1  # noqa: SLF001
    lim.follow(0)
    clock.now += 1
    assert lim.limit(2000) == 0  # 1 s after it stopped charging: below the 2 s minimum


def _stopped(clock: _Clock) -> SlewLimiter:
    """A limiter that was charging 1200 W and has just been commanded to 0 W."""
    lim = _limiter(clock, flip=6)
    lim.current, lim._updated, lim._last_dir = -1200, clock.now, -1  # noqa: SLF001
    clock.now += 1
    assert lim.limit(0) == 0
    return lim


def test_confirmed_zero_releases_the_direction_change_after_two_seconds() -> None:
    clock = _Clock()
    lim = _stopped(clock)
    clock.now += 1.5
    lim.measure(0)  # fresh measurement (1.5 s after the stop): 0 W
    assert lim.limit(800) == 0  # still below the 2 s minimum
    clock.now += 0.5
    assert lim.limit(800) > 0  # 2 s and confirmed: released


def test_measurement_right_after_the_stop_does_not_count() -> None:
    clock = _Clock()
    lim = _stopped(clock)
    clock.now += 0.5
    lim.measure(0)  # may still be the report from before the stop
    clock.now += 2
    assert lim.limit(800) == 0  # not confirmed, flip_delay (6 s) not over


def test_without_measurements_the_fixed_pause_applies() -> None:
    clock = _Clock()
    lim = _stopped(clock)
    clock.now += 5.9
    assert lim.limit(800) == 0
    clock.now += 0.2
    assert lim.limit(800) > 0


def test_device_that_still_reports_power_is_waited_for() -> None:
    clock = _Clock()
    lim = _stopped(clock)
    clock.now += 2
    lim.measure(-700)  # it is still charging: did not follow the stop yet
    clock.now += 5
    assert lim.limit(800) == 0  # 7 s, but no 0 W seen: keep waiting
    lim.measure(0)
    clock.now += 1
    assert lim.limit(800) > 0  # confirmed now
