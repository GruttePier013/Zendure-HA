"""Pure decision logic of the power manager (no Home Assistant imports).

Keeping this logic free of Home Assistant makes it easy to replay a recorded P1 trace in a test
and to reason about it. The manager only feeds it values and acts on the result.
"""

from __future__ import annotations

import time as _time
from collections import deque
from collections.abc import Callable, Sequence
from datetime import datetime
from math import sqrt

DEFAULT_ROLE_MARGIN = 10  # SoC points a device must lead before a role moves to it
ROLE_HOLD = 300  # seconds a role is kept at least before it may move again


class P1Tracker:
    """Detects significant changes of the P1 meter and decides when a power update must run.

    A fast P1 monitor is cheap: it only reads values. The expensive part (sending commands to the
    devices) only runs when the P1 value changed significantly (fast) or the update interval passed.
    """

    def __init__(self) -> None:
        self.history: deque[int] = deque([25, -25], maxlen=8)

    def evaluate(
        self,
        p1: int,
        now: datetime,
        zero_fast: datetime,
        zero_next: datetime,
        stddev_factor: float,
        stddev_min: float,
    ) -> tuple[bool, bool]:
        """Return (run an update now, the change was fast)."""
        # an update is running or has just finished: only collect the value
        if now < zero_fast:
            self.history.append(p1)
            return False, False

        fast = False
        if len(self.history) > 1:
            avg = int(sum(self.history) / len(self.history))
            stddev = stddev_factor * max(stddev_min, sqrt(sum(pow(i - avg, 2) for i in self.history) / len(self.history)))
            if abs(p1 - avg) > stddev or abs(p1 - self.history[0]) > stddev:
                fast = True
                self.history.clear()
        self.history.append(p1)
        return fast or now > zero_next, fast


def peak_setpoint(setpoint: int, limit: int, produced: int = 0) -> int:
    """Peak shaving: let the grid import stay below ``limit`` W before the batteries discharge.

    Solar power that passes through the devices (``produced``) is always delivered.
    ``limit`` 0 disables peak shaving.
    """
    if limit <= 0 or setpoint <= 0:
        return setpoint
    return max(setpoint - limit, min(produced, setpoint))


class RoleSelector:
    """Gives one device the discharge role and another one the charge role.

    With several devices it is better for their lifetime when one device does the discharging
    and another one the charging, instead of every device reversing its direction all the time.
    A role only moves to another device when that device leads by ``margin`` SoC points and the
    role was held for at least ``hold`` seconds, so the roles do not flip-flop.
    """

    def __init__(self, margin: int = DEFAULT_ROLE_MARGIN, hold: float = ROLE_HOLD, clock: Callable[[], float] = _time.monotonic) -> None:
        self.margin = margin
        self.hold = hold
        self._clock = clock
        self.discharger: str | None = None
        self.charger: str | None = None
        self._since_discharger = 0.0
        self._since_charger = 0.0

    def reset(self) -> None:
        self.discharger = self.charger = None

    def update(self, dischargeable: Sequence[tuple[str, int]], chargeable: Sequence[tuple[str, int]]) -> None:
        """Update the roles from the (id, SoC) of the devices that can discharge / charge."""
        now = self._clock()
        self.discharger, self._since_discharger = self._pick(self.discharger, self._since_discharger, dischargeable, True, now)

        # the charger should be another device when there is one
        candidates = [c for c in chargeable if c[0] != self.discharger] or list(chargeable)
        self.charger, self._since_charger = self._pick(self.charger, self._since_charger, candidates, False, now)

    def _pick(self, current: str | None, since: float, candidates: Sequence[tuple[str, int]], highest: bool, now: float) -> tuple[str | None, float]:
        if not candidates:
            return None, since
        best_id, best_soc = (max if highest else min)(candidates, key=lambda c: c[1])
        socs = dict(candidates)
        if current not in socs or self.margin <= 0:
            return best_id, now

        lead = best_soc - socs[current] if highest else socs[current] - best_soc
        if best_id != current and lead >= self.margin and now - since >= self.hold:
            return best_id, now
        return current, since

    @property
    def active(self) -> bool:
        return self.margin > 0
