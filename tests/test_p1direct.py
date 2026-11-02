"""Reading a HomeWizard P1 meter directly (API v2 WebSocket / API v1) and following devices on the network."""

from __future__ import annotations

import asyncio
import json
import ssl
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import WSMsgType

from custom_components.zendure_ha import discovery, p1direct
from custom_components.zendure_ha.const import CONF_DEVICE_IPS, Http
from custom_components.zendure_ha.manager import ZendureManager
from custom_components.zendure_ha.p1direct import FAIL_AFTER, HomeWizardP1, UnauthorizedError, parse_power


# ----------------------------------------------------------------------------------------------
# P1 meter
# ----------------------------------------------------------------------------------------------
def test_parse_power() -> None:
    assert parse_power({"active_power_w": 512.4}) == 512
    assert parse_power({"power_w": -678}, "power_w") == -678
    assert parse_power({"power_w": True}, "power_w") is None
    assert parse_power({"energy_import_kwh": 1.0}, "power_w") is None
    assert parse_power(None) is None


def test_homewizard_ca_is_valid() -> None:
    context = p1direct._ssl_context()
    assert context.verify_mode.name == "CERT_REQUIRED"
    assert any("HomeWizard" in str(c.get("subject")) for c in context.get_ca_certs())
    # HomeWizard certificates have no Authority Key Identifier: strict checking (default since Python 3.13) refuses them
    assert not context.verify_flags & ssl.VERIFY_X509_STRICT


class _FakeWs:
    def __init__(self, messages: list[dict[str, Any]], expect_token: str) -> None:
        self._messages = messages
        self.sent: list[dict[str, Any]] = []
        self._token = expect_token

    async def __aenter__(self) -> _FakeWs:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    async def receive(self, timeout: float | None = None) -> Any:
        await asyncio.sleep(0)
        if not self._messages:
            return SimpleNamespace(type=WSMsgType.CLOSED)
        message = self._messages.pop(0)
        return SimpleNamespace(type=WSMsgType.TEXT, json=lambda m=message: m)


def _connect(ws: _FakeWs) -> Any:
    async def connect(*_: Any, **__: Any) -> _FakeWs:
        return ws

    return connect


async def test_websocket_authorizes_subscribes_and_hands_over_values(hass, monkeypatch) -> None:  # noqa: ANN001
    ws = _FakeWs(
        [
            {"type": "authorization_requested", "data": {"api_version": "2.0.0"}},
            {"type": "authorized"},
            {"type": "measurement", "data": {"power_w": 1200, "energy_import_kwh": 1}},
            {"type": "measurement", "data": {"power_w": -300}},
        ],
        "ABC",
    )
    session = SimpleNamespace(ws_connect=_connect(ws))
    monkeypatch.setattr(p1direct, "async_get_clientsession", lambda _hass: session)

    async def ctx(_hass: Any) -> None:
        return None

    monkeypatch.setattr(p1direct, "ssl_context", ctx)
    values: list[int] = []

    async def on_value(p1: int) -> None:
        values.append(p1)

    meter = HomeWizardP1(hass, "192.168.1.30", "ABC", on_value)
    meter._task = object()  # type: ignore[assignment] - running
    with pytest.raises(ConnectionError):
        await meter._websocket()
    await asyncio.sleep(0.01)
    assert ws.sent == [{"type": "authorization", "data": "ABC"}, {"type": "subscribe", "data": "measurement"}]
    assert values[-1] == -300
    assert meter.healthy


async def test_websocket_refused_token(hass, monkeypatch) -> None:  # noqa: ANN001
    ws = _FakeWs([{"type": "error", "data": {"message": "user:unauthorized"}}], "X")
    monkeypatch.setattr(p1direct, "async_get_clientsession", lambda _hass: SimpleNamespace(ws_connect=_connect(ws)))

    async def ctx(_hass: Any) -> None:
        return None

    monkeypatch.setattr(p1direct, "ssl_context", ctx)

    async def on_value(_p1: int) -> None:
        return None

    with pytest.raises(UnauthorizedError):
        await HomeWizardP1(hass, "h", "X", on_value)._websocket()


async def test_only_newest_value_while_busy(hass) -> None:  # noqa: ANN001
    handled: list[int] = []
    gate = asyncio.Event()

    async def on_value(p1: int) -> None:
        handled.append(p1)
        await gate.wait()

    meter = HomeWizardP1(hass, "h", None, on_value)
    meter._value(100)
    await asyncio.sleep(0)
    for value in (200, 300, 400):  # the manager is still busy with 100
        meter._value(value)
    gate.set()
    await asyncio.sleep(0.01)
    assert handled == [100, 400]


async def test_fallback_and_new_address(hass) -> None:  # noqa: ANN001
    async def on_value(_p1: int) -> None:
        return None

    meter = HomeWizardP1(hass, "192.168.1.30", None, on_value, resolve=lambda: "192.168.1.31")
    meter._task = object()  # type: ignore[assignment]
    meter._value(10)
    assert meter.healthy
    for _ in range(FAIL_AFTER):
        meter._failed(OSError("timeout"))
    assert not meter.healthy  # the Home Assistant sensor is used again
    assert meter.host == "192.168.1.31"  # the HomeWizard integration knows the new address
    meter._value(20)
    assert meter.healthy


async def test_sensor_ignored_while_meter_is_read_directly(hass) -> None:  # noqa: ANN001
    manager = object.__new__(ZendureManager)
    manager.hass = hass
    handled: list[int] = []

    async def handle(p1: int, _source: str = "sensor") -> None:
        handled.append(p1)

    manager._handle_p1 = handle  # type: ignore[method-assign]
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="250", attributes={"unit_of_measurement": "W"})})
    manager.p1direct = SimpleNamespace(healthy=True)  # type: ignore[assignment]
    await manager._p1_changed(event)  # type: ignore[arg-type]
    assert handled == []
    manager.p1direct = SimpleNamespace(healthy=False)  # type: ignore[assignment]
    await manager._p1_changed(event)  # type: ignore[arg-type]
    assert handled == [250]


# ----------------------------------------------------------------------------------------------
# following a device on the network
# ----------------------------------------------------------------------------------------------
def test_report_serial_and_subnet() -> None:
    assert discovery.report_serial(json.dumps({"sn": "HEC4NENCN521110", "properties": {}})) == "HEC4NENCN521110"
    assert discovery.report_serial("<html>") is None
    hosts = discovery.subnet_hosts("192.168.1.201")
    assert len(hosts) == 253 and "192.168.1.201" not in hosts
    assert hosts[:2] == ["192.168.1.200", "192.168.1.202"]  # nearest first
    assert discovery.subnet_hosts("solarflow.local") == []


async def test_search_subnet_finds_moved_device(hass, monkeypatch) -> None:  # noqa: ANN001
    async def probe(_hass: Any, host: str, _timeout: Any = None) -> str | None:
        return {"192.168.1.57": "HEC4NENCN521112", "192.168.1.8": "OTHERDEVICE"}.get(host)

    monkeypatch.setattr(discovery, "probe_serial", probe)
    assert await discovery.search_subnet(hass, "192.168.1.201", {"HEC4NENCN521112"}) == {"HEC4NENCN521112": "192.168.1.57"}


async def test_unmatched_mdns_service_identified_by_report(hass, monkeypatch) -> None:  # noqa: ANN001
    found: list[tuple[str, str]] = []
    disc = discovery.ZendureDiscovery(hass, lambda sn, a: found.append((sn, a)), lambda: {"HEC4NENCN521112": "L1"})

    class Info:
        def __init__(self, *_: Any) -> None:
            self.properties: dict[Any, Any] = {}

        async def async_request(self, *_: Any) -> bool:
            return True

        def parsed_addresses(self) -> list[str]:
            return ["192.168.1.57"]

    import zeroconf.asyncio as zc

    monkeypatch.setattr(zc, "AsyncServiceInfo", Info)
    calls: list[str] = []

    async def probe(_hass: Any, host: str, _timeout: Any = None) -> str | None:
        calls.append(host)
        return "hec4nencn521112"

    monkeypatch.setattr(discovery, "probe_serial", probe)
    aiozc = SimpleNamespace(zeroconf=None)
    await disc._resolve(aiozc, discovery.SERVICE_TYPE, "Zendure-solarFlow2400AC-abc123._zendure._tcp.local.")
    await disc._resolve(aiozc, discovery.SERVICE_TYPE, "Zendure-solarFlow2400AC-abc123._zendure._tcp.local.")
    assert found == [("HEC4NENCN521112", "192.168.1.57")] * 2
    assert calls == ["192.168.1.57"]  # learned: the device is asked only once


async def test_manual_address_follows_when_it_no_longer_answers(hass, zensdk_device) -> None:  # noqa: ANN001
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    sn = zensdk_device.snNumber
    entry = MockConfigEntry(domain="zendure_ha", data={CONF_DEVICE_IPS: {sn: "192.168.1.201"}})
    entry.add_to_hass(hass)
    manager = object.__new__(ZendureManager)
    manager.hass = hass
    manager.config_entry = entry
    manager.devices = [zensdk_device]
    manager._ip_overrides = {sn: "192.168.1.201"}
    zensdk_device.set_address("192.168.1.201", "manual")

    manager._discovered(sn, "192.168.1.57")  # the manual address still answers: kept
    assert zensdk_device.ipAddress == "192.168.1.201"

    zensdk_device._http_failures = Http.OFFLINE_AFTER
    manager._discovered(sn, "192.168.1.57")
    assert zensdk_device.ipAddress == "192.168.1.57"
    assert zensdk_device.address_source == "manual"
    assert entry.data[CONF_DEVICE_IPS][sn] == "192.168.1.57"
