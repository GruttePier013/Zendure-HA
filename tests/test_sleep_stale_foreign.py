"""1.5.9: idle inverters to sleep, a P1 meter that stops sending, writes of another controller."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from time import monotonic
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.zendure_ha import events, manager as manager_module
from custom_components.zendure_ha.const import ManagerMode, SmartMode
from custom_components.zendure_ha.device import ZendureDevice, ZendureZenSdk
from custom_components.zendure_ha.manager import ZendureManager


@pytest.fixture(autouse=True)
def _clean_events() -> None:
    events.reset()


class _Slew:
    def __init__(self, current: int = 0, zero_for: float = 1e9) -> None:
        self.current = current
        self.pending = False
        self._zero_for = zero_for

    def at_zero_for(self, seconds: float) -> bool:
        return self.current == 0 and self._zero_for >= seconds


class _Dev:
    def __init__(self, name: str, current: int = 0, zero_for: float = 1e9, command_age: float = 1e6) -> None:
        self.name = self.deviceId = name
        self.slew = _Slew(current, zero_for)
        self.command_time = monotonic() - command_age
        self.asleep = False
        self.slept = 0
        self.calls: list[str] = []

    async def sleep(self) -> bool:
        self.slept += 1
        self.asleep = True
        return True

    async def power_discharge(self, power: int) -> int:
        self.calls.append(f"discharge {power}")
        self.slew.current = power
        return power

    async def power_charge(self, power: int) -> int:
        self.calls.append(f"charge {power}")
        self.slew.current = power
        return power


def _manager(devices: list[Any], operation: ManagerMode = ManagerMode.MATCHING) -> ZendureManager:
    m = object.__new__(ZendureManager)
    m.devices = devices
    m.idle = list(devices)
    m.operation = operation
    m.roles = SimpleNamespace(discharger=None, charger=None)
    m.cloud_reserve = None
    m.sleep_after = 600.0
    m.zero_fast = datetime.min
    return m


# ---------------------------------------------------------------------------------------------
# sleep
# ---------------------------------------------------------------------------------------------
async def test_idle_inverters_sleep_but_the_discharger_stays_awake() -> None:
    l1, l2, l3 = _Dev("L1"), _Dev("L2"), _Dev("L3", zero_for=120)
    m = _manager([l1, l2, l3])
    m.roles.discharger = "L1"
    await m._sleep_idle()
    assert (l1.slept, l2.slept, l3.slept) == (0, 1, 0)  # L1 ready for the next peak, L3 only 2 minutes at 0 W
    await m._sleep_idle()
    assert l2.slept == 1  # once


async def test_no_sleep_when_switched_off_or_after_a_recent_command() -> None:
    recent = _Dev("L2", command_age=30)
    m = _manager([recent, _Dev("L3")])
    await m._sleep_idle()
    assert recent.slept == 0
    m.sleep_after = 0
    other = _Dev("L1")
    m.idle = [other]
    await m._sleep_idle()
    assert other.slept == 0
    m.sleep_after = 600
    m.operation = ManagerMode.MANUAL
    await m._sleep_idle()
    assert other.slept == 0


async def test_zensdk_sleep_sends_only_the_smart_mode_and_a_command_wakes_it() -> None:
    sent: list[Any] = []

    async def do_command(command: Any) -> bool:
        sent.append(command)
        return True

    d = SimpleNamespace(name="L2", asleep=False, limits_sent=(0, 0), electricLevel=SimpleNamespace(asInt=60), doCommand=do_command)
    d.limits_sent_nonzero = False
    assert await ZendureZenSdk.sleep(d) is True
    assert sent == [{"properties": {"smartMode": 0}}] and d.asleep
    assert await ZendureZenSdk.sleep(d) is False  # already asleep
    assert events.counters["sleep"] == 1


# ---------------------------------------------------------------------------------------------
# P1 meter without values
# ---------------------------------------------------------------------------------------------
def test_fallback_goal_only_lowers_the_power() -> None:
    m = _manager([])
    now = monotonic()
    m._net_history = deque((now, v) for v in [300, 320, 350, 400, 900, 2500, 380, 360, 340, 330])
    assert m.fallback_goal(1000) == 300  # discharging: the demand that was there 90 % of the time
    assert m.fallback_goal(200) == 200  # never more than now
    m._net_history = deque((now, v) for v in [-1500, -1400, -1600, -200, -1450, -1550, -1500, -1480, -1420, -1460])
    assert m.fallback_goal(-2000) == -1400  # charging: the surplus that was there 90 % of the time
    assert m.fallback_goal(-1000) == -1000
    m._net_history = deque((now, v) for v in [300] * 10)
    assert m.fallback_goal(-800) == 0  # charging while the house used power: stop charging from the grid
    m._net_history = deque()
    assert m.fallback_goal(500) == 0


async def test_stale_p1_raises_an_issue_and_falls_back(monkeypatch) -> None:  # noqa: ANN001
    issues: list[str] = []
    monkeypatch.setattr(manager_module.ir, "async_create_issue", lambda _h, _d, issue, **_k: issues.append(f"+{issue}"))
    monkeypatch.setattr(manager_module.ir, "async_delete_issue", lambda _h, _d, issue: issues.append(f"-{issue}"))
    l1, l2 = _Dev("L1", current=600), _Dev("L2", current=400)
    m = _manager([l1, l2])
    m.hass = None
    m.p1_stale = False
    now = monotonic()
    m._net_history = deque((now, 500) for _ in range(10))
    m.p1_time = now - 30
    await m._watch()
    assert issues == [] and l1.calls == []
    m.p1_time = now - 90
    await m._watch()
    assert issues == ["+p1_stale"] and l1.calls == []  # first only the issue, the power stays
    m.p1_time = now - 400
    await m._watch()
    assert l1.calls == ["discharge 300"] and l2.calls == ["discharge 200"]  # 1000 W -> 500 W, in proportion
    await m._watch()
    assert l1.calls == ["discharge 300"]  # nothing more to lower
    m._p1_back()
    assert issues[-1] == "-p1_stale" and not m.p1_stale


# ---------------------------------------------------------------------------------------------
# another controller
# ---------------------------------------------------------------------------------------------
def _sdk(sent: tuple[int, int], command_age: float = 60) -> Any:
    d = SimpleNamespace(name="L1", limits_sent=sent, command_time=monotonic() - command_age, foreign_writes=[])
    return d


def test_foreign_write_needs_our_value_taken_first() -> None:
    d = _sdk((800, 0))
    ZendureDevice._check_foreign(d, "outputLimit", 790)  # within the tolerance: confirmed
    ZendureDevice._check_foreign(d, "outputLimit", 790)
    assert d.foreign_writes == []
    ZendureDevice._check_foreign(d, "outputLimit", 200)  # someone else wrote 200 W
    assert len(d.foreign_writes) == 1 and events.counters["foreign_write"] == 1
    ZendureDevice._check_foreign(d, "outputLimit", 200)  # the same value again: one write
    assert len(d.foreign_writes) == 1


def test_rounding_or_clamping_of_the_device_is_not_a_foreign_write() -> None:
    d = _sdk((2400, 0))
    ZendureDevice._check_foreign(d, "outputLimit", 2200)  # clamped to the device limit, never confirmed
    assert d.foreign_writes == []
    d = _sdk((800, 0), command_age=1)
    d._limit_confirmed = {"outputLimit": 800}
    ZendureDevice._check_foreign(d, "outputLimit", 0)  # within the report lag after our own command
    assert d.foreign_writes == []


def test_second_controller_issue_after_five_writes(monkeypatch) -> None:  # noqa: ANN001
    issues: list[str] = []
    monkeypatch.setattr(manager_module.ir, "async_create_issue", lambda _h, _d, issue, **_k: issues.append(f"+{issue}"))
    monkeypatch.setattr(manager_module.ir, "async_delete_issue", lambda _h, _d, issue: issues.append(f"-{issue}"))
    now = monotonic()
    d = SimpleNamespace(name="L1", foreign_writes=[now - 10 * i for i in range(SmartMode.FOREIGN_COUNT - 1)])
    m = _manager([d])
    m.hass = None
    m._check_foreign_issue()
    assert issues == []
    d.foreign_writes.append(now)
    m._check_foreign_issue()
    m._check_foreign_issue()
    assert issues == ["+second_controller"]
    d.foreign_writes = [now - SmartMode.FOREIGN_CLEAR - 1]
    m._check_foreign_issue()
    assert issues == ["+second_controller", "-second_controller"]


# ---------------------------------------------------------------------------------------------
# a cycle at once after a mode change
# ---------------------------------------------------------------------------------------------
async def test_mode_change_starts_a_cycle_on_the_next_p1_value() -> None:
    m = _manager([], ManagerMode.OFF)
    m.p1meterEvent = None
    m.zero_next = m.zero_fast = datetime(2099, 1, 1)  # the slow cadence of the off mode
    await m.update_operation(SimpleNamespace(value=ManagerMode.MATCHING.value), None)
    assert m.zero_next == m.zero_fast == datetime.min
    m.zero_fast = datetime.max  # a cycle runs: leave it alone
    await m.update_operation(SimpleNamespace(value=ManagerMode.OFF.value), None)
    assert m.zero_fast == datetime.max


# ---------------------------------------------------------------------------------------------
# 1.6.0: devices asleep as a sensor and an activity
# ---------------------------------------------------------------------------------------------
class _Count:
    def __init__(self) -> None:
        self.value = None
        self._attr_extra_state_attributes: dict[str, Any] = {}

    def update_value(self, value: Any) -> bool:
        changed = value != self.value
        self.value = value
        return changed


def test_idle_and_asleep_are_counted_apart() -> None:
    awake, asleep = _Dev("L1"), _Dev("L2")
    asleep.asleep = True
    m = _manager([awake, asleep])
    m.devicesIdle, m.devicesSleep = _Count(), _Count()
    m._set_devices(m.devicesIdle, [d for d in m.idle if not d.asleep])
    m._set_devices(m.devicesSleep, [d for d in m.idle if d.asleep])
    assert (m.devicesIdle.value, m.devicesSleep.value) == (1, 1)
    assert m.devicesSleep._attr_extra_state_attributes == {"devices": ["L2"]}
    m.charge, m.discharge = [], []
    assert m._activity(awake) == "standby" and m._activity(asleep) == "sleeping"
    m.cloud_reserve = "L2"
    assert m._activity(asleep) == "reserve"


async def test_retired_ramp_settings_are_removed_by_the_migration(hass) -> None:  # noqa: ANN001
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.zendure_ha import async_migrate_entry
    from custom_components.zendure_ha.const import DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, version=1, minor_version=8,
                            data={"slew_enabled": True, "slew_up": 1000, "slew_down": 1600, "ready_rate": 1600, "flip_delay": 6, "min_run": 10})  # fmt: skip
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert dict(entry.data) == {"flip_delay": 6, "min_run": 10}
    assert entry.minor_version == 9


async def test_retired_entities_are_removed(hass) -> None:  # noqa: ANN001
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.zendure_ha import remove_retired_entities
    from custom_components.zendure_ha.const import DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for platform, uid in (("number", "l1_solarflow_2400ac_slew_factor"), ("binary_sensor", "l1_solarflow_2400ac_slew_active"),
                          ("sensor", "zendure_manager_devices_ramping"), ("sensor", "zendure_manager_devices_idle")):  # fmt: skip
        registry.async_get_or_create(platform, DOMAIN, uid, config_entry=entry)
    assert remove_retired_entities(hass, entry) == 3
    left = [e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)]
    assert left == ["zendure_manager_devices_idle"]


# ---------------------------------------------------------------------------------------------
# 1.6.3: ready hours for the discharger, sleep after 10 minutes by default
# ---------------------------------------------------------------------------------------------
def test_parse_hours() -> None:
    from custom_components.zendure_ha.dispatch import in_hours, parse_hours

    assert parse_hours("7-9, 12-13,17:30-19") == [(7, 9), (12, 13), (17.5, 19)]
    assert parse_hours("") == []
    for bad in ("7", "9-7", "7-25", "a-b", "7:75-8"):
        with pytest.raises(ValueError):
            parse_hours(bad)
    hours = parse_hours("7-9,17:30-19")
    assert in_hours(hours, datetime(2026, 11, 3, 8, 59))
    assert not in_hours(hours, datetime(2026, 11, 3, 9, 0))
    assert in_hours(hours, datetime(2026, 11, 3, 17, 45))
    assert in_hours([], datetime(2026, 11, 3, 3, 0))  # no hours: always


class _Waker(_Dev):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.woken = 0

    async def wake(self) -> bool:
        self.woken += 1
        self.asleep = False
        return True


async def test_discharger_sleeps_outside_the_ready_hours_and_is_woken_at_the_start() -> None:
    discharger = _Waker("L1")
    m = _manager([discharger])
    m.roles.discharger = "L1"
    m.ready_hours = [(17.0, 19.0)]
    await m._sleep_idle(datetime(2026, 11, 3, 15, 0))  # outside: it may sleep
    assert discharger.slept == 1 and discharger.asleep
    await m._sleep_idle(datetime(2026, 11, 3, 16, 59))
    assert discharger.woken == 0
    await m._sleep_idle(datetime(2026, 11, 3, 17, 0))  # a ready hour starts: woken
    assert discharger.woken == 1 and not discharger.asleep
    await m._sleep_idle(datetime(2026, 11, 3, 17, 30))  # and kept awake
    assert discharger.slept == 1


async def test_without_ready_hours_the_discharger_always_stays_awake() -> None:
    discharger = _Waker("L1")
    m = _manager([discharger])
    m.roles.discharger = "L1"
    m.ready_hours = []
    await m._sleep_idle(datetime(2026, 11, 3, 3, 0))
    assert discharger.slept == 0 and discharger.woken == 0


async def test_zensdk_wake_sends_smart_mode_1_at_0_w() -> None:
    calls: list[tuple] = []

    async def power_command(mode: int, output: int, input_: int, persist: bool = False) -> None:
        calls.append((mode, output, input_))
        d.asleep = False

    d = SimpleNamespace(name="L1", asleep=True, ac_mode_sent=2, _power_command=power_command)
    assert await ZendureZenSdk.wake(d) is True
    assert calls == [(2, 0, 0)] and events.counters["wake"] == 1
    assert await ZendureZenSdk.wake(d) is False


def test_sleep_after_10_minutes_by_default() -> None:
    m = _manager([])
    m.apply_settings({})
    assert m.sleep_after == 600 and m.ready_hours == []
    m.apply_settings({"sleep_after": 0, "ready_hours": "7-9"})
    assert m.sleep_after == 0 and m.ready_hours == [(7, 9)]
    m.apply_settings({"ready_hours": "nonsense"})
    assert m.ready_hours == []  # an invalid value (not possible via the options) counts as no hours
    m.apply_settings({})
