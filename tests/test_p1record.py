"""P1 recording until midnight and the renamed operation modes."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.zendure_ha.const import CONF_APPTOKEN, CONF_MQTTLOCAL, CONF_MQTTLOG, CONF_P1_RECORD, CONF_P1METER, CONF_SIM, DOMAIN
from custom_components.zendure_ha.manager import ZendureManager
from custom_components.zendure_ha.p1record import HEADER, P1Recorder, record_path


async def test_recorder_writes_a_file_per_day_and_stops_at_midnight(hass, tmp_path) -> None:  # noqa: ANN001
    hass.config.config_dir = str(tmp_path)
    done: list[bool] = []
    recorder = P1Recorder(hass, lambda: done.append(True))
    recorder.start()
    recorder.add(-650, 0, "homewizard")
    recorder.add(120, 300, "homewizard")
    await recorder.flush()

    path = record_path(hass, dt_util.now().date())
    assert path == tmp_path / "zendure_ha" / f"p1_{dt_util.now().date().isoformat()}.csv"
    lines = path.read_text().splitlines()
    assert lines[0] + "\n" == HEADER
    assert lines[1].endswith(";-650;0;homewizard")
    assert lines[2].endswith(";120;300;homewizard")

    async_fire_time_changed(hass, dt_util.start_of_local_day() + timedelta(days=1, seconds=1))
    await hass.async_block_till_done()
    assert done == [True]


def _manager(hass: Any, entry: MockConfigEntry) -> ZendureManager:
    manager = object.__new__(ZendureManager)
    manager.hass = hass
    manager.config_entry = entry
    manager.devices = []
    return manager


async def test_recording_runs_on_the_stored_day_only(hass, tmp_path) -> None:  # noqa: ANN001
    hass.config.config_dir = str(tmp_path)
    today = dt_util.now().date().isoformat()
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_P1_RECORD: today})
    entry.add_to_hass(hass)
    manager = _manager(hass, entry)

    manager._update_p1_record(today)
    assert manager.p1record is not None
    manager.p1record.add(300, 0, "homewizard")
    assert manager.p1record.lines == 1

    await manager.p1record.stop()  # midnight: the recorder writes what is left (as in P1Recorder._midnight) ...
    manager._p1_record_done()  # ... and tells the manager
    assert manager.p1record is None
    assert entry.data[CONF_P1_RECORD] == ""


async def test_recording_of_a_past_day_is_switched_off(hass) -> None:  # noqa: ANN001
    yesterday = (dt_util.now().date() - timedelta(days=1)).isoformat()
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_P1_RECORD: yesterday})
    entry.add_to_hass(hass)
    manager = _manager(hass, entry)
    manager._update_p1_record(yesterday)  # Home Assistant was not running at midnight
    assert manager.p1record is None
    assert entry.data[CONF_P1_RECORD] == ""


async def test_options_switch_the_recording_on_for_today(hass, enable_custom_integrations) -> None:  # noqa: ANN001
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_APPTOKEN: "tok", CONF_P1METER: "sensor.p1", CONF_MQTTLOG: False, CONF_MQTTLOCAL: False})
    entry.add_to_hass(hass)
    with patch("homeassistant.config_entries.async_process_deps_reqs", AsyncMock()):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "diagnostics"})
        result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_MQTTLOG: False, CONF_SIM: False, CONF_P1_RECORD: True})
    assert result["type"] is FlowResultType.MENU
    assert entry.data[CONF_P1_RECORD] == dt_util.now().date().isoformat()
    assert "p1_" in result["description_placeholders"]["simulation"]


async def test_old_operation_mode_is_restored_under_its_new_name(hass, monkeypatch) -> None:  # noqa: ANN001
    from custom_components.zendure_ha.select import ZendureRestoreSelect

    device = SimpleNamespace(name="Zendure Manager", entities={}, checkEntity={}, attr_device_info={})
    changed: list[str] = []
    select = ZendureRestoreSelect(
        device,  # type: ignore[arg-type]
        "Operation",
        {0: "off", 1: "manual", 2: "smart", 3: "discharge", 4: "charge", 5: "store_solar"},
        lambda _s, option: changed.append(option),
        renamed={"smart_discharging": "discharge", "smart_charging": "charge"},
    )
    select.hass = hass
    monkeypatch.setattr(ZendureRestoreSelect, "async_get_last_state", AsyncMock(return_value=SimpleNamespace(state="smart_charging")))
    with patch("homeassistant.components.select.SelectEntity.async_added_to_hass", AsyncMock()):
        await select.async_added_to_hass()
    assert select.current_option == "charge"
    assert select.value == 4
    assert changed == ["charge"]
