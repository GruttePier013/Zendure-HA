"""A control cycle that hangs or fails must not block the following cycles."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from custom_components.zendure_ha.const import ManagerMode, SmartMode
from custom_components.zendure_ha.manager import ZendureManager


class _Value:
    def update_value(self, _value: Any) -> bool:
        return True


def _manager(hass: Any) -> ZendureManager:
    m = object.__new__(ZendureManager)
    m.hass = hass
    m.devices = []
    m.fuseGroups = []
    m.charge, m.discharge, m.idle = [], [], []
    m.operation = ManagerMode.MATCHING
    m.last_cycle = None
    m._followup = None
    for name in ("cycleDuration", "cycleInterval", "cycleCount", "lastCycle", "p1Value", "devicesRamping"):
        setattr(m, name, _Value())
    return m


async def test_a_hanging_cycle_is_stopped_and_logged(hass, monkeypatch, caplog) -> None:  # noqa: ANN001
    monkeypatch.setattr(SmartMode, "CYCLE_TIMEOUT", 0.6)
    m = _manager(hass)
    never = asyncio.Event()

    async def wait_for_a_device() -> None:
        await never.wait()

    async def hanging(_p1: int, _fast: bool, _time: datetime) -> None:
        await wait_for_a_device()

    m.powerChanged = hanging  # type: ignore[method-assign]
    await m._run_power_update(100, False, datetime.now())
    assert m.zero_fast != datetime.max  # the lock is released: the next cycle can run
    assert m.cycle_errors == 1
    assert "wait_for_a_device" in (m.last_cycle_stuck or "")
    assert "does not finish" in caplog.text


async def test_a_failing_cycle_is_counted(hass) -> None:  # noqa: ANN001
    m = _manager(hass)

    async def failing(_p1: int, _fast: bool, _time: datetime) -> None:
        raise KeyError("acMode")

    m.powerChanged = failing  # type: ignore[method-assign]
    await m._run_power_update(100, False, datetime.now())
    await m._run_power_update(100, False, datetime.now())
    assert m.cycle_errors == 2
    assert m.cycle_total == 2
    assert "KeyError" in (m.last_cycle_error or "")
