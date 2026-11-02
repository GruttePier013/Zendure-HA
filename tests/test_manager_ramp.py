"""Tests for the manager side of the slew limiter: settings, OFF ramp and follow-up timer."""

from __future__ import annotations



from custom_components.zendure_ha.const import CONTROL_DEFAULTS, CONF_TIMEFAST, CONF_TIMEZERO, ManagerMode, SmartMode
from custom_components.zendure_ha.dispatch import RoleSelector
from custom_components.zendure_ha.manager import ZendureManager
from custom_components.zendure_ha.slew import SlewLimiter


class _Dev:
    def __init__(self, ramping_steps: int) -> None:
        self.slew = SlewLimiter()
        self.steps = ramping_steps
        self.calls = 0

    def configure_slew(self, enabled, up, down, flip_delay, min_run, ready_up=1200) -> None:  # noqa: ANN001
        self.slew.enabled, self.slew.up, self.slew.down, self.slew.flip_delay, self.slew.min_run = enabled, up, down, flip_delay, min_run
        self.slew.ready_up = ready_up

    async def power_off_ramped(self) -> bool:
        self.calls += 1
        return self.calls < self.steps


def _manager(devices):  # noqa: ANN001
    m = object.__new__(ZendureManager)
    m.devices = devices
    m._followup = None
    m.roles = RoleSelector()
    m.scheduled = 0
    m._schedule_followup = lambda: setattr(m, "scheduled", m.scheduled + 1)
    return m


def test_apply_settings_uses_defaults_and_overrides() -> None:
    dev = _Dev(1)
    m = _manager([dev])
    try:
        m.apply_settings({"slew_up": 300, "slew_down": 700, "flip_delay": 9, "slew_enabled": False, CONF_TIMEZERO: 6, CONF_TIMEFAST: 9})
        assert (dev.slew.up, dev.slew.down, dev.slew.flip_delay, dev.slew.enabled) == (300, 700, 9, False)
        assert SmartMode.TIMEZERO == 6 and SmartMode.TIMEFAST == 6  # fast is never slower than normal
        m.apply_settings({})
        assert dev.slew.enabled and dev.slew.up == 600 and dev.slew.down == 1200
        assert SmartMode.TIMEZERO == CONTROL_DEFAULTS[CONF_TIMEZERO] and SmartMode.TIMEFAST == CONTROL_DEFAULTS[CONF_TIMEFAST]
    finally:
        m.apply_settings({})


async def test_ramp_off_schedules_followup_while_ramping() -> None:
    dev = _Dev(2)
    m = _manager([dev])
    await m.ramp_off()
    assert m.scheduled == 1
    await m.ramp_off()  # second call finishes the ramp
    assert m.scheduled == 1


async def test_followup_run_in_off_mode_continues_ramp() -> None:
    dev = _Dev(5)
    m = _manager([dev])
    m.operation = ManagerMode.OFF
    await m._followup_run()
    assert dev.calls == 1 and m.scheduled == 1
