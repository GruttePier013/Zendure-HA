"""Tests for the SlewLimiter (ramp and direction change handling)."""

from __future__ import annotations

from custom_components.zendure_ha.slew import SlewLimiter


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _limiter(clock: _Clock, up: float = 600, down: float = 1200, flip: float = 5, min_run: float = 0) -> SlewLimiter:
    lim = SlewLimiter(clock)
    lim.up, lim.down, lim.flip_delay = up, down, flip
    lim.min_run = min_run
    return lim


def test_disabled_passes_everything_through() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    lim.enabled = False
    assert lim.limit(-2400) == -2400
    assert lim.limit(2000) == 2000
    assert lim.pending is False


def test_small_step_is_not_held_back() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    assert lim.limit(200) == 200
    clock.now += 2.2
    assert lim.limit(350) == 350


def test_ramp_up_is_limited_per_second() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    lim.limit(0)
    clock.now += 2
    assert lim.limit(2400) == 600  # a start from 0 W: at most one second of ramp (600 W/s) per command
    assert lim.pending is True
    for expected in (1800, 2400):  # running in the same direction: the faster rate (1200 W/s)
        clock.now += 1
        assert lim.limit(2400) == expected
    assert lim.pending is False


def test_start_from_standby_does_not_jump() -> None:
    """A device that was idle for an hour (or never commanded) must still ramp, not jump to full power."""
    clock = _Clock()
    lim = _limiter(clock)
    assert lim.limit(2400) == 600  # never commanded
    lim2 = _limiter(clock)
    lim2.limit(0)
    clock.now += 3600
    assert lim2.limit(1100) == 600
    clock.now += 1
    assert lim2.limit(1100) == 1100


def test_ramp_down_uses_down_rate() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    lim.current, lim._updated, lim._last_dir = -2400, clock.now, -1  # noqa: SLF001
    clock.now += 1
    assert lim.limit(0) == -1200


def test_direction_change_goes_through_zero_and_waits() -> None:
    """Charging 2400 W -> manual 2000 W discharge: ramp down, hold at 0 W, then ramp up."""
    clock = _Clock()
    lim = _limiter(clock)
    lim.current, lim._updated, lim._last_dir = -2400, clock.now, -1  # noqa: SLF001
    seen = []
    for _ in range(20):
        clock.now += 1
        seen.append(lim.limit(2000))
    assert seen[0] == -1200 and seen[1] == 0  # down to 0 W first, never straight to +
    first_positive = next(i for i, v in enumerate(seen) if v > 0)
    assert all(v <= 0 for v in seen[:first_positive])
    # released only flip_delay (5 s) after reaching 0 W
    assert first_positive - 1 >= 5
    assert seen[-1] == 2000


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
    assert lim.limit(0) == -1200


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


def test_ready_start_from_standby_ramps_faster() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    lim.ready_up = 1200
    lim.limit(0)
    clock.now += 60
    assert lim.limit(2200, ready=True) == 1200
    assert lim.boost
    clock.now += 1
    assert lim.limit(2200) == 2200  # the follow-up step keeps the faster rate
    assert not lim.boost  # target reached


def test_ready_start_right_after_charging_is_a_direction_change() -> None:
    """A device that charged a moment ago keeps the normal rate (and the pause at 0 W)."""
    clock = _Clock()
    lim = _limiter(clock)
    lim.ready_up = 1200
    lim.current, lim._updated, lim._last_dir = -500, clock.now, -1  # noqa: SLF001
    clock.now += 1
    assert lim.limit(0) == 0
    clock.now += 6  # flip delay passed, but not yet in standby
    assert lim.limit(2200, ready=True) == 600
    assert not lim.boost


def test_ready_start_after_a_long_standby_in_the_other_direction_is_fast() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    lim.ready_up = 1200
    lim.current, lim._updated, lim._last_dir = -500, clock.now, -1  # noqa: SLF001
    clock.now += 1
    lim.limit(0)
    clock.now += 60
    assert lim.limit(2200, ready=True) == 1200


def test_ready_boost_stops_when_the_target_goes_down() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    lim.ready_up = 1200
    lim.limit(0)
    clock.now += 60
    lim.limit(2200, ready=True)
    clock.now += 1
    lim.limit(800)  # kettle off
    assert not lim.boost
    clock.now += 1
    assert not lim.boost
    assert lim.limit(2200) == 2000  # running in the same direction: faster rate (1200 W/s), not the ready boost


def test_ready_device_already_running_in_the_same_direction_ramps_faster() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    lim.ready_up = 1200
    lim.limit(250)  # covering the base load
    clock.now += 1
    assert lim.limit(2450, ready=True) == 1450  # relay already closed: 1200 W/s
    clock.now += 1
    assert lim.limit(2450, ready=True) == 2450


def test_a_start_from_zero_keeps_the_normal_rate() -> None:
    """Only a start from 0 W (relay switching) uses the normal rate; running devices ramp faster (option D)."""
    clock = _Clock()
    lim = _limiter(clock)
    lim.ready_up = 1200
    lim.limit(0)
    clock.now += 60
    assert lim.limit(2450) == 600  # not the ready device: 600 W/s from 0 W
    clock.now += 1
    assert lim.limit(2450) == 1800  # now running: 1200 W/s


def test_ready_flag_never_speeds_up_a_direction_change() -> None:
    clock = _Clock()
    lim = _limiter(clock)
    lim.ready_up = 1200
    lim.current, lim._updated, lim._last_dir = -800, clock.now, -1  # noqa: SLF001
    clock.now += 1
    assert lim.limit(2000, ready=True) == 0  # down to 0 W first
    assert not lim.boost


def test_follow_a_full_battery_that_stopped_charging() -> None:
    """The device stopped charging because it is full: no direction change pause, a ready start can be fast."""
    clock = _Clock()
    lim = _limiter(clock)
    lim.ready_up = 1200
    lim.current, lim._updated, lim._last_dir = -1200, clock.now, -1  # noqa: SLF001
    lim.follow(0)
    assert lim.current == 0 and not lim.pending
    clock.now += 60
    assert lim.limit(2000, ready=True) == 1200  # standby for 60 s: no flip pause, faster start


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
