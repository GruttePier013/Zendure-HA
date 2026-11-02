"""Record the P1 meter every second for one day, as test data for the simulation.

Switched on in *Configure → Logging & simulation*. Every P1 value (every second when the HomeWizard P1
meter is read directly, otherwise every update of the P1 sensor) is written to

    <config>/zendure_ha/p1_<YYYY-MM-DD>.csv

with the power of the batteries at that moment, so the net demand of the house without the batteries
(P1 + batteries) can be replayed later (`scripts/compare_smart_mode.py`, SCENARIO=file). The recording
switches itself off at midnight (local time). Lines are written in batches (every 60 s) to spare the
SD card / SSD of the host; a day at 1 s is about 3 MB.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

FOLDER = "zendure_ha"
HEADER = "time;unix;p1_w;battery_w;source\n"
FLUSH_INTERVAL = 60.0  # s
FLUSH_LINES = 600


def record_path(hass: HomeAssistant, day: date) -> Path:
    """The file of one day: <config>/zendure_ha/p1_<YYYY-MM-DD>.csv."""
    return Path(hass.config.path(FOLDER, f"p1_{day.isoformat()}.csv"))


def _append(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if new:
            f.write(HEADER)
        f.write("".join(lines))


class P1Recorder:
    """Collect P1 values of today and write them in batches; stops itself at midnight."""

    def __init__(self, hass: HomeAssistant, on_midnight: Callable[[], None]) -> None:
        self._hass = hass
        self._on_midnight = on_midnight
        self.day: date = dt_util.now().date()
        self.path = record_path(hass, self.day)
        self.lines = 0
        self._pending: list[str] = []
        self._flush_handle = None
        self._midnight_unsub: Callable[[], None] | None = None

    def start(self) -> None:
        midnight = dt_util.start_of_local_day() + timedelta(days=1)
        self._midnight_unsub = async_track_point_in_time(self._hass, self._midnight, midnight)
        _LOGGER.info("Recording P1 every second until midnight in %s", self.path)

    async def _midnight(self, _now: datetime) -> None:
        self._midnight_unsub = None
        await self.stop()
        _LOGGER.info("P1 recording of %s finished at midnight: %s lines in %s", self.day, self.lines, self.path)
        self._on_midnight()

    def add(self, p1: int, battery: int, source: str) -> None:
        now = dt_util.now()
        if now.date() != self.day:
            return  # after midnight, before the timer fired
        self._pending.append(f"{now.isoformat(timespec='milliseconds')};{now.timestamp():.3f};{p1};{battery};{source}\n")
        self.lines += 1
        if len(self._pending) >= FLUSH_LINES:
            self._hass.async_create_task(self.flush())
        elif self._flush_handle is None:
            self._flush_handle = self._hass.loop.call_later(FLUSH_INTERVAL, lambda: self._hass.async_create_task(self.flush()))

    async def flush(self) -> None:
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        if not self._pending:
            return
        lines, self._pending = self._pending, []
        try:
            await self._hass.async_add_executor_job(_append, self.path, lines)
        except OSError as err:
            _LOGGER.error("Cannot write the P1 recording %s: %s", self.path, err)

    async def stop(self) -> None:
        if self._midnight_unsub is not None:
            self._midnight_unsub()
            self._midnight_unsub = None
        await self.flush()
