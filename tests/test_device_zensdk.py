"""Scenario tests for ZendureZenSdk.charge / discharge / power_off."""

from __future__ import annotations

from custom_components.zendure_ha.const import SmartMode


def _last_props(device) -> dict:  # noqa: ANN001
    return device.mqtt_commands[-1]["properties"]


async def test_discharge_first_call_asserts_full_command(zensdk_device) -> None:  # noqa: ANN001
    await zensdk_device.discharge(500)

    assert len(zensdk_device.mqtt_commands) == 1
    assert _last_props(zensdk_device) == {"outputLimit": 500, "inputLimit": 0, "smartMode": 1, "acMode": 2}


async def test_charge_after_discharge_switches_mode(zensdk_device) -> None:  # noqa: ANN001
    """Switching direction sends acMode 1 and moves the power to inputLimit."""
    await zensdk_device.discharge(500)
    await zensdk_device.charge(-400)

    props = _last_props(zensdk_device)
    assert props == {"outputLimit": 0, "inputLimit": 400, "smartMode": 1, "acMode": 1}


async def test_discharge_zero_power_stays_in_ram(zensdk_device) -> None:  # noqa: ANN001
    """Stopping is a RAM write (smartMode 1): a stop must not wear out the flash of the device."""
    await zensdk_device.discharge(0)

    assert _last_props(zensdk_device)["smartMode"] == 1
    assert zensdk_device.flash_count == 0


async def test_discharge_kickstart_boosts_power_from_stalled_start(zensdk_device) -> None:  # noqa: ANN001
    """When starting exactly at POWER_START with headroom and no home load, discharge() kickstarts above the noise floor."""
    zensdk_device.limitOutput.update_value(200)
    assert zensdk_device.homeOutput.asInt == 0  # default, no home load

    power = await zensdk_device.discharge(SmartMode.POWER_START)

    assert power == min(200 + 4, 2 * SmartMode.POWER_START)
    assert _last_props(zensdk_device)["outputLimit"] == power


async def test_charge_kickstart_boosts_power_from_stalled_start(zensdk_device) -> None:  # noqa: ANN001
    zensdk_device.limitInput.update_value(200)
    assert zensdk_device.homeInput.asInt == 0  # default, no home load

    power = await zensdk_device.charge(-SmartMode.POWER_START)

    assert power == -min(200 + 4, 2 * SmartMode.POWER_START)
    assert _last_props(zensdk_device)["inputLimit"] == -power


async def test_power_off_sends_zero_limits(zensdk_device) -> None:  # noqa: ANN001
    zensdk_device.mqtt = object()  # connected
    await zensdk_device.power_off()

    # the first power off also stores the safe 0 W state in the flash (smartMode 0)
    assert _last_props(zensdk_device) == {"outputLimit": 0, "inputLimit": 0, "smartMode": 0, "acMode": 2}
    assert zensdk_device.flash_count == 1


async def test_power_off_writes_flash_only_once(zensdk_device) -> None:  # noqa: ANN001
    zensdk_device.mqtt = object()
    await zensdk_device.power_off()
    await zensdk_device.discharge(500)
    await zensdk_device.power_off()

    assert _last_props(zensdk_device)["smartMode"] == 1
    assert zensdk_device.flash_count == 1


async def test_switch_off_is_not_repeated(zensdk_device) -> None:  # noqa: ANN001
    zensdk_device.mqtt = object()
    await zensdk_device.switch_off()
    await zensdk_device.switch_off()

    assert len(zensdk_device.mqtt_commands) == 1


async def test_idle_device_stores_safe_state_once_after_a_long_idle_period(zensdk_device, monkeypatch) -> None:  # noqa: ANN001
    import custom_components.zendure_ha.device as dev
    from custom_components.zendure_ha.const import Flash

    zensdk_device.mqtt = object()
    zensdk_device.connectionStatus._attr_native_value = 10  # online
    clock = [1000.0]
    monkeypatch.setattr(dev, "monotonic", lambda: clock[0])

    await zensdk_device._flash_tick()  # starts the idle timer
    assert zensdk_device.mqtt_commands == []
    clock[0] += Flash.IDLE_PERSIST + 1
    await zensdk_device._flash_tick()
    assert _last_props(zensdk_device)["smartMode"] == 0
    clock[0] += Flash.MIN_INTERVAL * 5
    await zensdk_device._flash_tick()
    assert len(zensdk_device.mqtt_commands) == 1  # stored once, never again


async def test_device_started_from_standby_ramps_instead_of_jumping(zensdk_device) -> None:  # noqa: ANN001
    """The manager asks a standby device for 1100 W at once; the device itself only takes the first slew step."""
    zensdk_device.slew.enabled = True
    zensdk_device.slew.up = 600
    zensdk_device.setLimits(-2400, 2400)
    await zensdk_device.power_discharge(1100)
    assert zensdk_device.slew.current == 600
    assert zensdk_device.slew.pending  # the manager schedules the next step about 1 s later
    assert _last_props(zensdk_device)["outputLimit"] == 600


async def test_ready_device_starts_faster_from_standby(zensdk_device) -> None:  # noqa: ANN001
    zensdk_device.setLimits(-2400, 2400)
    zensdk_device.configure_slew(True, 600, 1200, 5, 10, 1200)
    await zensdk_device.power_discharge(2200, ready=True)
    assert _last_props(zensdk_device)["outputLimit"] == 1200


async def test_full_battery_clears_the_old_charge_limit(zensdk_device) -> None:  # noqa: ANN001
    """A full battery stops on its own; the old limit is cleared so it cannot jump back to it at once."""
    from custom_components.zendure_ha.const import DeviceState

    zensdk_device.setLimits(-2400, 2400)
    zensdk_device.slew.current, zensdk_device.slew._last_dir = -2400, -1  # noqa: SLF001
    zensdk_device.limitInput._attr_native_value = 2400
    zensdk_device.state = DeviceState.SOCFULL
    await zensdk_device._follow_battery_limit(0)  # noqa: SLF001
    assert zensdk_device.slew.current == 0
    assert _last_props(zensdk_device)["inputLimit"] == 0
    assert _last_props(zensdk_device)["smartMode"] == 1  # RAM, no flash write
