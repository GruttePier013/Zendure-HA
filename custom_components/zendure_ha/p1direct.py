"""Read the P1 meter directly from a HomeWizard P1 meter: a new value every second.

The HomeWizard integration of Home Assistant updates the power about every 5 seconds, while a DSMR 5
meter sends a telegram every second. The manager follows P1 much better with 1 s values (simulation
with changing clouds: 67 instead of 142 Wh import), so it can read the meter itself:

* API v2 (preferred): a WebSocket (wss://<address>/api/ws) on which the meter pushes every new
  measurement; no polling. It needs a token: the token of the HomeWizard integration of Home Assistant
  is used when that integration uses API v2, otherwise the options flow asks for one (press the button
  on the meter). The certificate of the meter is checked against the HomeWizard CA.
* API v1: GET http://<address>/api/v1/data every second (the "Local API" must be switched on in the
  HomeWizard app). HomeWizard advises not to poll faster than every 500 ms.

When the meter does not answer FAIL_AFTER times in a row the manager uses the Home Assistant sensor
again, until the meter answers again. When the HomeWizard integration knows a new address of the meter
(DHCP), that address is used.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from aiohttp import ClientTimeout, WSMsgType
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

INTERVAL = 1.0  # s between two readings (API v1)
TIMEOUT = ClientTimeout(total=3)
RECEIVE_TIMEOUT = 10.0  # s without a measurement before the WebSocket is opened again
FAIL_AFTER = 3  # failures in a row before the Home Assistant sensor is used again
RETRY = 30.0  # s between two attempts while the meter does not answer
RETRY_UNAUTHORIZED = 300.0  # s between two attempts while the token is refused
TOKEN_NAME = "local/zendure_ha"
V2_HEADERS = {"X-Api-Version": "2", "Content-Type": "application/json"}

# HomeWizard "Appliance Access CA" (valid until 2031-12-16), published in the API documentation
HOMEWIZARD_CA = """
-----BEGIN CERTIFICATE-----
MIIDITCCAgkCFDn7cwYLioTM3VxdAygLl/Px9ovFMA0GCSqGSIb3DQEBCwUAME0x
CzAJBgNVBAYTAk5MMQswCQYDVQQIDAJaSDETMBEGA1UECgwKSG9tZVdpemFyZDEc
MBoGA1UEAwwTQXBwbGlhbmNlIEFjY2VzcyBDQTAeFw0yMTEyMTgxOTEyMTJaFw0z
MTEyMTYxOTEyMTJaME0xCzAJBgNVBAYTAk5MMQswCQYDVQQIDAJaSDETMBEGA1UE
CgwKSG9tZVdpemFyZDEcMBoGA1UEAwwTQXBwbGlhbmNlIEFjY2VzcyBDQTCCASIw
DQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAPBIvW8NRffqdvzHZY0M32fQHiGm
pJgNGhiaQmpJfRDhT9yihM0S/hYcN8IqnfrMqoCQb/56Ub0+dZizmtfcGsE+Lpm1
K1znkWqSDlpnuTNOb70TrsxBmbFuNOZQEi/xOjzT2j98wT0GSfxz1RVq6lZhDRRz
xoe08+Xo4+ttUGanfOggJi0BXygeFEVBpbctVVJ9EgqeEE9itjcMlcxMe1QN14f8
hCcOnId+9PSsdmyUCLrTB0FVYrbNfbJPk/vMU57fu6swBjWhYBxPx9ZhFy+7WnPR
9BFg4seHNVQIqZNrf1YwBXlmZQIL32SRPaiH/+AVNMrYGXBvncY0Km6ZHIMCAwEA
ATANBgkqhkiG9w0BAQsFAAOCAQEA6ybM8xm0PCXg8Rr/q0v1vPxQy44PmwXTDj0e
r2vW4ZMiEwXZCp0Kk2K16KJYz4iJyfiQk8ikAIMiRSbyXzmyQ7XmL1O4l4d8E1Pg
8EImvcyoBxFhd0Lq7VKriLc8Bw8SXbahPMGT+Y8Yz0uIsLAYVwlkLfgppVPmBaLD
QautcQnI8WxPvCIQf5anyzgAyJC5ac6/CkB+iyPcuWcG3RMYvXnC0QoTlRa5YMlE
FweVDlT2C/MdDyOxiAD/H1EP/eaySnU0zsxyD0yNFRKsQfQ+UJEPd2GS1AGA1lTy
CGdyYj/Gghrusw0hM4rYXQSERWGF0mpEnuJ+7bHDolHu0rzgTQ==
-----END CERTIFICATE-----
"""


class UnauthorizedError(Exception):
    """The meter refused the token."""


def parse_power(data: Any, key: str = "active_power_w") -> int | None:
    """The total power in W (positive = import) from a v1 (active_power_w) or v2 (power_w) answer."""
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value))


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cadata=HOMEWIZARD_CA)
    # The HomeWizard certificates have no X.509 v3 extensions (no Authority Key Identifier). Python 3.13+
    # checks strictly by default (VERIFY_X509_STRICT) and then refuses them: "Missing Authority Key Identifier".
    # The chain itself is still verified against the HomeWizard CA.
    context.verify_flags = (context.verify_flags & ~ssl.VERIFY_X509_STRICT) | ssl.VERIFY_X509_PARTIAL_CHAIN
    # the certificate is issued to appliance/p1dongle/<serial>, not to the address
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    return context


async def ssl_context(hass: HomeAssistant) -> ssl.SSLContext:
    """The SSL context for API v2 (loading certificates blocks: done in the executor)."""
    return await hass.async_add_executor_job(_ssl_context)


def homewizard_entry(hass: HomeAssistant, entity_id: str | None) -> tuple[str | None, str | None]:
    """Address and API v2 token of the HomeWizard integration that provides the P1 sensor (None when unknown)."""
    if not entity_id or (entry := er.async_get(hass).async_get(entity_id)) is None or entry.platform != "homewizard":
        return None, None
    if entry.config_entry_id is None or (config := hass.config_entries.async_get_entry(entry.config_entry_id)) is None:
        return None, None
    address = config.data.get("ip_address") or config.data.get("host")
    token = config.data.get("token")
    return (str(address) if address else None), (str(token) if token else None)


async def request_token(hass: HomeAssistant, host: str) -> tuple[str | None, str]:
    """Ask the meter for an API v2 token. Returns (token, "") or (None, reason).

    The meter only gives a token within 30 seconds after its button was pressed; before that it answers
    403 user:creation-not-enabled (reason "button").
    """
    try:
        session = async_get_clientsession(hass)
        async with session.post(
            f"https://{host}/api/user", json={"name": TOKEN_NAME}, headers=V2_HEADERS, ssl=await ssl_context(hass), timeout=TIMEOUT
        ) as response:
            data = await response.json(content_type=None)
            if response.status == 403:
                return None, "button"
            if response.status == 200 and isinstance(data, dict) and data.get("token"):
                return str(data["token"]), ""
            return None, str(data.get("error") if isinstance(data, dict) else response.status)
    except Exception as err:  # noqa: BLE001 - not reachable, no v2 firmware, ...
        return None, f"unreachable: {err}"


async def read_once(hass: HomeAssistant, host: str, token: str | None) -> int | None:
    """Read the power once (API v2 with a token, v1 without). Raises on errors."""
    session = async_get_clientsession(hass)
    if token:
        headers = {**V2_HEADERS, "Authorization": f"Bearer {token}"}
        async with session.get(f"https://{host}/api/measurement", headers=headers, ssl=await ssl_context(hass), timeout=TIMEOUT) as response:
            if response.status == 401:
                raise UnauthorizedError
            if response.status != 200:
                raise OSError(f"HTTP {response.status}")
            return parse_power(await response.json(content_type=None), "power_w")
    async with session.get(f"http://{host}/api/v1/data", timeout=TIMEOUT) as response:
        if response.status != 200:
            raise OSError(f"HTTP {response.status}")
        return parse_power(await response.json(content_type=None))


class HomeWizardP1:
    """Read a HomeWizard P1 meter continuously and hand every new value to the manager.

    Values are handed over one at a time: when the manager is still busy with a value, only the newest
    value that arrives meanwhile is handed over next (no backlog of old values).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        token: str | None,
        on_value: Callable[[int], Awaitable[None]],
        resolve: Callable[[], str | None] | None = None,
    ) -> None:
        self._hass = hass
        self.host = host.strip()
        self.token = token or None
        self._on_value = on_value
        self._resolve = resolve
        self._task: asyncio.Task | None = None
        self._worker: asyncio.Task | None = None
        self._latest: int | None = None
        self.failures = 0
        self.readings = 0
        self.last_value = 0.0  # monotonic time of the last value

    @property
    def api(self) -> str:
        return "v2" if self.token else "v1"

    @property
    def healthy(self) -> bool:
        """True while the meter answers: the Home Assistant sensor is then not used."""
        return self._task is not None and self.readings > 0 and self.failures < FAIL_AFTER

    def start(self) -> None:
        if self._task is None and self.host:
            self._task = self._hass.async_create_background_task(self._run(), f"zendure_ha P1 {self.host}")

    def stop(self) -> None:
        for task in (self._task, self._worker):
            if task is not None:
                task.cancel()
        self._task = self._worker = None

    # ------------------------------------------------------------------------------------------
    def _value(self, power: int) -> None:
        if self.failures >= FAIL_AFTER:
            _LOGGER.info("HomeWizard P1 %s answers again: reading the meter directly (API %s)", self.host, self.api)
        self.failures = 0
        self.readings += 1
        self.last_value = monotonic()
        self._latest = power
        if self._worker is None or self._worker.done():
            self._worker = self._hass.async_create_background_task(self._drain(), "zendure_ha P1 value")

    async def _drain(self) -> None:
        while (power := self._latest) is not None:
            self._latest = None
            try:
                await self._on_value(power)
            except Exception:
                _LOGGER.exception("Processing P1 value %s failed", power)

    def _failed(self, err: Exception) -> None:
        self.failures += 1
        if self.failures == FAIL_AFTER:
            hint = "is the token still valid?" if isinstance(err, UnauthorizedError) else "is the Local API switched on?" if not self.token else "is the meter reachable?"
            _LOGGER.warning("HomeWizard P1 %s does not answer (%s): using the Home Assistant P1 sensor; %s", self.host, err or type(err).__name__, hint)
        # a new address of the meter (DHCP) is known by the HomeWizard integration of Home Assistant
        if self.failures >= FAIL_AFTER and self._resolve is not None and (host := self._resolve()) and host != self.host:
            _LOGGER.info("HomeWizard P1 moved from %s to %s", self.host, host)
            self.host = host

    async def _run(self) -> None:
        while True:
            started = monotonic()
            try:
                if self.token:
                    await self._websocket()
                    wait = 1.0  # closed by the meter: open it again
                else:
                    if (power := await read_once(self._hass, self.host, None)) is None:
                        raise ValueError("no active_power_w in the answer")
                    self._value(power)
                    wait = INTERVAL
            except asyncio.CancelledError:
                raise
            except Exception as err:  # the meter is optional: never stop the loop
                self._failed(err)
                wait = RETRY_UNAUTHORIZED if isinstance(err, UnauthorizedError) else RETRY if self.failures >= FAIL_AFTER else INTERVAL
            await asyncio.sleep(max(0.0, wait - (monotonic() - started)))

    async def _websocket(self) -> None:
        """API v2: authorize, subscribe to the measurements and receive them until the connection ends."""
        session = async_get_clientsession(self._hass)
        ssl_ctx = await ssl_context(self._hass)
        async with asyncio.timeout(RECEIVE_TIMEOUT):
            ws = await session.ws_connect(f"wss://{self.host}/api/ws", ssl=ssl_ctx, heartbeat=RECEIVE_TIMEOUT)
        async with ws:
            while True:
                msg = await ws.receive(timeout=RECEIVE_TIMEOUT)
                if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING, WSMsgType.ERROR):
                    raise ConnectionError("WebSocket closed")
                if msg.type != WSMsgType.TEXT:
                    continue
                data = msg.json()
                if not isinstance(data, dict):
                    continue
                match data.get("type"):
                    case "authorization_requested":
                        await ws.send_json({"type": "authorization", "data": self.token})
                    case "authorized":
                        await ws.send_json({"type": "subscribe", "data": "measurement"})
                    case "measurement":
                        if (power := parse_power(data.get("data"), "power_w")) is not None:
                            self._value(power)
                    case "error":
                        error = str((data.get("data") or {}).get("message", ""))
                        if error.startswith("user:unauthorized"):
                            raise UnauthorizedError("token refused")
                        raise ConnectionError(error or "error")


__all__ = ["FAIL_AFTER", "HomeWizardP1", "homewizard_entry", "parse_power", "read_once", "request_token"]
