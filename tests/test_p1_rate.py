"""1.6.1: diagnostic sensor with the number of P1 values of the last minute."""

from __future__ import annotations

from collections import deque
from time import monotonic
from typing import Any

from custom_components.zendure_ha.const import ManagerMode
from custom_components.zendure_ha.manager import ZendureManager


class _Sensor:
    def __init__(self) -> None:
        self.value: Any = None
        self._attr_extra_state_attributes: dict[str, Any] = {}

    def update_value(self, value: Any) -> bool:
        changed = value != self.value
        self.value = value
        return changed


def _manager() -> ZendureManager:
    m = object.__new__(ZendureManager)
    m.devices = []
    m.operation = ManagerMode.OFF
    m.p1Rate = _Sensor()
    m.p1_source = "ws_v2"
    return m


def test_counts_the_values_of_the_last_minute_only() -> None:
    m = _manager()
    now = monotonic()
    m._p1_stamps = deque(now - s for s in (90, 75, 59, 30, 10, 1))
    assert m.p1_rate(now) == 4
    assert len(m._p1_stamps) == 4  # older values are dropped


def test_no_values_yet_is_zero() -> None:
    assert _manager().p1_rate() == 0


async def test_the_watch_updates_the_sensor_also_when_the_manager_is_off() -> None:
    m = _manager()
    now = monotonic()
    m._p1_stamps = deque(now - s for s in range(60))
    await m._watch()
    assert m.p1Rate.value == 60
    assert m.p1Rate._attr_extra_state_attributes == {"source": "ws_v2"}


async def test_every_handled_p1_value_is_counted(monkeypatch) -> None:  # noqa: ANN001
    m = _manager()
    m.p1_stale, m._net_history, m.p1record = False, None, None
    m.p1_values = 0

    async def nothing(*_args: Any) -> None:
        return None

    monkeypatch.setattr(m, "_watch_stale", nothing)
    monkeypatch.setattr(m, "fast_track", nothing)
    monkeypatch.setattr(m, "_run_power_update", nothing)
    from custom_components.zendure_ha.dispatch import P1Tracker

    m.p1tracker = P1Tracker()
    from datetime import datetime

    m.zero_fast = m.zero_next = datetime.max
    for p1 in (100, 120, 90):
        await m._handle_p1(p1, "ws_v2")
    assert m.p1_rate() == 3
