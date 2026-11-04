"""mDNS discovery of zenSDK devices (service type _zendure._tcp).

mDNS does not cross VLANs and is not available everywhere, so this is only a convenience: when a
device is seen on the local network with another address than the one we use (DHCP change), the
address is updated.

A service whose name or TXT records do not contain the serial number is identified by asking the
address itself: the zenSDK report (GET /properties/report) contains the serial number ("sn").

When mDNS does not work at all, a device that no longer answers at its address is looked for in the
/24 network of that address (search_subnet): one GET /properties/report per address, at most once
per SEARCH_INTERVAL. Its MQTT reports usually still arrive in that case, so it is still on the network.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from collections.abc import Callable
from typing import Any

from aiohttp import ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

SERVICE_TYPE = "_zendure._tcp.local."
PROBE_TIMEOUT = ClientTimeout(total=2)
SEARCH_TIMEOUT = ClientTimeout(total=1.5)
SEARCH_PARALLEL = 32  # requests at the same time while searching a network
SEARCH_INTERVAL = 900.0  # s between two searches of the network


def report_serial(body: str) -> str | None:
    """The serial number in a zenSDK report, None when the answer is not a zenSDK report."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    sn = data.get("sn") if isinstance(data, dict) else None
    return str(sn) if sn else None


def subnet_hosts(address: str) -> list[str]:
    """The other addresses of the /24 network of an IPv4 address, nearest first ([] for a name)."""
    try:
        ip = ipaddress.IPv4Address(address)
    except ValueError:
        return []
    network = ipaddress.IPv4Network(f"{ip}/24", strict=False)
    hosts = [str(h) for h in network.hosts() if h != ip]
    return sorted(hosts, key=lambda h: abs(int(ipaddress.IPv4Address(h)) - int(ip)))


async def probe_serial(hass: HomeAssistant, address: str, request_timeout: ClientTimeout = PROBE_TIMEOUT) -> str | None:
    """Ask a zenSDK device at an address for its serial number."""
    try:
        session = async_get_clientsession(hass)
        async with session.get(f"http://{address}/properties/report", timeout=request_timeout) as response:
            if response.status != 200:
                return None
            return report_serial(await response.text())
    except Exception:  # noqa: BLE001 - not reachable or not a zenSDK device
        return None


async def search_subnet(hass: HomeAssistant, address: str, serials: set[str]) -> dict[str, str]:
    """Look for zenSDK devices in the /24 network of an address: serial -> address of the ones found."""
    found: dict[str, str] = {}
    wanted = {sn.upper() for sn in serials}
    semaphore = asyncio.Semaphore(SEARCH_PARALLEL)

    async def probe(host: str) -> None:
        if wanted <= set(found):
            return
        async with semaphore:
            if (sn := await probe_serial(hass, host, SEARCH_TIMEOUT)) is not None and sn.upper() in wanted:
                found[sn.upper()] = host

    await asyncio.gather(*(probe(h) for h in subnet_hosts(address)))
    return {sn: found[sn.upper()] for sn in serials if sn.upper() in found}


def match_device(service_name: str, serials: dict[str, str], properties: dict[Any, Any] | None = None) -> str | None:
    """Return the serial of the device a service belongs to.

    The serial is looked for in the TXT records first (the suffix of the service name is not always the serial,
    e.g. Zendure-solarFlow2400Pro-<id>), then in the service name.
    """
    texts = []
    for key, value in (properties or {}).items():
        for item in (key, value):
            if isinstance(item, bytes):
                item = item.decode("utf-8", "ignore")
            if item:
                texts.append(str(item).upper())
    for sn in serials:
        if any(sn.upper() in text for text in texts):
            return sn

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
        self._names: dict[str, str] = {}  # service name -> serial, learned by asking the device

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
        addresses = [a for a in info.parsed_addresses() if ":" not in a]  # IPv4 only
        if not addresses:
            return
        serials = self._serials()
        if (sn := match_device(name, serials, info.properties)) is None and (sn := self._names.get(name)) not in serials:
            # the name does not tell which device it is: ask the device itself
            if (sn := await probe_serial(self._hass, addresses[0])) is not None:
                sn = next((s for s in serials if s.upper() == sn.upper()), None)
            if sn is None:
                # tell the user what was found, so the address can be entered in Configure -> Device addresses
                _LOGGER.info("Found Zendure device %s at %s, but could not match it to a serial number", name.split(".")[0], addresses[0])
                return
            self._names[name] = sn
        self._on_address(sn, addresses[0])

    async def async_stop(self) -> None:
        if self._browser is not None:
            await self._browser.async_cancel()
            self._browser = None
