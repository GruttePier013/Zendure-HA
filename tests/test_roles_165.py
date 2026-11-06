"""1.6.5: roles on the usable SoC with a guard near the limits, no almost empty cloud reserve, cloud mode hysteresis,
a short charge delay on days the batteries will not be full, and the meta file only when the devices are known."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from custom_components.zendure_ha.dispatch import ROLE_GUARD, VOLATILE_EDGES, VOLATILE_OFF_EDGES, RoleSelector, Volatility
from custom_components.zendure_ha.const import SmartMode
from tests.test_clouds import _charging_manager, _edges, _make_cloudy

T0 = datetime(2026, 11, 4, 10, 25, 0)


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_an_almost_empty_discharger_gives_the_role_away_at_once() -> None:
    clock = _Clock()
    roles = RoleSelector(margin=15, hold=300, clock=clock)
    roles.update([("L1", 10), ("L2", 12), ("L3", 10)], [])
    assert roles.discharger == "L2"
    clock.t += 10  # well within the hold time
    # 4 November 10:25 (minSoc 5): L2 2 points above its limit, L1 and L3 12 points: lead 10 < margin 15
    roles.update([("L1", 12), ("L2", 2), ("L3", 12)], [])
    assert roles.discharger in ("L1", "L3")


def test_two_almost_empty_devices_do_not_swap() -> None:
    clock = _Clock()
    roles = RoleSelector(margin=15, hold=300, clock=clock)
    roles.update([("L1", 5), ("L2", 6)], [])
    assert roles.discharger == "L2"
    clock.t += 400
    roles.update([("L1", 6), ("L2", 4)], [])  # lead 2 < ROLE_GUARD_LEAD
    assert roles.discharger == "L2"


def test_a_well_filled_discharger_keeps_the_margin() -> None:
    clock = _Clock()
    roles = RoleSelector(margin=15, hold=300, clock=clock)
    roles.update([("L1", 50), ("L2", 52)], [])
    clock.t += 400
    roles.update([("L1", 60), ("L2", ROLE_GUARD + 5)], [])  # lead 45, but... margin applies normally
    assert roles.discharger == "L1"
    clock.t += 10
    roles.update([("L1", 40), ("L2", 50)], [])  # lead 10 < 15 and not near the limit: kept
    assert roles.discharger == "L1"


def test_an_almost_full_charger_gives_the_role_away() -> None:
    clock = _Clock()
    roles = RoleSelector(margin=15, hold=300, clock=clock)
    # value = 100 - room below socSet: L3 has room 3 (value 97), L1 room 20 (value 80)
    roles.update([("L2", 50)], [("L1", 85), ("L3", 80)])
    assert roles.charger == "L3"
    clock.t += 10
    roles.update([("L2", 50)], [("L1", 80), ("L3", 97)])
    assert roles.charger == "L1"


def test_manager_uses_the_usable_soc() -> None:
    m, devices = _charging_manager([7, 17, 17])
    for d in devices:
        d.minSoc = SimpleNamespace(asNumber=5)
        d.socSet = SimpleNamespace(asNumber=95)
    assert [m._usable_soc(d) for d in devices] == [2, 12, 12]
    assert [m._room_soc(d) for d in devices] == [88, 78, 78]


async def test_an_almost_empty_discharger_is_no_cloud_reserve() -> None:
    m, devices = _charging_manager([7, 17, 17])
    for d in devices:
        d.minSoc = SimpleNamespace(asNumber=5)
    m.devices = devices
    _make_cloudy(m)
    m.roles.discharger = devices[0].deviceId
    await m._keep_cloud_reserve(-1500, T0)
    assert m.cloud_reserve is None
    devices[0].electricLevel = SimpleNamespace(asInt=40, asNumber=40)
    await m._keep_cloud_reserve(-1500, T0 + timedelta(seconds=1))
    assert m.cloud_reserve == devices[0].deviceId


def test_cloud_mode_does_not_flap_between_6_and_5_edges() -> None:
    vol = Volatility()
    t = _edges(vol, 0.0, VOLATILE_EDGES)
    assert vol.active
    # quiet: edges drop out of the window one by one; at 5 and 4 edges it stays on
    for k in range(0, 600, 5):
        vol.add(t + k, -1500)
        if vol.edges > VOLATILE_OFF_EDGES:
            assert vol.active
    assert not vol.active and vol.edges <= VOLATILE_OFF_EDGES


def test_cloud_mode_ends_when_the_sun_is_gone() -> None:
    vol = Volatility()
    t = _edges(vol, 0.0, VOLATILE_EDGES)
    assert vol.active
    for k in range(0, 1000, 5):
        vol.add(t + k, 300)  # evening: no surplus at all
    assert not vol.active


async def test_short_charge_delay_when_the_batteries_will_not_be_full() -> None:
    m, devices = _charging_manager([40, 50])
    m.charge_last = T0 - timedelta(seconds=60)  # charged a minute ago: normally the long delay
    m.solar_fills = False
    await m.power_charge(-400, T0)
    assert (m.charge_time - T0).total_seconds() == SmartMode.CLOUD_CHARGE_DELAY
    m, devices = _charging_manager([40, 50])
    m.charge_last = T0 - timedelta(seconds=60)
    m.solar_fills = None  # no forecast: as before
    await m.power_charge(-400, T0)
    assert (m.charge_time - T0).total_seconds() == SmartMode.CHARGE_DELAY


def test_no_meta_while_the_devices_load() -> None:
    from custom_components.zendure_ha.manager import ZendureManager

    m = object.__new__(ZendureManager)
    m.devices = []
    assert m._record_meta() is None
    m.devices = [SimpleNamespace(kWh=0.0)]
    assert m._record_meta() is None
