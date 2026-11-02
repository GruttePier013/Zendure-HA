"""zenSDK HTTP layer: serialization, status check, back-off, offline marking and repair issue."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from homeassistant.helpers import issue_registry as ir

from custom_components.zendure_ha.const import DOMAIN, Http


class _Response:
    def __init__(self, status: int, body: str, tracker: dict) -> None:
        self.status, self._body, self._tracker = status, body, tracker

    async def __aenter__(self) -> _Response:
        self._tracker["active"] += 1
        self._tracker["max"] = max(self._tracker["max"], self._tracker["active"])
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc) -> None:  # noqa: ANN002
        self._tracker["active"] -= 1

    async def text(self) -> str:
        return self._body


class _Session:
    def __init__(self, responses: list) -> None:
        self.responses = responses
        self.calls = 0
        self.tracker = {"active": 0, "max": 0}

    def request(self, *_args, **_kwargs) -> _Response:  # noqa: ANN002, ANN003
        self.calls += 1
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return _Response(item[0], item[1], self.tracker)


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Http, "MIN_INTERVAL", 0)


async def test_failed_request_starts_a_backoff(zensdk_device, fast) -> None:  # noqa: ANN001
    zensdk_device.session = _Session([(500, "")])
    assert await zensdk_device._http("GET", "properties/report") == (False, None)
    assert zensdk_device.session.calls == 1
    # inside the back-off window no request reaches the device
    assert await zensdk_device._http("GET", "properties/report") == (False, None)
    assert zensdk_device.session.calls == 1


async def test_backoff_grows_and_marks_the_device_offline(zensdk_device, fast) -> None:  # noqa: ANN001
    zensdk_device.session = _Session([asyncio.TimeoutError()])
    zensdk_device.lastseen = datetime.now()
    delays = []
    for _ in range(Http.OFFLINE_AFTER):
        zensdk_device._http_retry_after = 0.0  # pretend the back-off passed
        await zensdk_device._http("GET", "properties/report")
        delays.append(zensdk_device._http_retry_after)
    assert zensdk_device._http_failures == Http.OFFLINE_AFTER
    assert zensdk_device.lastseen == datetime.min
    assert delays[0] > 0


async def test_success_resets_failures_and_clears_the_issue(zensdk_device, fast, hass) -> None:  # noqa: ANN001
    zensdk_device.connection._attr_current_option = "zenSDK"
    zensdk_device.session = _Session([asyncio.TimeoutError()])
    for _ in range(Http.ISSUE_AFTER):
        zensdk_device._http_retry_after = 0.0
        await zensdk_device._http("GET", "x")
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"zensdk_unreachable_{zensdk_device.snNumber.lower()}") is not None

    zensdk_device.session = _Session([(200, '{"a": 1}')])
    zensdk_device._http_retry_after = 0.0
    assert await zensdk_device._http("GET", "x") == (True, {"a": 1})
    assert zensdk_device._http_failures == 0
    assert registry.async_get_issue(DOMAIN, f"zensdk_unreachable_{zensdk_device.snNumber.lower()}") is None


async def test_requests_are_never_parallel(zensdk_device, fast) -> None:  # noqa: ANN001
    session = _Session([(200, "{}")])
    zensdk_device.session = session
    await asyncio.gather(*(zensdk_device._http("GET", "x") for _ in range(5)))
    assert session.calls == 5
    assert session.tracker["max"] == 1


async def test_http_failures_do_not_mark_offline_while_mqtt_reports_arrive(zensdk_device, fast) -> None:  # noqa: ANN001
    """The address can be wrong while the device still reports over the local MQTT broker: it stays online."""
    from time import monotonic

    zensdk_device.session = _Session([asyncio.TimeoutError()])
    zensdk_device.lastseen = datetime.now()
    zensdk_device.mqtt_last = monotonic()
    for _ in range(Http.OFFLINE_AFTER + 1):
        zensdk_device._http_retry_after = 0.0
        await zensdk_device._http("GET", "properties/report")
    assert zensdk_device.lastseen != datetime.min


def test_zensdk_devices_are_subscribed_on_the_local_broker(zensdk_device) -> None:  # noqa: ANN001
    from unittest.mock import MagicMock

    from custom_components.zendure_ha.api import Api

    zensdk_device.connection._attr_current_option = "zenSDK"
    api = object.__new__(Api)
    client = MagicMock()
    Api.devices = {zensdk_device.deviceId: zensdk_device}
    try:
        api.mqttConnect(client, "local", None, 0, None)
    finally:
        Api.devices = {}
    topics = [c.args[0] for c in client.subscribe.call_args_list]
    assert f"/{zensdk_device.prodkey}/{zensdk_device.deviceId}/#" in topics


async def test_guessed_name_raises_the_no_address_issue_sooner(zensdk_device, fast, hass) -> None:  # noqa: ANN001
    zensdk_device.connection._attr_current_option = "zenSDK"
    zensdk_device.session = _Session([asyncio.TimeoutError()])
    assert zensdk_device.address_source == "guess"
    for _ in range(Http.OFFLINE_AFTER):
        zensdk_device._http_retry_after = 0.0
        await zensdk_device._http("GET", "x")
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"zensdk_unreachable_{zensdk_device.snNumber.lower()}")
    assert issue is not None and issue.translation_key == "zensdk_no_address"


async def test_no_zensdk_issue_in_cloud_mode(zensdk_device, fast, hass) -> None:  # noqa: ANN001
    """The start-up HTTP read also runs in cloud mode; failures there must not raise a zenSDK repair issue."""
    zensdk_device.connection._attr_current_option = "cloud"
    zensdk_device.session = _Session([asyncio.TimeoutError()])
    for _ in range(Http.ISSUE_AFTER + 1):
        zensdk_device._http_retry_after = 0.0
        await zensdk_device._http("GET", "x")
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"zensdk_unreachable_{zensdk_device.snNumber.lower()}") is None
