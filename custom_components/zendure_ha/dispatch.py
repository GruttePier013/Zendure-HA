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
ROLE_GUARD = 10  # usable SoC points: a role holder this close to its limit gives the role away at once (1.6.5)
ROLE_GUARD_LEAD = 3  # ... to a device that leads by at least this much (so two almost empty devices do not swap)


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
        """Update the roles from the (id, SoC) of the devices that can discharge / charge.

        The manager passes the usable SoC: for discharging the SoC above the lower limit (minSoc), for charging
        the SoC plus the room below the upper limit (100 - socSet), so the device with the most room has the lowest value.
        """
        now = self._clock()
        self.discharger, self._since_discharger = self._pick(self.discharger, self._since_discharger, dischargeable, True, now)

        # the charger is always another device: with one device there is no role split
        candidates = [c for c in chargeable if c[0] != self.discharger]
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
        # 1.6.5: the role holder is almost empty (or almost full for the charge role): the role moves at once,
        # without the margin and the hold time, to a device that has clearly more room (4 November: L2 kept the
        # discharge role at 7 % while L1 and L3 charged at 17 %, with a margin of 15)
        near_limit = socs[current] < ROLE_GUARD if highest else socs[current] > 100 - ROLE_GUARD
        if best_id != current and near_limit and lead >= ROLE_GUARD_LEAD:
            return best_id, now
        return current, since

    @property
    def active(self) -> bool:
        return self.margin > 0


# ------------------------------------------------------------------------------------------------
# changing clouds: detection of a volatile period
# ------------------------------------------------------------------------------------------------

VOLATILE_EDGE = 800  # W: largest change of the net demand within EDGE_WINDOW that is needed to count as a cloud edge
EDGE_MIN = 300  # W: smallest edge threshold (low sun)
EDGE_SHARE = 0.5  # the edge threshold is this share of the recent peak surplus, between EDGE_MIN and VOLATILE_EDGE
EDGE_WINDOW = 15.0  # s
VOLATILE_WINDOW = 600.0  # s: period in which the edges are counted
VOLATILE_EDGES = 6  # edges in VOLATILE_WINDOW (an appliance on + off is 2 edges, a passing cloud too)
VOLATILE_OFF_EDGES = 4  # 1.6.5: the mode ends only at this many edges or fewer (6 on / 5 off flapped within seconds; 4 simulated best)
SUN_WINDOW = 900.0  # s: period of the peak surplus
SUN_MIN = 200  # W: below this peak surplus there is no sun to speak of: no cloud mode (evening, night)


class Volatility:
    """Detect a period of changing clouds from the net demand (P1 plus what the batteries do).

    Clouds only matter while the sun produces more than the house uses, and a cloud can only take away
    what the sun gives. So the size of an edge is compared with the recent peak surplus: with 900 W of
    sun a cloud is a change of a few hundred watts, with 4500 W of sun it is much more. Without a
    surplus (evening, night) appliances switching on and off never start the cloud mode.
    """

    def __init__(self) -> None:
        self._samples: deque[tuple[float, int]] = deque()
        self._edges: deque[float] = deque()
        self._last_edge = float("-inf")
        self._active = False
        self._surplus: deque[tuple[float, int]] = deque()  # (t, surplus) with a falling maximum

    def add(self, t: float, demand: int) -> None:
        # peak surplus over SUN_WINDOW (monotonic deque: the front is the maximum)
        surplus = max(0, -demand)
        while self._surplus and self._surplus[-1][1] <= surplus:
            self._surplus.pop()
        self._surplus.append((t, surplus))
        while t - self._surplus[0][0] > SUN_WINDOW:
            self._surplus.popleft()

        self._samples.append((t, demand))
        while self._samples and t - self._samples[0][0] > EDGE_WINDOW:
            self._samples.popleft()
        values = [v for _, v in self._samples]
        if self.peak_surplus >= SUN_MIN and max(values) - min(values) >= self.threshold and t - self._last_edge >= EDGE_WINDOW:
            self._edges.append(t)
            self._last_edge = t
        while self._edges and t - self._edges[0] > VOLATILE_WINDOW:
            self._edges.popleft()
        if self.peak_surplus < SUN_MIN:
            self._active = False  # no sun to speak of (evening): no cloud mode
        elif not self._active and len(self._edges) >= VOLATILE_EDGES:
            self._active = True
        elif self._active and len(self._edges) <= VOLATILE_OFF_EDGES:
            self._active = False

    @property
    def peak_surplus(self) -> int:
        """The largest surplus (W, PV minus house) in the last SUN_WINDOW."""
        return self._surplus[0][1] if self._surplus else 0

    @property
    def threshold(self) -> int:
        """The change of the net demand (W) that counts as a cloud edge now."""
        return int(min(VOLATILE_EDGE, max(EDGE_MIN, EDGE_SHARE * self.peak_surplus)))

    @property
    def edges(self) -> int:
        return len(self._edges)

    @property
    def active(self) -> bool:
        """On at VOLATILE_EDGES edges, off at VOLATILE_OFF_EDGES or fewer, or when the sun is gone."""
        return self._active


def parse_hours(text: str) -> list[tuple[float, float]]:
    """Hours of the day from text like "7-9, 12-13, 17:30-19" (local time); "" gives []. ValueError when invalid."""
    out: list[tuple[float, float]] = []
    for part in str(text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            raise ValueError(part)
        start, end = (_hour(x) for x in part.split("-", 1))
        if not 0 <= start < end <= 24:
            raise ValueError(part)
        out.append((start, end))
    return out


def _hour(text: str) -> float:
    text = text.strip()
    if ":" in text:
        h, m = text.split(":", 1)
        hours, minutes = int(h), int(m)
        if not 0 <= minutes < 60:
            raise ValueError(text)
        return hours + minutes / 60
    return float(int(text))


def in_hours(hours: Sequence[tuple[float, float]], when: datetime) -> bool:
    """True when ``when`` (local time) falls in one of the hours; an empty list means always."""
    if not hours:
        return True
    h = when.hour + when.minute / 60
    return any(a <= h < b for a, b in hours)
