"""Initialize the Zendure component."""

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .api import Api
from .binary_sensor import ZendureBinarySensor
from .button import ZendureButton
from .const import CONF_MQTTLOG, CONF_P1METER, CONF_SIM
from .device import ZendureDevice
from .entity import EntityDevice
from .manager import ZendureConfigEntry, ZendureManager
from .migration import Migration
from .number import ZendureNumber
from .select import ZendureSelect
from .sensor import ZendureSensor
from .switch import ZendureSwitch

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ZendureConfigEntry) -> bool:
    """Migrate config entry to new version."""
    if entry.version == 1 and entry.minor_version < 8:
        _LOGGER.info("Migrating Zendure config entry from version %s.%s", entry.version, entry.minor_version)
        await Migration.async_migrate(hass, entry.entry_id)
    hass.config_entries.async_update_entry(entry, version=1, minor_version=8)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ZendureConfigEntry) -> bool:
    """Set up Zendure as config entry.

    The manager is loaded first: when the Zendure API is not reachable ConfigEntryNotReady is raised
    before anything was set up, and Home Assistant retries later. Entities created by the manager wait
    in a queue (EntityAdder) until the platforms are forwarded.
    """
    await EntityDevice.async_load_translations(hass)
    manager = ZendureManager(hass, entry)
    try:
        await manager.loadDevices()
    except BaseException:
        await manager.async_shutdown_manager()
        await Api.stop(hass)
        raise
    entry.runtime_data = manager
    entry.async_on_unload(manager.async_shutdown_manager)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await manager.async_config_entry_first_refresh()
    # no update listener: the options flow applies its settings itself and schedules a reload only when needed
    # (Home Assistant 2026.12 no longer allows an update listener together with a reload requested by the flow)
    return True


def apply_entry_settings(entry: ZendureConfigEntry) -> None:
    """Apply the settings that do not need a reload to the running integration."""
    _LOGGER.debug("Applying Zendure settings: %s", entry.entry_id)
    Api.mqttLogging = entry.data.get(CONF_MQTTLOG, False)
    ZendureManager.simulation = entry.data.get(CONF_SIM, False)
    if (manager := getattr(entry, "runtime_data", None)) is None:
        return
    manager.update_p1meter(entry.data.get(CONF_P1METER, "sensor.power_actual"), entry.data)
    manager.apply_settings(entry.data)


async def async_unload_entry(hass: HomeAssistant, entry: ZendureConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Zendure config entry: %s", entry.entry_id)
    result = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if result:
        manager = entry.runtime_data
        await manager.async_shutdown_manager()
        await Api.stop(hass)  # stops the MQTT threads and forgets the devices
        for platform_class in (ZendureBinarySensor, ZendureButton, ZendureNumber, ZendureSelect, ZendureSensor, ZendureSwitch):
            if hasattr(platform_class.add, "detach"):
                platform_class.add.detach()
        manager.update_p1meter(None)
        manager.apply_settings({})  # restore the default control constants
        manager.fuseGroups.clear()
        manager.devices.clear()
    return result


async def async_remove_config_entry_device(_hass: HomeAssistant, entry: ZendureConfigEntry, device_entry: dr.DeviceEntry) -> bool:
    """Remove a device from a config entry."""
    manager = entry.runtime_data

    # check for device to remove
    for d in manager.devices:
        if d.name == device_entry.name:
            manager.devices.remove(d)
            return True

        if isinstance(d, ZendureDevice) and (bat := next((b for b in d.batteries.values() if b.name == device_entry.name), None)) is not None:
            d.batteries.pop(bat.deviceId)
            return True

    return True