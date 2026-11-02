"""mDNS discovery of zenSDK devices (service type _zendure._tcp).

mDNS does not cross VLANs and is not available everywhere, so this is only a convenience: when a
device is seen on the local network with another address than the one we use (DHCP change), the
address is updated in memory. A manually configured address is never overruled, and nothing is
stored (no writes to the storage of the host).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

SERVICE_TYPE = "_zendure._tcp.local."


def match_device(service_name: str, serials: dict[str, str]) -> str | None:
    """Return the serial of the device a service name belongs to, e.g. Zendure-SolarFlow800-WOB1NHMAMXXXXX3._zendure._tcp.local."""
    segment = service_name.split(".")[0].split("-")[-1].upper()
    if len(segment) < 6:
        return None
    for sn in serials:
        if sn.upper().endswith(segment) or segment in sn.upper():
            return sn
    return None


class ZendureDiscovery:
    """Listen for zenSDK services and report the address of known devices."""

    def __init__(self, hass: HomeAssistant, on_address: Callable[[str, str], None], serials: Callable[[], dict[str, str]]) -> None:
        self._hass = hass
        self._on_address = on_address
        self._serials = serials
        self._browser: Any = None

    async def async_start(self) -> None:
        try:
            from homeassistant.components import zeroconf
            from zeroconf import ServiceStateChange
            from zeroconf.asyncio import AsyncServiceBrowser

            aiozc = await zeroconf.async_get_async_instance(self._hass)

            def _handler(zeroconf: Any, service_type: str, name: str, state_change: ServiceStateChange) -> None:
                if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
                    self._hass.async_create_task(self._resolve(aiozc, service_type, name))

            self._browser = AsyncServiceBrowser(aiozc.zeroconf, SERVICE_TYPE, handlers=[_handler])
        except Exception as err:  # discovery is optional
            _LOGGER.debug("mDNS discovery of zenSDK devices is not available: %s", err)

    async def _resolve(self, aiozc: Any, service_type: str, name: str) -> None:
        from zeroconf.asyncio import AsyncServiceInfo

        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(aiozc.zeroconf, 3000):
            return
        if (sn := match_device(name, self._serials())) is None:
            return
        for address in info.parsed_addresses():
            if ":" not in address:  # IPv4 only
                self._on_address(sn, address)
                return

    async def async_stop(self) -> None:
        if self._browser is not None:
            await self._browser.async_cancel()
            self._browser = None
