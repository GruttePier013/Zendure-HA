"""Buffered writer for the simulation log (simulation.csv).

Writing one line per P1 update directly to the file is hard on the SD card / SSD of the Home
Assistant host. Lines are collected in memory, written in one go from the executor and the file
is rotated when it grows too large.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024  # the file is rotated (one backup is kept) above this size
FLUSH_INTERVAL = 60.0  # seconds
FLUSH_LINES = 500


def _write(path: Path, header: str, lines: list[str]) -> None:
    if path.exists() and path.stat().st_size > MAX_BYTES:
        os.replace(path, path.with_name(path.name + ".1"))
    new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if new and header:
            f.write(header)
        f.write("".join(lines))


class SimulationLog:
    """Collect lines and write them in batches."""

    def __init__(self, hass: HomeAssistant, path: str | None = None) -> None:
        self._hass = hass
        self._path = Path(path or hass.config.path("simulation.csv"))
        self._lines: list[str] = []
        self._header = ""
        self._handle: asyncio.TimerHandle | None = None

    def add(self, line: str, header: str = "") -> None:
        """Queue a line; the header is only written when the file is created."""
        self._header = header or self._header
        self._lines.append(line)
        if len(self._lines) >= FLUSH_LINES:
            self._hass.async_create_task(self.flush())
        elif self._handle is None:
            self._handle = self._hass.loop.call_later(FLUSH_INTERVAL, lambda: self._hass.async_create_task(self.flush()))

    async def flush(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        if not self._lines:
            return
        lines, self._lines = self._lines, []
        try:
            await self._hass.async_add_executor_job(_write, self._path, self._header, lines)
        except OSError as err:
            _LOGGER.error("Unable to write the simulation log %s: %s", self._path, err)

    @property
    def pending(self) -> int:
        return len(self._lines)
