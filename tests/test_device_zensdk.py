"""Scenario tests for ZendureZenSdk.charge / discharge / power_off."""

from __future__ import annotations


from custom_components.zendure_ha.const import SmartMode


def _last_props(device) -> dict:  # noqa: ANN001
    return device.mqtt_commands[-1]["properties"]


async def test_discharge_first_call_asserts_full_command(zensdk_device) -> None:  # noqa: ANN001
    zensdk_device.acMode._attr_current_option = "output"  # already in output mode: one command
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


async def test_device_started_from_standby_gets_the_power_at_once(zensdk_device) -> None:  # noqa: ANN001
    """1.6.0: the inverter ramps by itself, so a standby device gets the full command at once."""
    zensdk_device.setLimits(-2400, 2400)
    zensdk_device.configure_slew(5, 10)
    await zensdk_device.power_discharge(2200)
    assert zensdk_device.slew.current == 2200
    assert not zensdk_device.slew.pending
    assert _last_props(zensdk_device)["outputLimit"] == 2200


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


async def test_prepare_sets_input_mode_for_the_charge_role(zensdk_device) -> None:  # noqa: ANN001
    """An idle device with the charge role still in output mode is switched to input, at 0 W, RAM only."""
    zensdk_device.connectionStatus._attr_native_value = 10  # online
    zensdk_device.acMode._attr_current_option = "output"
    assert await zensdk_device.prepare(-1) is True
    assert _last_props(zensdk_device) == {"smartMode": 1, "acMode": 1, "outputLimit": 0, "inputLimit": 0}
    sent = len(zensdk_device.mqtt_commands)
    assert await zensdk_device.prepare(-1) is False  # already in input mode: nothing sent again
    assert len(zensdk_device.mqtt_commands) == sent


async def test_prepare_waits_for_the_direction_change_pause(zensdk_device) -> None:  # noqa: ANN001
    zensdk_device.connectionStatus._attr_native_value = 10  # online
    zensdk_device.acMode._attr_current_option = "output"
    zensdk_device.slew.limit(500)
    zensdk_device.slew.limit(0, hold=False)  # just stopped discharging
    assert await zensdk_device.prepare(-1) is False


async def test_prepare_does_nothing_while_running(zensdk_device) -> None:  # noqa: ANN001
    zensdk_device.connectionStatus._attr_native_value = 10  # online
    zensdk_device.homeOutput._attr_native_value = 300
    assert await zensdk_device.prepare(-1) is False


def test_guessed_address_is_marked_and_a_manual_one_replaces_it(zensdk_device) -> None:  # noqa: ANN001
    assert zensdk_device.address_source == "guess"
    zensdk_device._http_failures = 4  # noqa: SLF001
    zensdk_device.set_address("192.168.1.51", "manual")
    assert zensdk_device.ipAddress == "192.168.1.51"
    assert zensdk_device.address_source == "manual"
    assert zensdk_device._http_failures == 0  # noqa: SLF001  fresh start at the new address


def _props(device) -> list[dict]:  # noqa: ANN001
    return [{k: v for k, v in c["properties"].items()} for c in device.mqtt_commands]


async def test_first_discharge_from_input_mode_at_zero_is_one_command(zensdk_device) -> None:  # noqa: ANN001
    """1.6.2: at 0 W (commanded and measured) the new mode and the limit go in one command."""
    zensdk_device.acMode._attr_current_option = "input"
    await zensdk_device.discharge(500)
    assert _props(zensdk_device) == [{"smartMode": 1, "acMode": 2, "outputLimit": 500, "inputLimit": 0}]


async def test_mode_switch_while_still_measuring_power_keeps_the_zero_step(zensdk_device) -> None:  # noqa: ANN001
    """The stop was sent but the device still reports power: switch the mode at 0 W first, as before."""
    zensdk_device.acMode._attr_current_option = "input"
    zensdk_device.homeInput._attr_native_value = 300
    await zensdk_device.discharge(500)
    assert _props(zensdk_device) == [
        {"smartMode": 1, "acMode": 2, "outputLimit": 0, "inputLimit": 0},
        {"smartMode": 1, "acMode": 2, "outputLimit": 500, "inputLimit": 0},
    ]


async def test_mode_switch_goes_limit_zero_then_mode_then_new_limit(zensdk_device) -> None:  # noqa: ANN001
    """Discharging 500 W, then charging 400 W: old limit to 0, switch mode at 0 W, then the new limit."""
    await zensdk_device.discharge(500)
    await zensdk_device.charge(-400)
    assert _props(zensdk_device)[-3:] == [
        {"smartMode": 1, "acMode": 2, "outputLimit": 0, "inputLimit": 0},
        {"smartMode": 1, "acMode": 1, "outputLimit": 0, "inputLimit": 0},
        {"smartMode": 1, "acMode": 1, "outputLimit": 0, "inputLimit": 400},
    ]


async def test_mode_switch_after_a_confirmed_stop_is_one_command(zensdk_device) -> None:  # noqa: ANN001
    await zensdk_device.discharge(500)
    await zensdk_device.discharge(0)  # the direction protection already brought it to 0 W (measured 0 W)
    n = len(zensdk_device.mqtt_commands)
    await zensdk_device.charge(-400)
    assert _props(zensdk_device)[n:] == [{"smartMode": 1, "acMode": 1, "outputLimit": 0, "inputLimit": 400}]


async def test_a_stop_reads_the_device_every_second_until_0_w(zensdk_device, monkeypatch) -> None:  # noqa: ANN001
    from custom_components.zendure_ha.const import Http

    monkeypatch.setattr(Http, "CONFIRM_FIRST", 0.01)
    monkeypatch.setattr(Http, "CONFIRM_INTERVAL", 0.01)
    zensdk_device.connection._attr_current_option = "zenSDK"
    zensdk_device.connection._attr_native_value = 2
    monkeypatch.setattr(type(zensdk_device.connection), "value", property(lambda _self: 2), raising=False)
    sent: list[object] = []

    async def post(_path, command):  # noqa: ANN001, ANN202
        sent.append(command)
        return True

    reads = []

    async def poll(force: bool = False) -> None:
        reads.append(force)
        if len(reads) == 2:
            zensdk_device.homeOutput._attr_native_value = 0  # the device shows 0 W at the second read

    monkeypatch.setattr(zensdk_device, "httpPost", post)
    monkeypatch.setattr(zensdk_device, "poll", poll)
    zensdk_device.homeOutput._attr_native_value = 900  # still discharging
    await zensdk_device.discharge(0)
    await zensdk_device._confirm_task
    assert reads == [True, True]  # read every second, stopped once 0 W was seen


async def test_no_extra_reads_over_mqtt(zensdk_device) -> None:  # noqa: ANN001
    zensdk_device.homeOutput._attr_native_value = 900
    await zensdk_device.discharge(0)
    assert getattr(zensdk_device, "_confirm_task", None) is None


async def test_same_mode_is_one_command(zensdk_device) -> None:  # noqa: ANN001
    await zensdk_device.charge(-400)
    n = len(zensdk_device.mqtt_commands)
    await zensdk_device.charge(-800)
    assert _props(zensdk_device)[n:] == [{"smartMode": 1, "acMode": 1, "outputLimit": 0, "inputLimit": 800}]


async def test_prepared_device_starts_with_one_command(zensdk_device) -> None:  # noqa: ANN001
    """A device that was prepared for its role (mode already switched at 0 W) starts without another switch."""
    zensdk_device.connectionStatus._attr_native_value = 10
    zensdk_device.acMode._attr_current_option = "output"
    await zensdk_device.prepare(-1)
    n = len(zensdk_device.mqtt_commands)
    await zensdk_device.charge(-600)
    assert _props(zensdk_device)[n:] == [{"smartMode": 1, "acMode": 1, "outputLimit": 0, "inputLimit": 600}]


def test_activity_and_role_sensors_are_diagnostic_enums(zensdk_device) -> None:  # noqa: ANN001
    from homeassistant.const import EntityCategory

    for sensor in (zensdk_device.activity, zensdk_device.role):
        assert sensor.entity_category is EntityCategory.DIAGNOSTIC
        assert sensor.device_class == "enum"
    assert "standby" in zensdk_device.activity.options
    assert zensdk_device.role.options == ["charge", "discharge", "none"]


def test_device_settings_are_in_the_configuration_section(zensdk_device) -> None:  # noqa: ANN001
    from homeassistant.const import EntityCategory

    from custom_components.zendure_ha.entity import CONFIG_KEYS

    assert zensdk_device.connection.entity_category is EntityCategory.CONFIG
    assert zensdk_device.fuseGroup.entity_category is EntityCategory.CONFIG
    assert {"gridReverse", "Fanspeed", "Fanmode", "lampSwitch"} <= CONFIG_KEYS


async def test_reports_are_read_in_the_background_not_in_the_cycle(zensdk_device, monkeypatch) -> None:  # noqa: ANN001
    """1.6.5: the control cycle no longer reads the report itself, so its command does not wait 0.5 s for the ESP32."""
    import asyncio

    monkeypatch.setattr(type(zensdk_device.connection), "value", property(lambda _self: 2), raising=False)
    reads: list[bool] = []

    async def poll(force: bool = False) -> None:
        reads.append(force)

    monkeypatch.setattr(zensdk_device, "poll", poll)
    await zensdk_device.power_get()
    assert zensdk_device._reader is not None and not zensdk_device._reader.done()
    n = len(reads)
    await zensdk_device.power_get()
    await asyncio.sleep(0)
    assert len(reads) <= n + 1  # the cycle did not read again itself; only the reader reads
    await asyncio.sleep(0.3)
    assert len(reads) >= 2  # the reader keeps reading on its own rhythm
    zensdk_device.cancel_timers()
    await asyncio.sleep(0)
    assert zensdk_device._reader is None


async def test_without_a_running_home_assistant_the_cycle_reads_itself(zensdk_device, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(type(zensdk_device.connection), "value", property(lambda _self: 2), raising=False)
    monkeypatch.setattr(zensdk_device.hass, "state", __import__("homeassistant.core", fromlist=["CoreState"]).CoreState.not_running)
    reads: list[bool] = []

    async def poll(force: bool = False) -> None:
        reads.append(force)

    monkeypatch.setattr(zensdk_device, "poll", poll)
    await zensdk_device.power_get()
    assert reads == [False] and getattr(zensdk_device, "_reader", None) is None
