"""Record the P1 meter every second for one day, as test data for the simulation and to explain the control.

Switched on in *Configure → Logging, simulation and test data*. Every P1 value (every second when the
HomeWizard P1 meter is read directly, otherwise every update of the P1 sensor) is written to

    <config>/zendure_ha/p1_<YYYY-MM-DD>.csv          one line per P1 value
    <config>/zendure_ha/p1_<YYYY-MM-DD>.meta.json    versions, devices (d1, d2, ...) and settings
    <config>/zendure_ha/events_<YYYY-MM-DD>.csv      what the control did and why (events.py)

The first five columns are the same as in 1.5.5 (``time;unix;p1_w;battery_w;source``). With *control
state* switched on (default) the state of the manager and of every device follows (MANAGER_COLUMNS,
DEVICE_COLUMNS with prefix d1_, d2_, ...). The recording switches itself off at midnight (local time).

Lines are written in batches (every 60 s or 600 lines) from the executor, to spare the SD card / SSD of
the host. Files of earlier days are compressed (.csv.gz) and the folder is kept below MAX_FOLDER_BYTES
(oldest files first).
"""

from __future__ import annotations

import gzip
import json
import logging
import shutil
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

from . import events

_LOGGER = logging.getLogger(__name__)

FOLDER = "zendure_ha"
BASE_COLUMNS = ["time", "unix", "p1_w", "battery_w", "source"]
HEADER = ";".join(BASE_COLUMNS) + "\n"  # the 1.5.5 format
MANAGER_COLUMNS = [
    "mode",
    "setpoint_w",
    "net_w",
    "p1_age_ms",
    "p1_src",
    "cloud",
    "fast_track_n",
    "charge_delay_s",
    "cycle_n",
    "cycle_running",
    "cycle_ms",
    "cycle_fail_n",
    "cycle_stop_n",
    "role_dis",
    "role_chg",
    "target_w",
]
DEVICE_COLUMNS = ["soc", "state", "pwr_w", "cmd_w", "ramp"]
EVENTS_HEADER = "time;unix;type;device;detail\n"
FLUSH_INTERVAL = 60.0  # s
FLUSH_LINES = 600
MAX_FOLDER_BYTES = 200 * 1024 * 1024


def record_path(hass: HomeAssistant, day: date) -> Path:
    """The file of one day: <config>/zendure_ha/p1_<YYYY-MM-DD>.csv."""
    return Path(hass.config.path(FOLDER, f"p1_{day.isoformat()}.csv"))


def header(devices: int, detail: bool) -> str:
    """The header line: the 1.5.5 columns, then the control state (manager, then d1_..dn_ per device)."""
    if not detail:
        return HEADER
    columns = BASE_COLUMNS + MANAGER_COLUMNS + [f"d{i}_{c}" for i in range(1, devices + 1) for c in DEVICE_COLUMNS]
    return ";".join(columns) + "\n"


def _append(path: Path, head: str, lines: list[str]) -> Path:
    """Append lines; a file with another header (other format or devices) is continued in p1_<date>_2.csv etc."""
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate, n = path, 1
    while candidate.exists():
        with candidate.open(encoding="utf-8") as f:
            if f.readline() == head:
                break
        n += 1
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
    new = not candidate.exists()
    with candidate.open("a", encoding="utf-8") as f:
        if new:
            f.write(head)
        f.write("".join(lines))
    return candidate


def _write_meta(path: Path, meta: dict[str, Any]) -> None:
    """Add the settings to the meta file when they differ from the last entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"versions": []}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"versions": []}
    versions = data.setdefault("versions", [])
    if versions and {k: v for k, v in versions[-1].items() if k != "since"} == meta:
        return
    versions.append({"since": dt_util.now().isoformat(timespec="seconds"), **meta})
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def tidy_folder(folder: Path, today: date, max_bytes: int = MAX_FOLDER_BYTES) -> None:
    """Compress the files of earlier days and keep the folder below max_bytes (oldest first; today is kept)."""
    if not folder.is_dir():
        return
    stamp = today.isoformat()
    for file in folder.glob("*.csv"):
        if stamp in file.name:
            continue
        with file.open("rb") as src, gzip.open(file.with_name(file.name + ".gz"), "wb") as dst:
            shutil.copyfileobj(src, dst)
        file.unlink()
    files = sorted((f for f in folder.iterdir() if f.is_file()), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    for file in files:
        if total <= max_bytes:
            break
        if stamp in file.name:
            continue
        total -= file.stat().st_size
        file.unlink()


class _Batch:
    """Lines collected in memory and appended to a file in batches."""

    def __init__(self, hass: HomeAssistant, path: Path, head: str) -> None:
        self._hass = hass
        self.path = path
        self.head = head
        self.lines = 0
        self._pending: list[str] = []
        self._handle: Any = None

    def add(self, line: str) -> None:
        self._pending.append(line)
        self.lines += 1
        if len(self._pending) >= FLUSH_LINES:
            self._hass.async_create_task(self.flush())
        elif self._handle is None:
            self._handle = self._hass.loop.call_later(FLUSH_INTERVAL, lambda: self._hass.async_create_task(self.flush()))

    async def flush(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        if not self._pending:
            return
        lines, self._pending = self._pending, []
        try:
            self.path = await self._hass.async_add_executor_job(_append, self.path, self.head, lines)
        except OSError as err:
            _LOGGER.error("Cannot write %s: %s", self.path, err)


class P1Recorder:
    """Collect P1 values (and events) of today and write them in batches; stops itself at midnight."""

    def __init__(
        self,
        hass: HomeAssistant,
        on_midnight: Callable[[], None],
        devices: int = 0,
        detail: bool = False,
        meta: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._hass = hass
        self._on_midnight = on_midnight
        self.day: date = dt_util.now().date()
        self.detail = detail
        self.devices = devices
        self._meta = meta
        self._data = _Batch(hass, record_path(hass, self.day), header(devices, detail))
        self._events = _Batch(hass, Path(hass.config.path(FOLDER, f"events_{self.day.isoformat()}.csv")), EVENTS_HEADER)
        self._midnight_unsub: Callable[[], None] | None = None

    @property
    def path(self) -> Path:
        return self._data.path

    @property
    def lines(self) -> int:
        return self._data.lines

    @property
    def meta_path(self) -> Path:
        return Path(self._hass.config.path(FOLDER, f"p1_{self.day.isoformat()}.meta.json"))

    def start(self) -> None:
        midnight = dt_util.start_of_local_day() + timedelta(days=1)
        self._midnight_unsub = async_track_point_in_time(self._hass, self._midnight, midnight)
        events.set_writer(self.event)
        self._hass.async_create_task(self._prepare())
        _LOGGER.info("Recording P1 every second until midnight in %s (control state %s)", self.path, "on" if self.detail else "off")

    async def _prepare(self) -> None:
        try:
            await self._hass.async_add_executor_job(tidy_folder, self.path.parent, self.day)
        except OSError as err:
            _LOGGER.warning("Cannot tidy %s: %s", self.path.parent, err)
        await self.update_meta()

    async def update_meta(self) -> None:
        """Store the versions, devices and settings (when they changed)."""
        if self._meta is None or (meta := self._meta()) is None:
            return  # not known yet (the devices are still loading): written after the first control cycle
        try:
            await self._hass.async_add_executor_job(_write_meta, self.meta_path, meta)
        except (OSError, TypeError, ValueError) as err:
            _LOGGER.warning("Cannot write %s: %s", self.meta_path, err)

    async def _midnight(self, _now: datetime) -> None:
        self._midnight_unsub = None
        await self.stop()
        _LOGGER.info("P1 recording of %s finished at midnight: %s lines in %s", self.day, self.lines, self.path)
        self._on_midnight()

    def add(self, p1: int, battery: int, source: str, columns: list[Any] | None = None) -> None:
        now = dt_util.now()
        if now.date() != self.day:
            return  # after midnight, before the timer fired
        line = f"{now.isoformat(timespec='milliseconds')};{now.timestamp():.3f};{p1};{battery};{source}"
        if self.detail:
            values = columns or []
            if columns and (n := (len(columns) - len(MANAGER_COLUMNS)) // len(DEVICE_COLUMNS)) != self.devices:
                self.devices = n  # devices were added: continue in a file with the new header
                self._data.head = header(n, True)
            expected = len(MANAGER_COLUMNS) + len(DEVICE_COLUMNS) * self.devices
            values = (list(values) + [""] * expected)[:expected]
            line += ";" + ";".join("" if v is None else str(v).replace(";", ",") for v in values)
        self._data.add(line + "\n")

    def event(self, event: str, device: str, detail: str) -> None:
        now = dt_util.now()
        if now.date() == self.day:
            self._events.add(f"{now.isoformat(timespec='milliseconds')};{now.timestamp():.3f};{event};{device};{detail}\n")

    async def flush(self) -> None:
        await self._data.flush()
        await self._events.flush()

    async def stop(self) -> None:
        if self._midnight_unsub is not None:
            self._midnight_unsub()
            self._midnight_unsub = None
        events.set_writer(None)
        await self.flush()
