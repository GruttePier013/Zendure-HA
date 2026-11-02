"""Smaller regression tests: migration, simulation log, mqtt password, sensor deadband, timezone."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from custom_components.zendure_ha.api import mqtt_password
from custom_components.zendure_ha.migration import replace_entity_ids
from custom_components.zendure_ha.simlog import SimulationLog


def test_migration_replaces_whole_entity_ids_only() -> None:
    text = "a: sensor.x_power\nb: sensor.x_power_limit\nc: input.sensor.x_power\nd: sensor.x_power"
    result = replace_entity_ids(text, [("sensor.x_power", "sensor.new")])
    assert "b: sensor.x_power_limit" in result  # untouched
    assert "a: sensor.new" in result and "d: sensor.new" in result
    assert "input.sensor.x_power" in result  # part of another id


def test_mqtt_password_is_the_firmware_scheme() -> None:
    assert mqtt_password("abc") == "900150983CD24FB0D6963F7D28E17F72"[8:24]


async def test_simulation_log_writes_in_batches_and_rotates(hass, tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "simulation.csv"
    log = SimulationLog(hass, str(path))
    for i in range(5):
        log.add(f"line{i}\n", "header\n")
    assert not path.exists()  # nothing is written per line
    assert log.pending == 5
    await log.flush()
    assert path.read_text() == "header\nline0\nline1\nline2\nline3\nline4\n"

    import custom_components.zendure_ha.simlog as simlog

    old = simlog.MAX_BYTES
    simlog.MAX_BYTES = 10
    try:
        log.add("next\n", "header\n")
        await log.flush()
    finally:
        simlog.MAX_BYTES = old
    assert (tmp_path / "simulation.csv.1").exists()
    assert path.read_text() == "header\nnext\n"


async def test_power_sensor_deadband(zensdk_device) -> None:  # noqa: ANN001
    sensor = zensdk_device.homeOutput
    sensor._attr_native_value = 100
    assert sensor.update_value(101) is False  # 1 W: not recorded
    assert sensor._attr_native_value == 100
    sensor._attr_native_value = 0
    sensor._attr_native_value = 100


async def test_timezone_offset_follows_home_assistant(zensdk_device, hass) -> None:  # noqa: ANN001
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    offset = zensdk_device.timezone_offset()
    assert offset in ("GMT+01:00", "GMT+02:00")
    assert timedelta  # keep the import used


async def test_device_settings_are_config_entities_when_created_while_running(zensdk_device, monkeypatch) -> None:  # noqa: ANN001
    """Home Assistant adds an entity eagerly: the category must be known when the constructor adds it."""
    from homeassistant.const import EntityCategory

    from custom_components.zendure_ha.select import ZendureSelect
    from custom_components.zendure_ha.switch import ZendureSwitch

    seen: dict[str, Any] = {}

    def record(entities: list[Any]) -> None:
        for entity in entities:
            seen[entity.propertyName] = entity.entity_category

    monkeypatch.setattr(ZendureSwitch, "add", staticmethod(record))
    monkeypatch.setattr(ZendureSelect, "add", staticmethod(record))
    zensdk_device.entityUpdate("Fanmode", 1)
    zensdk_device.entityUpdate("lampSwitch", 0)
    zensdk_device.entityUpdate("gridReverse", 1)
    zensdk_device.entityUpdate("buzzerSwitch", 0)
    assert seen["Fanmode"] is EntityCategory.CONFIG
    assert seen["lampSwitch"] is EntityCategory.CONFIG
    assert seen["gridReverse"] is EntityCategory.CONFIG
    assert seen["buzzerSwitch"] is None
