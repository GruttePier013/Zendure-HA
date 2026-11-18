"""Fusegroup for Zendure devices."""

from __future__ import annotations

import logging

from .device import ZendureDevice

_LOGGER = logging.getLogger(__name__)


class FuseGroup:
    """Zendure Fuse Group."""

    def __init__(self, name: str, maxpower: int, minpower: int, devices: list[ZendureDevice] | None = None) -> None:
        """Initialize the fuse group."""
        self.name: str = name
        self.maxpower = maxpower
        self.minpower = minpower
        # separate flags: a group with a charging and a discharging device in one cycle needs both limits
        self.initCharge = True
        self.initDischarge = True
        self.devices: list[ZendureDevice] = devices if devices is not None else []
        for d in self.devices:
            d.fuseGrp = self

    @property
    def initPower(self) -> bool:
        return self.initCharge or self.initDischarge

    @initPower.setter
    def initPower(self, value: bool) -> None:
        """Mark the limits as outdated (True) at the start of an update cycle."""
        self.initCharge = self.initDischarge = value

    def charge_limit(self, d: ZendureDevice) -> int:
        """Return the limit charge power for a device."""
        if self.initCharge:
            self.initCharge = False
            if len(self.devices) == 1:
                d.pwr_max = max(self.minpower, _limit(d, False))
            else:
                limit = 0
                weight = 0
                for fd in self.devices:
                    if fd.homeInput.asInt > 0:
                        limit += _limit(fd, False)
                        weight += (100 - fd.electricLevel.asInt) * _limit(fd, False)
                avail = max(self.minpower, limit)
                for fd in self.devices:
                    if fd.homeInput.asInt > 0:
                        fd.pwr_max = int(avail * ((100 - fd.electricLevel.asInt) * _limit(fd, False)) / weight) if weight < 0 else fd.charge_start
                        limit -= _limit(fd, False)
                        if limit > avail - fd.pwr_max:
                            fd.pwr_max = max(avail - limit, avail)
                        fd.pwr_max = max(fd.pwr_max, _limit(fd, False))
                        avail -= fd.pwr_max

        return d.pwr_max

    def discharge_limit(self, d: ZendureDevice) -> int:
        """Return the limit discharge power for a device."""
        if self.initDischarge:
            self.initDischarge = False
            if len(self.devices) == 1:
                d.pwr_max = min(self.maxpower, _limit(d, True))
            else:
                limit = 0
                weight = 0
                for fd in self.devices:
                    if fd.homeOutput.asInt > 0:
                        limit += _limit(fd, True)
                        weight += fd.electricLevel.asInt * _limit(fd, True)
                avail = min(self.maxpower, limit)
                for fd in self.devices:
                    if fd.homeOutput.asInt > 0:
                        fd.pwr_max = int(avail * (fd.electricLevel.asInt * _limit(fd, True)) / weight) if weight > 0 else fd.discharge_start
                        limit -= _limit(fd, True)
                        if limit < avail - fd.pwr_max:
                            fd.pwr_max = min(avail - limit, avail)
                        fd.pwr_max = min(fd.pwr_max, _limit(fd, True))
                        avail -= fd.pwr_max

        return d.pwr_max


def _limit(d: ZendureDevice, discharge: bool) -> int:
    """The limit of a device within its BMS cap (1.6.9); a device without the cap logic uses its plain limit."""
    capped = getattr(d, "capped_limit", None)
    if capped is not None:
        return capped(discharge)
    return d.discharge_limit if discharge else d.charge_limit
