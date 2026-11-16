"""Changes taken over from Zendure-HA up to 1.4.6 (1.6.8).

- AB1000S detected as I8000 when the parent reports packType 70 (upstream #1765)
- fuse groups 600 W / 1200 W and 600 W / 2400 W (upstream #1739)
- a restored number (manual power) is not overwritten right after the restore (upstream #1764)
- store solar charges without the charge delay (upstream #1665)
- raw device properties in the Diagnostic section, with icons (upstream #1760)
- a failing entity migration does not stop the setup of the other entities (upstream #1760)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.components.number import NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.entity_platform import EntityPlatform
from pytest_homeassistant.common import mock_restore_cache

from custom_components.zendure_ha.const import ManagerMode
from custom_components.zendure_ha.device import ZendureBattery
from custom_components.zendure_ha.entity import EntityDevice
from custom_components.zendure_ha.manager import ZendureManager
from custom_components.zendure_ha.number import ZendureNumber, ZendureRestoreNumber
from tests.test_manager_dispatch import _make_manager_harness as _make_dispatch_harness

T0 = datetime(2026, 11, 16, 12, 0, 0)


# ---------------------------------------------------------------------------------------------
# AB1000S / I8000 (upstream #1765)
# ---------------------------------------------------------------------------------------------
def test_pack_type_70_on_an_sf800_is_an_ab1000s() -> None:
    _, model, kwh = ZendureBattery.get_battery_type("BO4H12345", 70, parent_is_sf4000=False)
    assert (model, kwh) == ("AB1000S", 0.96)


def test_pack_type_70_on_an_sf4000_is_the_internal_i8000() -> None:
    _, model, kwh = ZendureBattery.get_battery_type("BO4H12345", 70, parent_is_sf4000=True)
    assert (model, kwh) == ("I8000", 8.0)


# ---------------------------------------------------------------------------------------------
# fuse groups 600 W (upstream #1739)
# ---------------------------------------------------------------------------------------------
def _fuse_manager(devices: list) -> ZendureManager:  # noqa: ANN001
    manager = object.__new__(ZendureManager)
    manager.devices = devices
    manager.fuseGroups = []
    manager.operation = ManagerMode.OFF
    return manager


@pytest.mark.parametrize("option", ["group600_1200", "group600_2400"])
async def test_group600_is_a_selectable_option(zensdk_device, option: str) -> None:  # noqa: ANN001
    assert option in zensdk_device.fuseGroup.options


@pytest.mark.parametrize(("option", "charge"), [("group600_1200", -1200), ("group600_2400", -2400)])
async def test_group600_limits_device_to_600_out(zensdk_device, option: str, charge: int) -> None:  # noqa: ANN001
    zensdk_device.setLimits(-2400, 2400)
    zensdk_device.fuseGroup._attr_current_option = option
    await _fuse_manager([zensdk_device]).update_fusegroups()

    fg = zensdk_device.fuseGrp
    assert (fg.maxpower, fg.minpower) == (600, charge)
    assert fg.discharge_limit(zensdk_device) == 600


@pytest.mark.parametrize("option", ["group800", "group800_2400", "group1200", "group5000"])
async def test_existing_fuse_groups_keep_their_limits(zensdk_device, option: str) -> None:  # noqa: ANN001
    """The options are renumbered; the choice is restored by name, so a saved group stays the same group."""
    limits = {"group800": (800, -1200), "group800_2400": (800, -2400), "group1200": (1200, -1200), "group5000": (5000, -5000)}
    zensdk_device.setLimits(-2400, 2400)
    zensdk_device.fuseGroup._attr_current_option = option
    await _fuse_manager([zensdk_device]).update_fusegroups()
    assert (zensdk_device.fuseGrp.maxpower, zensdk_device.fuseGrp.minpower) == limits[option]


# ---------------------------------------------------------------------------------------------
# restored number (upstream #1764)
# ---------------------------------------------------------------------------------------------
ENTITY_ID = "number.zendure_manager_manual_power"


@pytest.fixture
def number_platform(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> EntityPlatform:
    """Route ZendureNumber.add through a real EntityPlatform, as number.async_setup_entry does."""
    platform = EntityPlatform(
        hass=hass,
        logger=logging.getLogger(__name__),
        domain="number",
        platform_name="zendure_ha",
        platform=None,
        scan_interval=timedelta(seconds=30),
        entity_namespace=None,
    )
    monkeypatch.setattr(ZendureNumber, "add", staticmethod(platform._async_schedule_add_entities))  # noqa: SLF001
    return platform


def _make_manual_power(hass: HomeAssistant) -> ZendureRestoreNumber:
    manager = EntityDevice(hass, "Zendure Manager", "Zendure Manager")
    return ZendureRestoreNumber(manager, "manual_power", None, None, "W", "power", 12000, -12000, NumberMode.BOX, True)


@pytest.mark.usefixtures("number_platform")
@pytest.mark.parametrize("restored", [100, -800])
async def test_restored_manual_power_is_the_value_the_manager_reads(hass: HomeAssistant, restored: int) -> None:
    mock_restore_cache(hass, [State(ENTITY_ID, str(restored))])
    number = _make_manual_power(hass)
    await hass.async_block_till_done()
    assert number.asNumber == restored


@pytest.mark.usefixtures("number_platform")
async def test_manual_power_defaults_to_zero_without_restored_state(hass: HomeAssistant) -> None:
    number = _make_manual_power(hass)
    await hass.async_block_till_done()
    assert number.asNumber == 0


# ---------------------------------------------------------------------------------------------
# store solar without the charge delay (upstream #1665)
# ---------------------------------------------------------------------------------------------
def _charging_manager(operation: ManagerMode) -> ZendureManager:
    m = _make_dispatch_harness()
    m.devices = [SimpleNamespace(online=True, homeInput=SimpleNamespace(asInt=1500), slew=SimpleNamespace(current=-1500))]
    m.p1meterEvent = None
    m.zero_fast = T0
    m.operation = operation
    return m


async def test_store_solar_to_smart_while_charging_starts_no_charge_delay() -> None:
    m = _charging_manager(ManagerMode.STORE_SOLAR)
    await m.update_operation(SimpleNamespace(value=ManagerMode.MATCHING.value), None)
    assert m.charge_time != datetime.max  # it already charges: the first smart cycle goes on charging


async def test_between_other_smart_modes_the_charge_delay_is_unchanged() -> None:
    m = _charging_manager(ManagerMode.MATCHING_CHARGE)
    await m.update_operation(SimpleNamespace(value=ManagerMode.MATCHING.value), None)
    assert m.charge_time == datetime.max


# ---------------------------------------------------------------------------------------------
# raw device properties (upstream #1760)
# ---------------------------------------------------------------------------------------------
async def test_raw_property_goes_to_diagnostic_with_an_icon(zensdk_device) -> None:  # noqa: ANN001
    zensdk_device.entityUpdate("smartMode", 1)
    entity = zensdk_device.entities["smartMode"]
    assert entity.entity_category == EntityCategory.DIAGNOSTIC
    assert entity.icon == "mdi:robot"


async def test_meaningful_raw_property_stays_in_the_main_list(zensdk_device) -> None:  # noqa: ANN001
    zensdk_device.entityUpdate("is_error", 0)
    entity = zensdk_device.entities["is_error"]
    assert entity.entity_category is None
