"""Tests for the options menu (overview + one step per group of settings)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zendure_ha.api import ApiError
from custom_components.zendure_ha.const import (
    CONF_APPTOKEN,
    CONF_FLIP_DELAY,
    CONF_MQTTLOCAL,
    CONF_MQTTLOG,
    CONF_MQTTSERVER,
    CONF_P1METER,
    CONF_SIM,
    CONF_SLEW_DOWN,
    CONF_SLEW_ENABLED,
    CONF_SLEW_UP,
    CONF_TIMEFAST,
    CONF_TIMEZERO,
    DOMAIN,
)


@pytest.fixture
def entry(hass, enable_custom_integrations):  # noqa: ANN001
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_APPTOKEN: "tok", CONF_P1METER: "sensor.p1", CONF_MQTTLOG: False, CONF_MQTTLOCAL: False})
    entry.add_to_hass(hass)
    # the options flow only needs the flow handler; do not set up the manifest dependencies (recorder, mqtt, ...)
    with patch("homeassistant.config_entries.async_process_deps_reqs", AsyncMock()):
        yield entry


async def _menu(hass, entry):  # noqa: ANN001
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return result


async def test_menu_lists_all_groups(hass, entry) -> None:  # noqa: ANN001
    result = await _menu(hass, entry)
    assert result["menu_options"] == ["connection", "mqtt", "p1_meter", "power_control", "slew", "lifetime", "device_ips", "diagnostics", "finish"]
    assert result["description_placeholders"]["p1meter"] == "sensor.p1"
    assert result["description_placeholders"]["slew"].startswith("600")


async def test_slew_step_stores_values_and_returns_to_menu(hass, entry) -> None:  # noqa: ANN001
    result = await _menu(hass, entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "slew"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "slew"
    values = {CONF_SLEW_ENABLED: True, CONF_SLEW_UP: 300, CONF_SLEW_DOWN: 900, CONF_FLIP_DELAY: 8, "min_run": 12, "ready_rate": 1500}
    result = await hass.config_entries.options.async_configure(result["flow_id"], values)
    assert result["type"] is FlowResultType.MENU
    assert entry.data[CONF_SLEW_UP] == 300 and entry.data[CONF_FLIP_DELAY] == 8
    assert entry.data[CONF_APPTOKEN] == "tok"  # untouched


async def test_power_control_rejects_fast_longer_than_normal(hass, entry) -> None:  # noqa: ANN001
    result = await _menu(hass, entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "power_control"})
    values = {CONF_TIMEZERO: 4, CONF_TIMEFAST: 9, "p1_stddev_min": 15, "power_start": 50, "power_tolerance": 5, "manual_interval": 15, "peak_limit": 0, "export_bias": 100}
    result = await hass.config_entries.options.async_configure(result["flow_id"], values)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_TIMEFAST: "fast_longer_than_normal"}


async def test_connection_step_validates_token(hass, entry) -> None:  # noqa: ANN001
    result = await _menu(hass, entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "connection"})
    with patch("custom_components.zendure_ha.config_flow.Api.Connect", AsyncMock(side_effect=ApiError("malformed_token"))):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_APPTOKEN: "bad"})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "malformed_token"}
    assert entry.data[CONF_APPTOKEN] == "tok"


async def test_mqtt_step_needs_server_when_local(hass, entry) -> None:  # noqa: ANN001
    result = await _menu(hass, entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "mqtt"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_MQTTLOCAL: True})
    assert result["errors"] == {CONF_MQTTSERVER: "mqtt_server_required"}


async def test_finish_reloads_only_after_connection_change(hass, entry) -> None:  # noqa: ANN001
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await _menu(hass, entry)
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "diagnostics"})
        result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_MQTTLOG: True, CONF_SIM: False})
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "finish"})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        reload.assert_not_called()
    assert entry.data[CONF_MQTTLOG] is True

    with patch.object(hass.config_entries, "async_schedule_reload") as reload, patch("custom_components.zendure_ha.config_flow.Api.Connect", AsyncMock(return_value={})):
        result = await _menu(hass, entry)
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "connection"})
        result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_APPTOKEN: "new"})
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "finish"})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        reload.assert_called_once_with(entry.entry_id)


async def test_lifetime_step_stores_values(hass, entry) -> None:  # noqa: ANN001
    result = await _menu(hass, entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "lifetime"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"role_margin": 15, "fill_level": 80, "flash_safe_state": False})
    assert result["type"] is FlowResultType.MENU
    assert entry.data["role_margin"] == 15 and entry.data["fill_level"] == 80 and entry.data["flash_safe_state"] is False


async def test_device_ips_validates_and_stores_addresses(hass, entry) -> None:  # noqa: ANN001
    entry.runtime_data = None
    hass.config_entries.async_update_entry(entry, data={**entry.data, "device_ips": {"SN1": "192.168.2.50"}})
    result = await _menu(hass, entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "device_ips"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "device_ips"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"SN1": "not an address!"})
    assert result["errors"] == {"SN1": "invalid_address"}

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"SN1": "zendure.lan"})
    assert result["type"] is FlowResultType.MENU
    assert entry.data["device_ips"] == {"SN1": "zendure.lan"}

    # clearing the field removes the override
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "device_ips"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"SN1": ""})
    assert entry.data["device_ips"] == {}


def test_settings_are_applied_without_an_update_listener() -> None:
    """HA 2026.12: no update listener; the flow applies non-reload settings directly."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import custom_components.zendure_ha as integration

    assert not hasattr(integration, "update_listener")
    manager = MagicMock()
    entry = SimpleNamespace(entry_id="x", data={"slew_up": 300}, runtime_data=manager)
    integration.apply_entry_settings(entry)
    manager.apply_settings.assert_called_once_with({"slew_up": 300})


async def test_p1_meter_direct_reading_asks_for_a_token(hass, entry) -> None:  # noqa: ANN001
    result = await _menu(hass, entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "p1_meter"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_P1METER: "sensor.p1", "p1_homewizard": " 192.168.1.30 "})
    assert result["step_id"] == "p1_token"
    assert result["description_placeholders"] == {"host": "192.168.1.30"}

    with patch("custom_components.zendure_ha.p1direct.request_token", AsyncMock(return_value=(None, "button"))):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"use_v1": False})
    assert result["errors"] == {"base": "homewizard_press_button"}

    with patch("custom_components.zendure_ha.p1direct.request_token", AsyncMock(return_value=("A" * 32, ""))):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"use_v1": False})
    assert result["type"] is FlowResultType.MENU
    assert entry.data["p1_homewizard"] == "192.168.1.30"
    assert entry.data["p1_homewizard_token"] == "A" * 32


async def test_p1_meter_api_v1_and_switch_off(hass, entry) -> None:  # noqa: ANN001
    result = await _menu(hass, entry)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "p1_meter"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_P1METER: "sensor.p1", "p1_homewizard": "192.168.1.30"})
    with patch("custom_components.zendure_ha.p1direct.read_once", AsyncMock(return_value=350)):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"use_v1": True})
    assert result["type"] is FlowResultType.MENU
    assert entry.data["p1_homewizard_token"] == ""

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "p1_meter"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_P1METER: "sensor.p1"})
    assert entry.data["p1_homewizard"] == ""


async def test_overview_in_the_language_of_home_assistant(hass, entry) -> None:  # noqa: ANN001
    hass.config.language = "nl"
    hass.config_entries.async_update_entry(entry, data={**entry.data, "fill_level": 70.0, "p1_homewizard": "192.168.1.151", "p1_homewizard_token": ""})
    result = await _menu(hass, entry)
    placeholders = result["description_placeholders"]
    assert placeholders["lifetime"].startswith("spreiddrempel 70%")
    assert placeholders["p1meter"] == "sensor.p1 + HomeWizard 192.168.1.151 (API v1, niet actief)"
    assert placeholders["logging"] == "uit"
