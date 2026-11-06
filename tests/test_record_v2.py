"""1.5.7: recording with the control state, events and the stale control watchdog."""

from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zendure_ha import events
from custom_components.zendure_ha.const import DOMAIN, DeviceState, ManagerMode, SmartMode
from custom_components.zendure_ha.manager import ZendureManager
from custom_components.zendure_ha.p1record import (
    DEVICE_COLUMNS,
    HEADER,
    MANAGER_COLUMNS,
    P1Recorder,
    header,
    tidy_folder,
)


@pytest.fixture(autouse=True)
def _clean_events() -> None:
    events.reset()


# ----------------------------------------------------------------------------------------------
# recording
# ----------------------------------------------------------------------------------------------
def test_header_keeps_the_old_columns_first() -> None:
    assert header(3, False) == HEADER
    columns = header(3, True).strip().split(";")
    assert columns[:5] == ["time", "unix", "p1_w", "battery_w", "source"]
    assert columns[5 : 5 + len(MANAGER_COLUMNS)] == MANAGER_COLUMNS
    assert columns[-5:] == [f"d3_{c}" for c in DEVICE_COLUMNS]
    assert len(columns) == 5 + len(MANAGER_COLUMNS) + 3 * len(DEVICE_COLUMNS)


async def test_recording_with_control_state_and_events_in_batches(hass, tmp_path) -> None:  # noqa: ANN001
    hass.config.config_dir = str(tmp_path)
    recorder = P1Recorder(hass, lambda: None, devices=1, detail=True)
    recorder.start()
    row = ["smart", -300, -280, 120, "ws_v2", 0, 4, 0, 10, 0, 35, 0, 0, "L1", "L2", -50, 55, "charging", -300, -300, 0]
    recorder.add(-20, -300, "homewizard", row)
    events.emit("dir_start", "L1", **{"from": "discharge", "to": "charge"}, reason="p1")
    assert not recorder.path.exists()  # written in batches, not per line
    await recorder.flush()

    lines = recorder.path.read_text().splitlines()
    assert lines[0] + "\n" == header(1, True)
    values = lines[1].split(";")
    assert len(values) == len(lines[0].split(";"))
    assert values[2:5] == ["-20", "-300", "homewizard"] and values[5] == "smart" and values[-4] == "charging"
    event_lines = (tmp_path / "zendure_ha" / f"events_{dt_util.now().date().isoformat()}.csv").read_text().splitlines()
    assert event_lines[0] == "time;unix;type;device;detail"
    assert event_lines[1].split(";")[2:] == ["dir_start", "L1", "from=discharge to=charge reason=p1"]
    await recorder.stop()


async def test_other_format_continues_in_a_new_file(hass, tmp_path) -> None:  # noqa: ANN001
    hass.config.config_dir = str(tmp_path)
    old = P1Recorder(hass, lambda: None)  # 1.5.5 format
    old.add(100, 0, "sensor")
    await old.flush()
    new = P1Recorder(hass, lambda: None, devices=1, detail=True)
    new.add(100, 0, "sensor", [""] * (len(MANAGER_COLUMNS) + len(DEVICE_COLUMNS)))
    await new.flush()
    assert old.path.read_text().startswith(HEADER)
    assert new.path.name.endswith("_2.csv")


def test_tidy_compresses_old_days_and_keeps_the_folder_small(tmp_path) -> None:  # noqa: ANN001
    today = datetime(2026, 11, 2).date()
    for i, name in enumerate(["p1_2026-10-30.csv", "events_2026-10-31.csv", "p1_2026-11-01.csv", "p1_2026-11-02.csv"]):
        path = tmp_path / name
        path.write_text("x" * 5000)
        os.utime(path, (1000 + i, 1000 + i))
    tidy_folder(tmp_path, today)
    assert (tmp_path / "p1_2026-10-30.csv.gz").exists() and not (tmp_path / "p1_2026-10-30.csv").exists()
    assert gzip.open(tmp_path / "p1_2026-11-01.csv.gz", "rt").read() == "x" * 5000
    assert (tmp_path / "p1_2026-11-02.csv").exists()  # today stays as it is
    tidy_folder(tmp_path, today, max_bytes=5000)
    assert [f.name for f in tmp_path.iterdir()] == ["p1_2026-11-02.csv"]  # oldest first, today is kept


def _device(name: str, soc: int = 50, state: DeviceState = DeviceState.INACTIVE, current: int = 0, pending: bool = False) -> Any:
    level = SimpleNamespace(asInt=soc, asNumber=soc)
    zero = SimpleNamespace(asInt=0)
    slew = SimpleNamespace(current=current, pending=pending, flip_released=lambda: True, _run_start=None)
    return SimpleNamespace(
        deviceId=name, name=name, model="SolarFlow 2400 AC+", electricLevel=level, state=state, homeOutput=zero, homeInput=zero,
        slew=slew, charge_limit=-2400, discharge_limit=2200, kWh=2.88, command_time=float("-inf"),
    )  # fmt: skip


def _manager(hass: Any, devices: list[Any], data: dict[str, Any] | None = None) -> ZendureManager:
    m = object.__new__(ZendureManager)
    m.hass = hass
    m.devices = devices
    m.charge, m.discharge, m.idle = [], [], list(devices)
    m.operation = ManagerMode.MATCHING
    m.zero_fast = datetime.min
    m.cloud_reserve = None
    entry = MockConfigEntry(domain=DOMAIN, data=data or {})
    entry.add_to_hass(hass)
    m.config_entry = entry
    return m


async def test_meta_has_settings_but_no_secrets(hass) -> None:  # noqa: ANN001
    data = {"token": "SECRET", "mqttpsw": "pw", "mqttuser": "u", "wifissid": "net", "wifipsw": "x", "p1_homewizard_token": "T",
            "device_ips": {"HEC4NENCN521110": "192.168.1.203"}, "fill_level": 70.0, "slew_up": 1000.0}  # fmt: skip
    m = _manager(hass, [_device("L1"), _device("L2")], data)
    m.fuseGroups = []
    meta = m._record_meta()
    text = json.dumps(meta)
    for secret in ("SECRET", "pw", "HEC4NENCN521110", "192.168.1.203", '"T"', "net"):
        assert secret not in text
    assert meta["settings"] == {"fill_level": 70.0, "slew_up": 1000.0}
    assert [d["column"] for d in meta["devices"]] == ["d1", "d2"]
    assert meta["integration_version"] == "?"  # set at setup from the integration loader


async def test_meta_reads_no_file_in_the_event_loop(hass, monkeypatch) -> None:  # noqa: ANN001
    """3 November: manifest.json was read in the event loop (Home Assistant reported a blocking call)."""
    from pathlib import Path

    def blocked(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("file read in the event loop")

    monkeypatch.setattr(Path, "read_text", blocked)
    monkeypatch.setattr(Path, "open", blocked)
    m = _manager(hass, [_device("L1")], {})
    m.fuseGroups = []
    m.integration_version = "1.6.4"
    assert m._record_meta()["integration_version"] == "1.6.4"


async def test_setup_takes_the_version_from_the_loader(hass, enable_custom_integrations) -> None:  # noqa: ANN001
    from homeassistant.loader import async_get_integration

    integration = await async_get_integration(hass, DOMAIN)
    assert str(integration.version) not in ("", "None")


# ----------------------------------------------------------------------------------------------
# changes of direction
# ----------------------------------------------------------------------------------------------
class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clocked(zensdk_device, monkeypatch):  # noqa: ANN001, ANN201
    clock = _Clock()
    monkeypatch.setattr("custom_components.zendure_ha.device.monotonic", clock)
    zensdk_device.slew._clock = clock
    zensdk_device.slew.min_run = 0
    zensdk_device.electricLevel = SimpleNamespace(asInt=50, asNumber=50)
    zensdk_device.charge_limit, zensdk_device.discharge_limit = -2400, 2200
    return zensdk_device, clock


async def test_change_of_direction_is_logged_with_its_duration(clocked) -> None:  # noqa: ANN001
    device, clock = clocked
    seen: dict[str, str] = {}
    events.set_writer(lambda event, _device, detail: seen.__setitem__(event, detail))
    await device.power_discharge(500)
    clock.t += 1
    await device.power_charge(-400)  # through 0 W: the change of direction starts
    assert events.counters["dir_start"] == 1
    assert "from=discharge to=charge" in seen["dir_start"]

    clock.t += 2.5
    device.slew.measure(0)  # a fresh report confirms 0 W
    await device.power_charge(-400)
    assert events.counters["dir_end"] == 1
    assert "confirmed=measurement" in seen["dir_end"] and "duration=2.5" in seen["dir_end"]
    assert device.last_dir_change_s == 2.5


async def test_a_slow_change_of_direction_warns_once(clocked, caplog) -> None:  # noqa: ANN001
    device, clock = clocked
    await device.power_discharge(500)
    clock.t += 1
    await device.power_charge(-400)
    device.slew.current = 200  # the ramp down does not get through (e.g. commands fail)
    clock.t += 16
    device._check_events(300)
    device._check_events(300)
    assert events.counters["dir_slow"] == 1
    assert "change of direction from discharge to charge takes 16 s" in caplog.text
    assert "ramping down from 200 W" in caplog.text


async def test_a_released_change_of_direction_without_demand_ends_quietly(clocked, caplog) -> None:  # noqa: ANN001
    """Seen on 2 November: released, but no charging was needed any more; it stayed open for 58 minutes."""
    device, clock = clocked
    seen: dict[str, str] = {}
    events.set_writer(lambda event, _device, detail: seen.__setitem__(event, detail))
    await device.power_discharge(500)
    clock.t += 1
    await device.power_charge(-400)
    clock.t += 3
    device.slew.measure(0)  # 0 W confirmed: released
    device._check_events(0)
    clock.t += 6
    device._check_events(0)
    assert events.counters["dir_end"] == 1 and "confirmed=not_needed" in seen["dir_end"]
    clock.t += 20
    device._check_events(0)
    assert events.counters["dir_slow"] == 0 and "takes" not in caplog.text


async def test_a_cancelled_change_of_direction(clocked) -> None:  # noqa: ANN001
    device, clock = clocked
    seen: dict[str, str] = {}
    events.set_writer(lambda event, _device, detail: seen.__setitem__(event, detail))
    await device.power_discharge(500)
    clock.t += 1
    await device.power_charge(-400)
    clock.t += 1
    await device.power_discharge(300)  # back to the old direction before it was done
    assert events.counters["dir_end"] == 1 and "confirmed=cancelled" in seen["dir_end"]


async def test_abrupt_stop_with_unchanged_command(clocked) -> None:  # noqa: ANN001
    device, clock = clocked
    await device.power_discharge(1800)
    device._check_events(1822)
    clock.t += 1
    device._check_events(0)  # the command is still 1800 W
    assert events.counters["stop_abrupt"] == 1
    assert "before=1822 after=0 command=1800 soc=50" in events.last["detail"]


# ----------------------------------------------------------------------------------------------
# watchdog
# ----------------------------------------------------------------------------------------------
async def _feed(m: ZendureManager, p1: int, seconds: int, monkeypatch: Any) -> list[str]:
    runs: list[str] = []

    async def forced(*_args: Any) -> None:
        runs.append("cycle")

    monkeypatch.setattr(m, "_run_power_update", forced)
    start = datetime.now()
    for s in range(seconds + 1):
        monkeypatch.setattr("custom_components.zendure_ha.manager.datetime", _FrozenDatetime(start + timedelta(seconds=s)))
        await m._watch_stale(p1)
    return runs


class _FrozenDatetime:
    """datetime with a fixed now() (min/max stay usable for the comparisons in the manager)."""

    min = datetime.min
    max = datetime.max

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


async def test_watchdog_logs_and_forces_a_cycle_once(hass, monkeypatch, caplog) -> None:  # noqa: ANN001
    m = _manager(hass, [_device("L1", current=-1571), _device("L2")])
    m.charge_time = datetime.max
    runs = await _feed(m, 2815, 15, monkeypatch)
    assert runs == ["cycle"]
    assert m.stale_total == 1
    assert "No control for 10.0 s at P1 2815 W" in caplog.text and "L1:" in caplog.text


async def test_watchdog_quiet_while_ramping_changing_direction_or_empty(hass, monkeypatch) -> None:  # noqa: ANN001
    ramping = _manager(hass, [_device("L1", pending=True)])
    assert await _feed(ramping, 2000, 15, monkeypatch) == []
    turning = _manager(hass, [_device("L1")])
    turning.devices[0].dir_change = {"start": 0}
    assert await _feed(turning, 2000, 15, monkeypatch) == []
    empty = _manager(hass, [_device("L1", state=DeviceState.SOCEMPTY), _device("L2", state=DeviceState.OFFLINE)])
    assert await _feed(empty, 2000, 15, monkeypatch) == []
    assert ramping.stale_total == turning.stale_total == empty.stale_total == 0


async def test_watchdog_explains_the_charge_delay(hass, monkeypatch) -> None:  # noqa: ANN001
    m = _manager(hass, [_device("L1")])
    m.charge_time = datetime.now() + timedelta(seconds=60)
    assert await _feed(m, -650, 15, monkeypatch) == []
    assert m.stale_total == 0
    assert events.counters["stale_control"] == 1 and "explained=charge_delay" in events.last["detail"]


async def test_watchdog_stops_counting_when_a_command_is_sent(hass, monkeypatch) -> None:  # noqa: ANN001
    m = _manager(hass, [_device("L1")])
    m.charge_time = datetime.max
    monkeypatch.setattr(SmartMode, "STALE_AFTER", 10.0)
    start = datetime.now()
    for s in range(15):
        monkeypatch.setattr("custom_components.zendure_ha.manager.datetime", _FrozenDatetime(start + timedelta(seconds=s)))
        m.devices[0].command_time = float("inf") if s == 5 else m.devices[0].command_time  # a command in between
        await m._watch_stale(1500)
    assert m.stale_total == 0


async def test_no_abrupt_stop_after_our_own_stop(clocked) -> None:  # noqa: ANN001
    """1.6.7: the power falls to 0 W after we commanded 0 W: no stop_abrupt (3 false events on 5 November)."""
    device, clock = clocked
    await device.power_discharge(0)
    device._check_events(900)
    clock.t += 1
    device._check_events(0)
    assert events.counters["stop_abrupt"] == 0
