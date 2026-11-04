"""Smoke test: the real manager can be constructed and exposes its diagnostic entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zendure_ha.const import DOMAIN
from custom_components.zendure_ha.manager import ZendureManager


async def test_manager_diagnostic_entities(hass, enable_custom_integrations) -> None:  # noqa: ANN001
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    manager = ZendureManager(hass, entry)
    with (
        patch("custom_components.zendure_ha.manager.Api.Connect", AsyncMock(return_value={"mqtt": {}, "deviceList": []})),
        patch.object(manager.api, "Init", MagicMock()),
        patch.object(manager, "update_p1meter", MagicMock()),
        # mDNS discovery would open real sockets (zeroconf); it is not what this test is about
        patch("custom_components.zendure_ha.manager.ZendureDiscovery.async_start", AsyncMock()),
    ):
        await manager.loadDevices()

    diagnostics = [
        manager.cycleDuration, manager.cycleInterval, manager.cycleCount, manager.lastCycle, manager.p1Value, manager.p1Rate, manager.dispatchSetpoint,
        manager.devicesOnline, manager.devicesCharging, manager.devicesDischarging, manager.devicesIdle, manager.devicesSleep,
        manager.mqttCloudConnected, manager.mqttLocalConnected,
    ]  # fmt: skip
    assert all(e.entity_category is EntityCategory.DIAGNOSTIC for e in diagnostics)
    assert manager.globalSoc.entity_category is None

    # entities are not added to hass here, so only check that updating does not raise
    manager.cycleCount.update_value(1)
    manager.mqttCloudConnected.update_value(1)
    await manager.async_shutdown_manager()  # stop the timers the manager started
