"""Tests for the manager side of the direction change protection: settings, switching off and the follow-up timer."""

from __future__ import annotations

from custom_components.zendure_ha.const import CONTROL_DEFAULTS, CONF_TIMEFAST, CONF_TIMEZERO, ManagerMode, SmartMode
from custom_components.zendure_ha.dispatch import RoleSelector
from custom_components.zendure_ha.manager import ZendureManager
from custom_components.zendure_ha.slew import DEFAULT_FLIP_DELAY, DEFAULT_MIN_RUN, SlewLimiter


class _Dev:
    def __init__(self) -> None:
        self.slew = SlewLimiter()
        self.stops = 0

    def configure_slew(self, flip_delay, min_run) -> None:  # noqa: ANN001
        self.slew.flip_delay, self.slew.min_run = flip_delay, min_run

    async def stop(self) -> None:
        self.stops += 1


def _manager(devices):  # noqa: ANN001
    m = object.__new__(ZendureManager)
    m.devices = devices
    m._followup = None
    m.roles = RoleSelector()
    m.scheduled = 0
    m._schedule_followup = lambda: setattr(m, "scheduled", m.scheduled + 1)
    return m


def test_apply_settings_uses_defaults_and_overrides() -> None:
    dev = _Dev()
    m = _manager([dev])
    try:
        # the retired ramp settings of 1.5.x are ignored
        m.apply_settings({"slew_up": 300, "slew_down": 700, "flip_delay": 9, "min_run": 4, "slew_enabled": False, CONF_TIMEZERO: 6, CONF_TIMEFAST: 9})
        assert (dev.slew.flip_delay, dev.slew.min_run) == (9, 4)
        assert not hasattr(dev.slew, "up") and not hasattr(dev.slew, "enabled")
        assert SmartMode.TIMEZERO == 6 and SmartMode.TIMEFAST == 6  # fast is never slower than normal
        m.apply_settings({})
        assert (dev.slew.flip_delay, dev.slew.min_run) == (DEFAULT_FLIP_DELAY, DEFAULT_MIN_RUN)
        assert SmartMode.TIMEZERO == CONTROL_DEFAULTS[CONF_TIMEZERO] and SmartMode.TIMEFAST == CONTROL_DEFAULTS[CONF_TIMEFAST]
    finally:
        m.apply_settings({})


async def test_stop_all_switches_every_device_off_at_once() -> None:
    devs = [_Dev(), _Dev()]
    m = _manager(devs)
    await m.stop_all()
    assert [d.stops for d in devs] == [1, 1]
    assert m.scheduled == 0  # nothing to follow up: the inverters ramp down by themselves


async def test_followup_run_in_off_mode_stops_the_devices() -> None:
    dev = _Dev()
    m = _manager([dev])
    m.operation = ManagerMode.OFF
    await m._followup_run()
    assert dev.stops == 1 and m.scheduled == 0
