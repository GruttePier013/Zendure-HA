"""Direction change protection for the power commands sent to a Zendure device.

Up to 1.5.9 this module also limited the speed of a power change (W/s). The SolarFlow 2400 AC+ ramps by
itself (measured on 2 November 2026: about 250 W/s per inverter), so that limit is gone since 1.6.0. What
stays protects the relays:

* A change of direction (charge <-> discharge) always passes through 0 W. The opposite direction is
  released as soon as a fresh measurement (taken at least ``MEASURE_LAG`` s after the stop) confirms
  0 W, but not before ``FLIP_MIN`` s. Without a fresh measurement it is released after ``flip_delay``
  s; when the device still reports power, the limiter waits for 0 W, at most ``FLIP_MAX`` s.
* A device that was just started keeps running at a low power (``HOLD_POWER``) until it has run
  for ``min_run`` seconds, so a short disturbance does not make the relay chatter.
* ``pending`` is True while the command is held back by one of these rules; the manager then runs the
  next cycle about one second later.

Sign convention: positive = discharge (power into the home), negative = charge.
"""

from __future__ import annotations

import time as _time
from collections.abc import Callable

DEFAULT_FLIP_DELAY = 6  # seconds at 0 W before the direction may change when no measurement confirms 0 W
FLIP_MIN = 2.0  # seconds at 0 W before the direction may change, also when 0 W is confirmed
FLIP_MAX = 15.0  # seconds: the longest wait for a device that still reports power after the stop
MEASURE_LAG = 1.0  # seconds: a measurement this soon after the stop may still be from before it
DEFAULT_MIN_RUN = 10  # seconds a started device keeps running before it may return to 0 W (0 = off)
HOLD_POWER = 50  # W, power used to keep a just started device running
ZERO_BAND = 10  # W, a command below this magnitude counts as 0 W (the manager uses +/-10 W as keep-alive)


class SlewLimiter:
    """Hold back a command that would change the direction too soon or stop a device that just started."""

    def __init__(self, clock: Callable[[], float] = _time.monotonic) -> None:
        self._clock = clock
        self.flip_delay: float = DEFAULT_FLIP_DELAY
        self.min_run: float = DEFAULT_MIN_RUN
        self.current: int = 0  # last commanded (limited) power
        self.pending = False  # True while the last command is held back (direction change, minimum run time)
        self._updated: float | None = None
        self._zero_since: float | None = None
        self._zero_confirmed = False  # a fresh measurement after the stop showed 0 W
        self._still_running = False  # a fresh measurement after the stop still showed power
        self._run_start: float | None = None  # when the output left 0 W
        self._last_dir = 0  # direction of the last non-zero command: 1, -1 or 0

    def reset(self) -> None:
        """Forget the command history, e.g. when the device went offline."""
        self.current = 0
        self.pending = False
        self._updated = None
        self._zero_since = None
        self._run_start = None
        self._last_dir = 0
        self._zero_confirmed = self._still_running = False

    def sync(self, measured: int) -> None:
        """Use the measured power as reference when there is no command history yet."""
        if self._updated is None and abs(measured) >= ZERO_BAND:
            self.current = measured
            self._last_dir = 1 if measured > 0 else -1
            self._updated = self._clock()

    def measure(self, measured: int) -> None:
        """Feed the measured power of the device (positive = discharge) to confirm a stop."""
        if abs(self.current) >= ZERO_BAND or self._zero_since is None:
            return
        if self._clock() - self._zero_since < MEASURE_LAG:
            return  # the report may still be from before the stop
        if abs(measured) < ZERO_BAND:
            self._zero_confirmed = True
            self._still_running = False
        elif not self._zero_confirmed:
            self._still_running = True

    def flip_released(self, now: float | None = None) -> bool:
        """True when the device may start in the other direction."""
        if self._zero_since is None:
            return True
        at_zero = (self._clock() if now is None else now) - self._zero_since
        if at_zero < FLIP_MIN:
            return False
        if self._zero_confirmed:
            return True
        if self._still_running:
            return at_zero >= FLIP_MAX
        return at_zero >= self.flip_delay

    def at_zero_for(self, seconds: float) -> bool:
        """True when the command has been 0 W for at least ``seconds`` (or the device was never commanded)."""
        if abs(self.current) >= ZERO_BAND:
            return False
        return self._zero_since is None or self._clock() - self._zero_since >= seconds

    def follow(self, measured: int) -> None:
        """The device does not run the commanded power (its battery is full or empty): continue from the measured power.

        Otherwise the limiter would still think a stopped device is charging (or discharging) and would apply the
        direction change pause when it is asked to go the other way.
        """
        now = self._clock()
        if abs(measured) < ZERO_BAND:
            if abs(self.current) >= ZERO_BAND or self._zero_since is None:
                self._zero_since = now
            self._zero_confirmed = True  # follow() is called with a measured 0 W
            self._still_running = False
            self._run_start = None
            measured = 0
        self.current = measured
        self.pending = False
        self._updated = now

    def limit(self, target: int, hold: bool = True) -> int:
        """Return the power to command now on the way to ``target``.

        ``hold=False`` skips the minimum run time (used when switching a device off on purpose).
        """
        now = self._clock()
        ref = self.current
        goal = target

        # A change of direction goes through 0 W first
        if ref * target < 0 and abs(ref) >= ZERO_BAND:
            goal = 0

        # A device that just started keeps running at a low power for a minimum time
        if hold and goal == 0 and self.min_run > 0 and abs(ref) > ZERO_BAND and self._run_start is not None and now - self._run_start < self.min_run:
            goal = self._last_dir * HOLD_POWER

        # Hold the opposite direction until the device is confirmed at 0 W (or the waiting time is over)
        if goal != 0 and abs(ref) < ZERO_BAND and self._last_dir != 0 and (1 if goal > 0 else -1) != self._last_dir and not self.flip_released(now):
            goal = 0

        return self._store(goal, target, now)

    def _store(self, new: int, target: int, now: float) -> int:
        # the +/-ZERO_BAND keep-alive values of the manager do not count as a running device
        if abs(new) <= ZERO_BAND:
            self._run_start = None
        elif abs(self.current) <= ZERO_BAND:
            self._run_start = now  # the output leaves 0 W
        if abs(new) < ZERO_BAND:
            if abs(self.current) >= ZERO_BAND or self._zero_since is None:
                self._zero_since = now
                self._zero_confirmed = self._still_running = False
        else:
            self._zero_since = None
            self._last_dir = 1 if new > 0 else -1
        self.current = new
        self.pending = new != target
        if not self.pending:
            self._updated = now
        return new
