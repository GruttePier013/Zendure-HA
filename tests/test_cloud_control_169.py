"""1.6.9: the changing clouds mode by hand (select auto / on / off) and its own P1 target (clouds)."""

from __future__ import annotations

from datetime import datetime

import pytest
from homeassistant.components.number import NumberMode
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant.common import mock_restore_cache

from custom_components.zendure_ha import events
from custom_components.zendure_ha.entity import CONFIG_KEYS, EntityDevice
from custom_components.zendure_ha.number import ZendureRestoreNumber
from tests.test_clouds import T0, _make_cloudy, _manager
from tests.test_upstream_146 import number_platform  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def _clean_events() -> None:
    events.reset()


class _Sensor:
    hass = None

    def __init__(self) -> None:
        self.values: list[int] = []
        self._attr_extra_state_attributes: dict = {}

    def update_value(self, value: int) -> bool:
        changed = not self.values or self.values[-1] != value
        self.values.append(value)
        return changed


def test_auto_follows_the_detection_and_on_off_overrule_it() -> None:
    m, _ = _manager([40, 50])
    assert m.cloud_mode is False
    m.cloud_control = "on"
    assert m.cloud_mode is True
    _make_cloudy(m)
    m.cloud_control = "off"
    assert m.cloud_mode is False and m._volatility().active  # the detection keeps running
    m.cloud_control = "auto"
    assert m.cloud_mode is True


async def test_select_sets_the_mode_with_an_event_and_the_source() -> None:
    m, _ = _manager([40, 50])
    m.cloudMode = _Sensor()
    await m.update_cloud_control(None, 1)  # the select passes the option key ...
    assert m.cloud_control == "on" and m.cloud_mode
    assert events.counters["cloud_on"] == 1 and "source=user" in events.last["detail"]
    assert m.cloudMode.values[-1] == 1 and m.cloudMode._attr_extra_state_attributes == {"source": "user", "detected": False}
    await m.update_cloud_control(None, "auto")  # ... and its name after a restore
    assert m.cloud_control == "auto" and not m.cloud_mode
    assert events.counters["cloud_off"] == 1 and "source=auto" in events.last["detail"]
    await m.update_cloud_control(None, "bogus")
    assert m.cloud_control == "auto"


def test_forced_off_gives_no_export_margin_and_forced_on_does() -> None:
    m, _ = _manager([40, 50])
    _make_cloudy(m)
    m.cloud_control = "off"
    assert m._cloud_adjust(-1000, T0) == -1000
    m2, _ = _manager([40, 50])
    m2.cloud_control = "on"
    assert m2._cloud_adjust(-1000, T0) == -900  # forced on: the margin applies without detected clouds
    m2.solar_fills = False
    assert m2._cloud_adjust(-1000, T0) == -1000  # but never on a day the batteries will not be full


def test_detection_change_under_an_override_updates_only_the_attributes() -> None:
    m, _ = _manager([40, 50])
    m.cloudMode = _Sensor()
    m.cloud_control = "on"
    _make_cloudy(m)
    m._cloud_adjust(-1000, datetime.fromtimestamp(T0.timestamp() + 1))
    assert events.counters["cloud_on"] == 0  # the mode in effect did not change
    assert m.cloudMode._attr_extra_state_attributes == {"source": "user", "detected": True}


async def test_p1_target_clouds_is_the_export_margin() -> None:
    m, _ = _manager([40, 50])
    m.export_bias = 100
    await m.update_p1_target_clouds(None, -153)
    assert m.export_bias == 150  # steps of 10 W
    assert events.counters["target_change"] == 1 and "target=clouds" in events.last["detail"]
    await m.update_p1_target_clouds(None, 80)  # export only
    assert m.export_bias == 0
    await m.update_p1_target_clouds(None, -9999)
    assert m.export_bias == 500
    await m.update_p1_target_clouds(None, "x")
    assert m.export_bias == 500


def test_both_targets_and_the_select_are_in_the_configuration_section() -> None:
    assert {"p1_target", "p1_target_clouds", "cloud_control"} <= CONFIG_KEYS


@pytest.mark.usefixtures("number_platform")
async def test_a_new_clouds_target_starts_at_the_old_option(hass: HomeAssistant) -> None:
    manager = EntityDevice(hass, "Zendure Manager", "Zendure Manager")
    number = ZendureRestoreNumber(manager, "p1_target_clouds", None, None, "W", "power", 0, -500, NumberMode.BOX, True, default=-60)
    await hass.async_block_till_done()
    assert number.asNumber == -60


@pytest.mark.usefixtures("number_platform")
async def test_a_restored_clouds_target_wins(hass: HomeAssistant) -> None:
    mock_restore_cache(hass, [State("number.zendure_manager_p1_target_clouds", "-200")])
    manager = EntityDevice(hass, "Zendure Manager", "Zendure Manager")
    number = ZendureRestoreNumber(manager, "p1_target_clouds", None, None, "W", "power", 0, -500, NumberMode.BOX, True, default=-60)
    await hass.async_block_till_done()
    assert number.asNumber == -200
