"""1.5.8: the P1 target band, clearing residual commands and the recorded state during a cycle."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.zendure_ha import events
from custom_components.zendure_ha.const import DeviceState, ManagerMode
from custom_components.zendure_ha.manager import ZendureManager


@pytest.fixture(autouse=True)
def _clean_events() -> None:
    events.reset()


def _device(current: int = 0, name: str = "L1") -> Any:
    return SimpleNamespace(deviceId=name, name=name, slew=SimpleNamespace(current=current, pending=False))


def _manager(target: int, devices: list[Any]) -> ZendureManager:
    m = object.__new__(ZendureManager)
    m.devices = devices
    m.p1_target = target
    return m


@pytest.mark.parametrize(
    ("target", "p1", "running", "expected"),
    [
        (0, 37, -300, 37),  # no target: P1 itself
        (-50, -30, -300, 0),  # charging, inside the band: no correction
        (-50, 0, -300, 0),
        (-50, -50, -300, 0),
        (-50, -120, -300, -70),  # more export than the band: charge more, back to the target
        (-50, 40, -300, 40),  # import while charging: charge less, to 0 W
        (-50, -30, 0, 0),  # idle, inside the band: nothing starts
        (-50, -120, 0, -70),  # idle, real export: the charger starts for what is outside the band
        (-50, 40, 0, 40),  # idle, import: start for the import only, not to create export
        (-50, -30, 300, -30),  # discharging: no band, zero at the meter (no stored energy to the grid)
        (-50, 40, 300, 40),
        (30, 20, 300, 0),  # positive target: a band of a little import while discharging
        (30, 100, 300, 70),
        (30, -40, 300, -40),  # export while discharging: back to 0 W
        (30, 20, -300, 20),  # charging: no band (it would buy energy to store)
    ],
)
def test_band(target: int, p1: int, running: int, expected: int) -> None:
    assert _manager(target, [_device(running)]).p1_band(p1) == expected


async def test_target_entity_rounds_limits_and_logs(hass) -> None:  # noqa: ANN001
    m = _manager(0, [])
    m.hass = hass
    await m.update_p1_target(None, -52.4)
    assert m.p1_target == -50
    await m.update_p1_target(None, -900)
    assert m.p1_target == -200
    assert events.counters["target_change"] == 2


def test_target_and_cloud_margin_do_not_add_up(monkeypatch) -> None:  # noqa: ANN001
    m = _manager(-50, [])
    m.export_bias = 100
    monkeypatch.setattr(m, "_volatility", lambda: SimpleNamespace(peak_surplus=3000))
    assert m.cloud_margin == 50  # 50 W comes from the target, 50 W more from the margin: 100 W in total
    m.p1_target = -150
    assert m.cloud_margin == 0


class _Slew:
    def __init__(self, current: int) -> None:
        self.current = current
        self.pending = False


class _Dev:
    def __init__(self, current: int, age: float, limit: int) -> None:
        from time import monotonic

        self.name = self.deviceId = "L2"
        self.slew = _Slew(current)
        self.command_time = monotonic() - age
        self.limitOutput = SimpleNamespace(asInt=limit)
        self.limitInput = SimpleNamespace(asInt=0)
        self.calls: list[str] = []

    async def power_discharge(self, power: int) -> int:
        self.calls.append(f"power_discharge {power}")
        self.slew.current = power
        return power

    async def power_charge(self, power: int) -> int:
        self.calls.append(f"power_charge {power}")
        self.slew.current = power
        return power

    async def discharge(self, power: int) -> int:
        self.calls.append(f"discharge {power}")
        return power


async def test_residual_command_on_a_standby_device_is_cleared() -> None:
    stale = _Dev(19, age=60, limit=19)  # 2 November: 19 W left after a hand-over
    fresh = _Dev(50, age=1, limit=50)  # just started at POWER_START: keep
    running = _Dev(300, age=60, limit=300)  # not a residual
    m = _manager(0, [])
    m.idle = [stale, fresh, running]
    await m._clear_residual()
    assert stale.calls == ["power_discharge 0", "discharge 0"]
    assert fresh.calls == [] and running.calls == []


def test_recording_uses_the_last_finished_cycle_while_a_cycle_runs() -> None:
    d = SimpleNamespace(
        deviceId="L1", name="L1", state=DeviceState.INACTIVE, electricLevel=SimpleNamespace(asInt=60),
        homeOutput=SimpleNamespace(asInt=300), homeInput=SimpleNamespace(asInt=0), slew=SimpleNamespace(current=300, pending=False),
    )  # fmt: skip
    m = _manager(0, [d])
    m.operation = ManagerMode.MATCHING
    m.charge, m.discharge, m.idle = [], [], []  # cleared at the start of a cycle
    m.zero_fast = datetime.max  # a cycle runs
    m.cloud_reserve = None
    m._record_states = {"L1": "discharging"}
    columns = m.record_columns(0, 300)
    assert columns[-4] == "discharging"  # not "offline"
