"""Zendure HA-GP013 device."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

from aiohttp import ClientTimeout
from bleak import BleakClient
from bleak.exc import BleakError

try:
    from bleak_retry_connector import establish_connection
except ImportError:
    establish_connection = None

from homeassistant.components import bluetooth, persistent_notification
from homeassistant.components.number import NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from paho.mqtt import client as mqtt_client

from .binary_sensor import ZendureBinarySensor
from .button import ZendureButton
from .const import DOMAIN, DeviceState, Flash, Http, SmartMode
from .entity import POWER_DEADBAND, EntityDevice, EntityZendure, quiet
from .number import ZendureNumber, ZendureRestoreNumber
from .select import ZendureRestoreSelect, ZendureSelect
from .sensor import ZendureRestoreSensor, ZendureSensor
from .slew import DEFAULT_DOWN, DEFAULT_FLIP_DELAY, DEFAULT_MIN_RUN, DEFAULT_READY_UP, DEFAULT_UP, ZERO_BAND, SlewLimiter

_LOGGER = logging.getLogger(__name__)

CONST_HEADER = {"content-type": "application/json; charset=UTF-8"}
CONST_TIMEOUT = ClientTimeout(total=Http.TIMEOUT)
HTTP_OK = 200
SF_COMMAND_CHAR = "0000c304-0000-1000-8000-00805f9b34fb"


class ZendureBattery(EntityDevice):
    """Zendure Battery class for devices."""

    @staticmethod
    def get_battery_type(sn: str, pack_type: int | None = None) -> tuple[str, str, float]:
        model = "???"
        match sn[0]:
            case "A":
                if sn[3] == "3":
                    model = "AIO2400"
                    kWh = 2.4
                else:
                    model = "AB1000"
                    kWh = 0.96
            case "B":
                # packType 70 is the SF4000 Mix AC+'s internal 8 kWh pack, which shares
                # its serial prefix with the unrelated 0.96 kWh AB1000S.
                if pack_type == 70:
                    model = "I8000"
                    kWh = 8.0
                else:
                    model = "AB1000S"
                    kWh = 0.96
            case "C":
                # External AB2000X and internal AB2000X of SF800+/SF800Pro/SF1600AC+ starting with CO4A. They are also described as additional battery in the Zendure App, even when they are integrated into the device.
                model = "AB2000" + ("S" if sn[3] == "F" else "X" if sn[3] == "E" else "")
                kWh = 1.92
            case "F":
                model = "AB3000"
                kWh = 2.88
            case "G":
                model = "AB3000L"
                kWh = 2.88
            case "J":
                # JO2A => internal battery of SF2400AC pro
                # JO4A => internal battery of SF2400AC+
                model = "I2400"
                kWh = 2.4
            case _:
                model = "Unknown"
                kWh = 0.0

        name = f"{model} {sn[-5:]}".strip()
        return name, model, kWh

    def __init__(self, hass: HomeAssistant, sn: str, parent: EntityDevice, pack_type: int | None = None) -> None:
        """Initialize Device."""
        name, model, self.kWh = ZendureBattery.get_battery_type(sn, pack_type)
        super().__init__(hass, sn, name, model, "", sn, parent.sn)
        self.attr_device_info["serial_number"] = sn
        self.deltaVoltage = ZendureSensor(self, "deltaVoltage", None, "V", "voltage", "measurement", 3)

    def entityUpdate(self, key: Any, value: Any) -> bool:
        """Update entity state and recalculate deltaVoltage when maxVol or minVol changes."""
        changed = super().entityUpdate(key, value)
        if changed and key in {"maxVol", "minVol"}:
            max_vol = self.entities.get("maxVol")
            min_vol = self.entities.get("minVol")
            if max_vol is not None and min_vol is not None:
                max_val = max_vol.asNumber
                min_val = min_vol.asNumber
                if max_val != 0 and min_val != 0:
                    self.deltaVoltage.update_value(round(max_val - min_val, 3))
        return changed


class ZendureDevice(EntityDevice):
    """Zendure Device class for devices integration."""

    def __init__(self, hass: HomeAssistant, deviceId: str, name: str, model: str, definition: dict[str, str], parent: str | None = None) -> None:
        """Initialize Device."""
        from .fusegroup import FuseGroup

        """Initialize Device."""
        self.prodkey = definition["productKey"]
        super().__init__(hass, deviceId, name, model, self.prodkey, definition["snNumber"], parent)
        self.snNumber = definition["snNumber"]
        self.definition = definition
        self.fuseGrp: FuseGroup

        self.mqtt: mqtt_client.Client | None = None
        self.zendure: mqtt_client.Client | None = None
        self.ipAddress = definition.get("ip", "") if definition.get("ip", "") != "" else self.default_hostname(definition["productModel"], self.snNumber)

        self.topic_read = f"iot/{self.prodkey}/{self.deviceId}/properties/read"
        self.topic_write = f"iot/{self.prodkey}/{self.deviceId}/properties/write"
        self.topic_function = f"iot/{self.prodkey}/{self.deviceId}/function/invoke"

        self.batteries: dict[str, ZendureBattery | None] = {}
        self.lastseen = datetime.min
        self._messageid = 0
        self.kWh = 0.0

        self.charge_limit: int = 0
        self.charge_optimal: int = 0
        self.charge_start: int = 0
        self.discharge_limit: int = 0
        self.discharge_optimal: int = 0
        self.discharge_start: int = 0
        self.maxSolar = 0
        self.pwr_max: int = 0
        self.pwr_produced: int = 0
        self.actualKwh: float = 0.0
        self.state: DeviceState = DeviceState.OFFLINE
        self.exports_bypass: bool = True
        self.slew = SlewLimiter()
        self._slew_base: tuple[float, float, float, float, float] = (DEFAULT_UP, DEFAULT_DOWN, DEFAULT_FLIP_DELAY, DEFAULT_MIN_RUN, DEFAULT_READY_UP)
        self._lastMessageUpdate = datetime.min

        # flash / RAM protection of the device
        self._reported: dict[str, Any] = {}  # last value reported by the device per property
        self._writes: dict[str, tuple[Any, float]] = {}  # property -> (last value written, time) for duplicate detection
        self._deferred: dict[str, tuple[EntityZendure, Any]] = {}  # persistent writes waiting for their rate limit window
        self._deferred_handle: asyncio.TimerHandle | None = None
        self.persist_safe_state = True  # allow the one-time write of the safe 0 W state to the flash
        self.flash_count = 0  # number of writes that reached the flash of the device (this session)
        self._flash_safe: bool | None = None  # True: the flash holds the safe 0 W state
        self._flash_last = -Flash.MIN_INTERVAL  # monotonic time of the last automatic flash write
        self._idle_since: float | None = None
        self._off_sent = False  # power_off was sent and nothing was commanded since

        self.create_entities()

    @staticmethod
    def default_hostname(product_model: str, sn: str) -> str:
        """Build the mDNS hostname used when the cloud does not report an IP address.

        Only letters, digits and hyphens are valid in a DNS label, so characters such as the
        '+' in 'SolarFlow 2400 AC+' are dropped; otherwise every lookup fails with
        'Misformatted domain name'.
        """
        model = re.sub(r"[^A-Za-z0-9-]", "", product_model)
        serial = re.sub(r"[^A-Za-z0-9-]", "", sn)
        return f"zendure-{model}-{serial}.local"

    def create_entities(self) -> None:
        """Create the device entities."""
        self.limitOutput =ZendureNumber(self, "outputLimit", self.entityWrite, None, "W", "power", self.discharge_limit, 0, NumberMode.SLIDER)
        self.limitInput = ZendureNumber(self, "inputLimit", self.entityWrite, None, "W", "power", self.charge_limit, 0, NumberMode.SLIDER)
        self.minSoc = ZendureNumber(self, "minSoc", self.entityWrite, None, "%", "soc", 100, 0, NumberMode.SLIDER, 10)
        self.socSet = ZendureNumber(self, "socSet", self.entityWrite, None, "%", "soc", 100, 0, NumberMode.SLIDER, 10)
        self.socStatus = ZendureSensor(self, "socStatus", state=0)
        self.socLimit = ZendureSensor(self, "socLimit", state=0)
        self.byPass = ZendureSensor(self, "pass", state=0)

        fuseGroups = {
            0: "unused",
            1: "owncircuit",
            2: "group800",
            3: "group800_2400",
            4: "group1200",
            5: "group2000",
            6: "group2400",
            7: "group3600",
            8: "group4000",
            9: "group5000",
        }
        self.fuseGroup = ZendureRestoreSelect(self, "fuseGroup", fuseGroups, None)
        self.acMode = ZendureSelect(self, "acMode", {1: "input", 2: "output"}, self.entityWrite, 1)
        self.electricLevel = ZendureSensor(self, "electricLevel", None, "%", "battery", "measurement")
        # set homeInput to 0 for devices, which have no AC charge capability
        self.homeInput = ZendureSensor(self, "gridInputPower", None, "W", "power", "measurement", state=0, deadband=POWER_DEADBAND)
        self.solarInput = ZendureSensor(self, "solarInputPower", None, "W", "power", "measurement", icon="mdi:solar-panel", deadband=POWER_DEADBAND)
        self.batteryInput = ZendureSensor(self, "outputPackPower", None, "W", "power", "measurement", deadband=POWER_DEADBAND)
        self.batteryOutput = ZendureSensor(self, "packInputPower", None, "W", "power", "measurement", deadband=POWER_DEADBAND)
        self.homeOutput = ZendureSensor(self, "outputHomePower", None, "W", "power", "measurement", deadband=POWER_DEADBAND)
        self.batInOut = ZendureSensor(self, "batInOut", None, "W", "power", "measurement", 0)
        self.heatState = ZendureBinarySensor(self, "heatState")
        self.hemsState = ZendureBinarySensor(self, "hemsState")
        self.hemsStateUpdated = datetime.min
        self.availableKwh = ZendureSensor(self, "available_kwh", None, "kWh", "energy_storage", None, 1)
        self.totalKwh = ZendureSensor(self, "total_kwh", None, "kWh", "energy_storage", "measurement", 2)
        self.connectionStatus = ZendureSensor(self, "connectionStatus")
        self.connection: ZendureRestoreSelect
        self.bleAdapter: ZendureRestoreSelect | None = None
        self.remainingTime = ZendureSensor(self, "remainingTime", None, "h", "duration", "measurement")
        self.nextCalibration = ZendureRestoreSensor(self, "nextCalibration", None, None, "timestamp", None)

        self.aggrCharge = ZendureRestoreSensor(self, "aggrCharge", None, "kWh", "energy", "total_increasing", 2)
        self.aggrDischarge = ZendureRestoreSensor(self, "aggrDischarge", None, "kWh", "energy", "total_increasing", 2)
        # Round-trip efficiency: ratio of total energy discharged to total energy charged, expressed as a percentage
        self.roundtripEfficiency = ZendureSensor(self, "roundtripEfficiency", None, "%", None, "measurement", 1)
        self.aggrHomeInput = ZendureRestoreSensor(self, "aggrGridInputPower", None, "kWh", "energy", "total_increasing", 2)
        self.aggrHomeOut = ZendureRestoreSensor(self, "aggrOutputHome", None, "kWh", "energy", "total_increasing", 2)
        self.aggrSolar = ZendureRestoreSensor(self, "aggrSolar", None, "kWh", "energy", "total_increasing", 2)
        self.aggrSwitchCount = ZendureRestoreSensor(self, "switchCount", None, None, None, "total_increasing", 0)

        # diagnostics
        diag = EntityCategory.DIAGNOSTIC
        self.lastMessage = ZendureSensor(self, "lastMessage", None, None, "timestamp", None, category=diag)
        self.commandPower = ZendureSensor(self, "commandPower", None, "W", "power", "measurement", 0, state=0, category=diag)
        self.commandCount = ZendureSensor(self, "commandCount", None, None, None, "total_increasing", 0, state=0, category=diag)
        self.slewActive = ZendureBinarySensor(self, "slewActive", None, None, category=diag)
        self.flashWrites = ZendureSensor(self, "flashWrites", None, None, None, "total_increasing", 0, state=0, category=diag)
        # high frequency diagnostics are disabled by default: every state change is a row in the recorder database
        quiet(self.lastMessage, self.commandPower, self.commandCount)

        # per device scaling of the slew rate (100 % = the global setting)
        self.slewFactor = ZendureRestoreNumber(self, "slewFactor", self._slew_factor_changed, None, "%", None, 300, 10, NumberMode.BOX, True)
        self.slewFactor._attr_native_value = 100
        self.slewFactor._attr_entity_category = EntityCategory.CONFIG

    def setLimits(self, charge: int, discharge: int) -> None:
        """Set the device limits."""
        try:
            self.charge_limit = charge
            self.charge_optimal = charge // 4
            self.charge_start = charge // 10
            self.limitInput.update_range(0, abs(charge))

            self.discharge_limit = discharge
            self.discharge_optimal = discharge // 4
            self.discharge_start = discharge // 10
            self.limitOutput.update_range(0, discharge)
        except Exception:
            _LOGGER.error("SetLimits error %s %s %s!", self.name, charge, discharge)

    def setStatus(self) -> None:
        from .api import Api

        try:
            if self.lastseen == datetime.min:
                self.connectionStatus.update_value(0)
            elif self.socStatus.asInt == 1:
                self.connectionStatus.update_value(1)
            elif self.hemsState.is_on:
                self.connectionStatus.update_value(2)
            elif self.fuseGroup.value == 0:
                self.connectionStatus.update_value(3)
            elif self.connection.value == SmartMode.ZENSDK:
                self.connectionStatus.update_value(12)
            elif self.mqtt is not None and self.mqtt.host == Api.localServer:
                self.connectionStatus.update_value(11)
            else:
                self.connectionStatus.update_value(10)
        except Exception:
            self.connectionStatus.update_value(0)

    def entityUpdate(self, key: Any, value: Any) -> bool:
        # update entity state
        if key in {"remainOutTime", "remainInputTime"}:
            self.remainingTime.update_value(self.calcRemainingTime())
            return True

        self._reported[key] = value
        changed = super().entityUpdate(key, value)
        try:
            if changed:
                match key:
                    case "packState":
                        if value == 0:
                            self.aggrSwitchCount.update_value(1 + self.aggrSwitchCount.asNumber)
                    case "outputPackPower":
                        if not self.heatState.is_on:
                            self.aggrCharge.aggregate(dt_util.now(), value)
                        self.aggrDischarge.aggregate(dt_util.now(), 0)
                        self.batInOut.update_value(self.batteryOutput.asInt - self.batteryInput.asInt)
                        self.roundtripEfficiency.update_value(round(self.aggrDischarge.asNumber / charge * 100, 1) if (charge := self.aggrCharge.asNumber) > 0 else 0)
                    case "packInputPower":
                        self.aggrCharge.aggregate(dt_util.now(), 0)
                        self.aggrDischarge.aggregate(dt_util.now(), value)
                        self.batInOut.update_value(self.batteryOutput.asInt - self.batteryInput.asInt)
                        self.roundtripEfficiency.update_value(round(self.aggrDischarge.asNumber / charge * 100, 1) if (charge := self.aggrCharge.asNumber) > 0 else 0)
                    case "solarInputPower":
                        self.aggrSolar.aggregate(dt_util.now(), value)
                    case "gridInputPower":
                        self.aggrHomeInput.aggregate(dt_util.now(), value)
                    case "outputHomePower":
                        self.aggrHomeOut.aggregate(dt_util.now(), value)
                    case "gridOffPower":
                        # only the models with an off-grid output have this aggregate
                        if (aggregate := getattr(self, "aggrOffGrid", None)) is not None:
                            aggregate.aggregate(dt_util.now(), value)
                    case "inverseMaxPower":
                        self.setLimits(self.charge_limit, value)
                    case "chargeLimit" | "chargeMaxLimit":
                        self.setLimits(-value, self.discharge_limit)
                    case "hemsState" | "socStatus":
                        self.setStatus()
                        if key == "socStatus" and self.socStatus.asInt == 0:
                            self.nextCalibration.update_value(dt_util.now() + timedelta(days=30))
                    case "electricLevel" | "minSoc" | "socLimit":
                        if self.electricLevel.asInt == 100:
                            self.nextCalibration.update_value(dt_util.now() + timedelta(days=30))
                        self.availableKwh.update_value((self.electricLevel.asNumber - self.minSoc.asNumber) / 100 * self.kWh)
                    case "gridReverse":
                        self.exports_bypass = value == 1
        except Exception as e:
            _LOGGER.error("EntityUpdate error %s %s %s!", self.name, key, e)
            _LOGGER.error(traceback.format_exc())

        return changed

    def calcRemainingTime(self) -> float:
        """Calculate the remaining time."""
        level = self.electricLevel.asInt
        power = self.batteryOutput.asInt - self.batteryInput.asInt

        if power == 0:
            return 0

        if power < 0:
            soc = self.socSet.asNumber
            return 0 if level >= soc else min(999, self.kWh * 10 / -power * (soc - level))

        soc = self.minSoc.asNumber
        return 0 if level <= soc else min(999, self.kWh * 10 / power * (level - soc))

    def _is_ram_property(self, _name: str) -> bool:
        """True when a write of this property never reaches the flash of the device."""
        return False

    def _write_properties(self, name: str, value: Any) -> dict[str, Any]:
        return {name: value}

    async def entityWrite(self, entity: EntityZendure, value: Any) -> None:
        """Write a property to the device, protecting its flash.

        * A value equal to what the device reported is not written again.
        * An identical write within a minute is dropped.
        * A persistent property is written at most once per PROPERTY_INTERVAL; in between only the
          newest value is kept and written when the window has passed (an automation that sets a
          value every few seconds results in one flash write per window).
        """
        if entity.translation_key is None:
            _LOGGER.error("Entity %s has no translation_key, cannot write property %s", entity.name, self.name)
            return

        name = entity.propertyName
        now = monotonic()
        if name in self._reported and self._reported[name] == value:
            _LOGGER.debug("Property %s %s already is %s", self.name, name, value)
            self._deferred.pop(name, None)
            return

        last = self._writes.get(name)
        if last is not None and last[0] == value and now - last[1] < Flash.SAME_VALUE_WINDOW:
            return

        if last is not None and not self._is_ram_property(name) and now - last[1] < Flash.PROPERTY_INTERVAL:
            self._deferred[name] = (entity, value)
            self._schedule_deferred(Flash.PROPERTY_INTERVAL - (now - last[1]))
            _LOGGER.debug("Property %s %s => %s is written later (flash protection)", self.name, name, value)
            return

        self._deferred.pop(name, None)
        await self._write_now(name, value)

    async def _write_now(self, name: str, value: Any) -> None:
        _LOGGER.info("Writing property %s %s => %s", self.name, name, value)
        if await self._send_properties(self._write_properties(name, value)):
            self._writes[name] = (value, monotonic())
            self._reported.pop(name, None)  # unknown until the device reports it
            if not self._is_ram_property(name):
                self._count_flash_write()
        else:
            _LOGGER.warning("Property %s %s => %s could not be sent: no connection to the device", self.name, name, value)

    async def _send_properties(self, properties: dict[str, Any]) -> bool:
        """Send properties to the device over MQTT. Returns False when there is no connection."""
        if self.mqtt is None:
            return False
        self._messageid += 1
        payload = json.dumps(
            {
                "deviceId": self.deviceId,
                "messageId": self._messageid,
                "timestamp": int(datetime.now().timestamp()),
                "properties": properties,
            },
            default=lambda o: o.__dict__,
        )
        self.mqtt.publish(self.topic_write, payload)
        return True

    def _schedule_deferred(self, delay: float) -> None:
        if self._deferred_handle is None and self.hass.loop.is_running():
            self._deferred_handle = self.hass.loop.call_later(max(1.0, delay), self._deferred_fire)

    def _deferred_fire(self) -> None:
        self._deferred_handle = None
        self.hass.async_create_task(self._flush_deferred())

    async def _flush_deferred(self) -> None:
        now = monotonic()
        for name, (_entity, value) in list(self._deferred.items()):
            last = self._writes.get(name)
            if last is not None and now - last[1] < Flash.PROPERTY_INTERVAL:
                self._schedule_deferred(Flash.PROPERTY_INTERVAL - (now - last[1]))
                continue
            del self._deferred[name]
            if self._reported.get(name) != value:
                await self._write_now(name, value)

    def cancel_timers(self) -> None:
        """Cancel pending timers when the integration is unloaded."""
        if self._deferred_handle is not None:
            self._deferred_handle.cancel()
            self._deferred_handle = None
        self._deferred.clear()

    def _count_flash_write(self) -> None:
        self.flash_count += 1
        self.flashWrites.update_value(self.flash_count)

    def _slew_factor_changed(self, _entity: Any, _value: Any) -> None:
        self._apply_slew()

    def configure_slew(self, enabled: bool, up: float, down: float, flip_delay: float, min_run: float, ready_up: float = DEFAULT_READY_UP) -> None:
        """Apply the global slew settings; the per device factor scales the rates."""
        self.slew.enabled = enabled
        self._slew_base = (up, down, flip_delay, min_run, ready_up)
        self._apply_slew()

    def _apply_slew(self) -> None:
        factor = (self.slewFactor.asNumber or 100) / 100
        up, down, flip_delay, min_run, ready_up = self._slew_base
        self.slew.up = up * factor
        self.slew.down = down * factor
        self.slew.ready_up = ready_up * factor
        self.slew.flip_delay = flip_delay
        self.slew.min_run = min_run

    @property
    def idle(self) -> bool:
        """True when nothing flows and nothing is commanded: the device can be polled and written less often."""
        return (
            self.homeInput.asInt == 0
            and self.homeOutput.asInt == 0
            and self.solarInput.asInt == 0
            and abs(self.slew.current) < ZERO_BAND
            and not self.slew.pending
        )

    async def button_press(self, _key: str) -> None:
        return

    def mqttPublish(self, topic: str, command: Any, client: mqtt_client.Client | None = None) -> None:
        command["messageId"] = self._messageid
        command["deviceId"] = self.deviceId
        command["timestamp"] = int(datetime.now().timestamp())
        payload = json.dumps(command, default=lambda o: o.__dict__)

        if client is not None:
            client.publish(topic, payload)
        elif self.mqtt is not None:
            self.mqtt.publish(topic, payload)

    def mqttInvoke(self, command: Any) -> None:
        self._messageid += 1
        command["messageId"] = self._messageid
        command["deviceKey"] = self.deviceId
        command["timestamp"] = int(datetime.now().timestamp())
        self.mqttPublish(self.topic_function, command)

    def touchLastMessage(self) -> None:
        """Update the last message timestamp at most every minute."""
        now = datetime.now()
        if (now - self._lastMessageUpdate).total_seconds() >= 60:
            self._lastMessageUpdate = now
            self.lastMessage.update_value(dt_util.now())

    async def mqttProperties(self, payload: Any) -> None:
        self.touchLastMessage()
        if self.lastseen == datetime.min:
            self.lastseen = datetime.now() + timedelta(minutes=5)
            self.setStatus()
        else:
            self.lastseen = datetime.now() + timedelta(minutes=5)

        if (properties := payload.get("properties", None)) and len(properties) > 0:
            for key, value in properties.items():
                self.entityUpdate(key, value)

        # update the battery properties
        if batprops := payload.get("packData", None):
            for b in batprops:
                if (sn := b.get("sn", None)) is None:
                    continue

                if (bat := self.batteries.get(sn, None)) is None:
                    bat = ZendureBattery(self.hass, sn, self, b.get("packType"))
                    self.batteries[sn] = bat

                # Always apply properties — including for newly created batteries.
                # With elif, a new battery received no entityUpdate on its first packData
                # message, so HA entities were never created until the *next* poll cycle
                # (every 60 s).  This caused batteries to be invisible after a failed
                # initial httpGet (e.g. brief WiFi outage at startup).
                if bat and b:
                    for key, value in b.items():
                        if key != "sn":
                            bat.entityUpdate(key, value)

            # Recalculate total capacity after every packData update
            # (covers both new batteries and potential pack changes)
            self.kWh = sum(0 if b is None else b.kWh for b in self.batteries.values())
            self.totalKwh.update_value(self.kWh)
            self.availableKwh.update_value((self.electricLevel.asNumber - self.minSoc.asNumber) / 100 * self.kWh)

    def mqttMessage(self, topic: str, payload: Any) -> bool:
        try:
            match topic:
                case "properties/report":
                    asyncio.run_coroutine_threadsafe(self.mqttProperties(payload), self.hass.loop)
                    # self.mqttProperties(payload)

                case "register/replay":
                    _LOGGER.debug("Register replay for %s => %s", self.name, payload)
                    if self.mqtt is not None:
                        self.mqtt.publish(f"iot/{self.prodkey}/{self.deviceId}/register/replay", None, 1, True)

                case "time-sync":
                    return True

                case "properties/energy":
                    self.hemsState.update_value(1)
                    self.hemsStateUpdated = datetime.now()
                    self.setStatus()
                    return True

                case "event/device" | "event/error":
                    return True

                case "properties/read" | "function/invoke/reply" | "properties/read/reply" | "config" | "log" | "function/invoke":
                    return False

                # case "firmware/report":
                #     _LOGGER.info("Firmware report for %s => %s", self.name, payload)
                case _:
                    return False
        except Exception as err:
            _LOGGER.error(err)

        return True

    async def mqttSelect(self, _select: ZendureRestoreSelect, _value: Any) -> None:
        from .api import Api

        self.mqtt = None
        if self.lastseen != datetime.min:
            if self.connection.value == 0:
                await self.bleMqtt(Api.mqttCloud)
            elif self.connection.value == 1:
                await self.bleMqtt(Api.mqttLocal)

        _LOGGER.debug("Mqtt selected %s", self.name)

    @property
    def bleMac(self) -> str | None:
        if (conn := self.attr_device_info.get("connections", None)) is not None:
            for connection_type, mac_address in conn:
                if connection_type == "bluetooth":
                    return mac_address
        return None

    @staticmethod
    def _scanner_source(scanner_device: Any) -> str | None:
        """Extract scanner source identifier from a BluetoothScannerDevice-like object."""
        source = getattr(scanner_device, "source", None)
        if source:
            return str(source)

        if scanner := getattr(scanner_device, "scanner", None):
            source = getattr(scanner, "source", None)
            if source:
                return str(source)

        if service_info := getattr(scanner_device, "service_info", None):
            source = getattr(service_info, "source", None)
            if source:
                return str(source)

        return None

    @staticmethod
    def _scanner_ble_device(scanner_device: Any) -> Any | None:
        """Extract BLEDevice from a BluetoothScannerDevice-like object."""
        device = getattr(scanner_device, "ble_device", None)
        if device is not None:
            return device

        device = getattr(scanner_device, "device", None)
        if device is not None:
            return device

        if service_info := getattr(scanner_device, "service_info", None):
            device = getattr(service_info, "device", None)
            if device is not None:
                return device

        return None

    def ble_sources(self) -> list[str]:
        """Get available Bluetooth source identifiers from Home Assistant."""
        sources: set[str] = set()
        ble_mac = self.bleMac

        # Prefer scanner sources for this specific device.
        try:
            if ble_mac and (scanner_devices_by_address := getattr(bluetooth, "async_scanner_devices_by_address", None)):
                for scanner_device in scanner_devices_by_address(self.hass, ble_mac, True):
                    if source := self._scanner_source(scanner_device):
                        sources.add(source)
        except Exception as err:
            _LOGGER.debug("Could not read bluetooth scanner sources for %s: %s", self.name, err)

        # Fallback: derive sources from all discovered connectable advertisements.
        try:
            if discovered_service_info := getattr(bluetooth, "async_discovered_service_info", None):
                for info in discovered_service_info(self.hass, True):
                    if source := getattr(info, "source", None):
                        sources.add(str(source))
        except Exception as err:
            _LOGGER.debug("Could not derive bluetooth sources for %s: %s", self.name, err)

        return sorted(sources)

    def ble_device_from_source(self, ble_mac: str, source: str) -> Any | None:
        """Return a BLEDevice for an address constrained to a specific scanner source."""
        if scanner_devices_by_address := getattr(bluetooth, "async_scanner_devices_by_address", None):
            try:
                for scanner_device in scanner_devices_by_address(self.hass, ble_mac, True):
                    if self._scanner_source(scanner_device) != source:
                        continue
                    if device := self._scanner_ble_device(scanner_device):
                        return device
            except Exception as err:
                _LOGGER.debug("Could not get BLE device for %s on source %s: %s", self.name, source, err)

        return None

    def ble_adapter_options(self) -> dict[int, str]:
        """Build selectable BLE adapter/source options for this device."""
        options = {0: "auto"}
        for idx, source in enumerate(self.ble_sources(), start=1):
            options[idx] = source
        return options

    def selected_ble_source(self) -> str | None:
        """Return configured BLE source for this device or None for auto selection."""
        if self.bleAdapter is None:
            return None

        self.bleAdapter.setDict(self.ble_adapter_options())
        source = self.bleAdapter.current_option
        return None if source in (None, "", "auto") else str(source)

    async def bleMqtt(self, mqtt: mqtt_client.Client) -> bool:
        """Set the MQTT server for the device via BLE."""
        from .api import Api

        msg: str | None = None
        try:
            if Api.wifipsw == "" or Api.wifissid == "":
                msg = "No WiFi credentials or connections found"
                return False

            if (ble_mac := self.bleMac) is None:
                msg = "No BLE MAC address available"
                return False

            # get the bluetooth device
            ble_source = self.selected_ble_source()
            device = None
            if ble_source is not None:
                device = self.ble_device_from_source(ble_mac, ble_source)

            if device is None:
                device = bluetooth.async_ble_device_from_address(self.hass, ble_mac, True)

            if device is None:
                msg = f"BLE device {ble_mac} not found"
                if ble_source is not None:
                    msg += f" on source {ble_source}"
                return False

            try:
                _LOGGER.info("Set mqtt %s to %s", self.name, mqtt.host)
                if establish_connection is not None:
                    client = await establish_connection(BleakClient, device, self.name)
                else:
                    client = BleakClient(device)
                    await client.connect()

                try:
                    await self.bleCommand(
                        client,
                        {
                            "iotUrl": mqtt.host,
                            "messageId": 1002,
                            "method": "token",
                            "password": Api.wifipsw,
                            "ssid": Api.wifissid,
                            "timeZone": self.timezone_offset(),
                            "token": "abcdefgh",
                        },
                    )

                    await self.bleCommand(
                        client,
                        {
                            "messageId": 1003,
                            "method": "station",
                        },
                    )
                finally:
                    # Ensure stale BLE sessions do not leak if command execution fails unexpectedly.
                    if client.is_connected:
                        await client.disconnect()
            except TimeoutError:
                msg = "Timeout when trying to connect to the BLE device"
                _LOGGER.warning(msg)
            except (AttributeError, BleakError) as err:
                msg = f"Could not connect to {self.name}: {err}"
                _LOGGER.warning(msg)
            except Exception as err:
                msg = f"BLE error: {err}"
                _LOGGER.warning(msg)
            else:
                self.mqtt = mqtt
                if self.zendure is not None:
                    self.zendure.loop_stop()
                    self.zendure.disconnect()
                    self.zendure = None

                self.mqttPublish(self.topic_read, {"properties": ["getAll"]}, self.mqtt)
                self.setStatus()

                return True
            return False

        finally:
            if msg is not None:
                msg = f"Error setting the MQTT server on {self.name} to {mqtt.host}, {msg}"
            else:
                msg = f"Changing the MQTT server on {self.name} to {mqtt.host} was successful"

            persistent_notification.async_create(self.hass, (msg), "Zendure", "zendure_ha")

            _LOGGER.info("BLE update ready")

    def timezone_offset(self) -> str:
        """Time zone of Home Assistant as the device expects it, for example GMT+02:00 (follows summer time)."""
        offset = dt_util.now().utcoffset() or timedelta(0)
        minutes = int(offset.total_seconds() // 60)
        hours, mins = divmod(abs(minutes), 60)
        return f"GMT{'+' if minutes >= 0 else '-'}{hours:02d}:{mins:02d}"

    async def bleCommand(self, client: BleakClient, command: Any) -> None:
        try:
            self._messageid += 1
            payload = json.dumps(command, default=lambda o: o.__dict__)
            # never write the Wi-Fi password to the log
            _LOGGER.info("BLE command: %s => %s", self.name, json.dumps({k: ("***" if k in {"password", "token"} else v) for k, v in command.items()}, default=lambda o: o.__dict__))
            await client.write_gatt_char(SF_COMMAND_CHAR, bytearray(payload.encode("utf-8")), response=False)
        except Exception as err:
            _LOGGER.warning("BLE error: %s", err)
            raise  # the caller reports the failure, otherwise a failed provisioning looks like a success

    async def power_get(self) -> bool:
        if self.lastseen < datetime.now():
            self.lastseen = datetime.min
            self.setStatus()

        self.actualKwh = self.availableKwh.asNumber

        if not self.online or self.socSet.asNumber == 0 or self.kWh == 0:
            self.state = DeviceState.OFFLINE
        elif self.socLimit.asInt == SmartMode.SOCFULL or self.electricLevel.asInt >= self.socSet.asNumber:
            self.state = DeviceState.SOCFULL
        elif self.socLimit.asInt == SmartMode.SOCEMPTY or self.electricLevel.asInt <= self.minSoc.asNumber:
            self.state = DeviceState.SOCEMPTY
        else:
            self.state = DeviceState.INACTIVE

        if self.state == DeviceState.OFFLINE:
            self.slew.reset()
            self.commandPower.update_value(0)
        else:
            measured = self.homeOutput.asInt - self.homeInput.asInt
            self.slew.sync(measured)
            await self._follow_battery_limit(measured)
        self.slewActive.update_value(1 if self.slew.pending else 0)

        return self.state != DeviceState.OFFLINE

    async def _follow_battery_limit(self, measured: int) -> None:
        """A full battery stops charging (an empty one stops discharging) on its own, whatever the command says.

        The slew limiter then continues from the measured power, and the old limit is cleared with one 0 W command
        (RAM). Without this the device would jump back to the old limit (up to 2400 W at once) as soon as the SoC
        drops below the limit again, and the next peak would wait for a direction change that does not happen.
        """
        if self.pwr_offgrid != 0:
            return
        if self.state == DeviceState.SOCFULL and measured > -ZERO_BAND and (self.slew.current <= -ZERO_BAND or self.limitInput.asInt > 0):
            self.slew.follow(measured)
            if self.limitInput.asInt > 0:
                await self.charge(0)
        elif self.state == DeviceState.SOCEMPTY and measured < ZERO_BAND and (self.slew.current >= ZERO_BAND or self.limitOutput.asInt > 0):
            self.slew.follow(measured)
            if self.limitOutput.asInt > 0:
                await self.discharge(0)

    async def charge(self, _power: int) -> int:
        """Set the power output/input."""
        return 0

    async def power_charge(self, power: int) -> int:
        """Set charge power (negative value), limited by the slew rate."""
        power = self.slew.limit(min(0, max(power, self.charge_limit)))
        if power > 0:
            # still ramping down a discharge: never charge before the output reached 0 W
            await self._apply_discharge(power)
            return 0
        return await self._apply_charge(power)

    async def _apply_charge(self, power: int) -> int:
        """power is here a negative value, but homeInput and homeOutput are always positive"""
        self.commandPower.update_value(power)
        if abs(power + self.homeInput.asInt - self.homeOutput.asInt) <= SmartMode.POWER_TOLERANCE:
            _LOGGER.debug("Power charge %s => no action [power %s]", self.name, power)
            return -self.homeInput.asInt
        self.commandCount.update_value(self.commandCount.asInt + 1)
        self._off_sent = False
        return await self.charge(power)

    async def discharge(self, _power: int) -> int:
        """Set the power output/input."""
        return 0

    async def power_discharge(self, power: int, ready: bool = False) -> int:
        """Set discharge power, limited by the slew rate. ``ready`` marks the start of the device kept ready for peaks."""
        power = self.slew.limit(max(0, min(power, self.discharge_limit)), ready=ready)
        if power < 0:
            # still ramping down a charge: never discharge before the input reached 0 W
            await self._apply_charge(power)
            return 0
        return await self._apply_discharge(power)

    async def _apply_discharge(self, power: int) -> int:
        self.commandPower.update_value(power)
        if abs(power - self.homeOutput.asInt + self.homeInput.asInt) <= SmartMode.POWER_TOLERANCE:
            _LOGGER.debug("Power discharge %s => no action [power %s]", self.name, power)
            return self.homeOutput.asInt
        self.commandCount.update_value(self.commandCount.asInt + 1)
        self._off_sent = False
        return await self.discharge(power)

    async def power_off_ramped(self) -> bool:
        """Ramp the power down to 0 W in steps, then switch off. Returns True while still ramping."""
        if not self.slew.enabled or not self.online:
            self.slew.reset()
            await self.switch_off()
            return False

        self.slew.sync(self.homeOutput.asInt - self.homeInput.asInt)
        if abs(self.slew.current) >= ZERO_BAND and (power := self.slew.limit(0, hold=False)) != 0:
            _LOGGER.debug("Ramping down %s => %sW", self.name, power)
            self.commandPower.update_value(power)
            await (self.charge(power) if power < 0 else self.discharge(power))
            self.slewActive.update_value(1)
            return True

        self.slew.reset()
        self.slewActive.update_value(0)
        self.commandPower.update_value(0)
        await self.switch_off()
        return False

    async def switch_off(self) -> None:
        """Switch the device off, but only once: do not repeat a command the device already got."""
        if self._off_sent and abs(self.slew.current) < ZERO_BAND:
            return
        self._off_sent = True
        await self.power_off()

    async def power_off(self) -> None:
        """Set the power off."""

    @property
    def online(self) -> bool:
        """Check if device is online."""
        return self.connectionStatus.asInt >= SmartMode.CONNECTED

    @property
    def pwr_offgrid(self) -> int:
        """Get the offgrid power."""
        return 0


class ZendureLegacy(ZendureDevice):
    """Zendure Legacy class for devices."""

    def __init__(self, hass: HomeAssistant, deviceId: str, name: str, model: str, definition: dict[str, str], parent: str | None = None) -> None:
        """Initialize Device."""
        super().__init__(hass, deviceId, name, model, definition, parent)
        self.connection = ZendureRestoreSelect(self, "connection", {0: "cloud", 1: "local"}, self.mqttSelect, 0)
        self.mqttReset = ZendureButton(self, "mqttReset", self.button_press)
        self.bleAdapter = ZendureRestoreSelect(self, "bleAdapter", self.ble_adapter_options(), self.bleAdapterSelect, 0)

    async def bleAdapterSelect(self, _select: ZendureRestoreSelect, _value: Any) -> None:
        # Refresh available sources whenever selection changes or is restored.
        if self.bleAdapter is not None:
            self.bleAdapter.setDict(self.ble_adapter_options())

    async def button_press(self, button: ZendureButton) -> None:
        from .api import Api

        match button.translation_key:
            case "mqtt_reset":
                _LOGGER.info("Resetting MQTT for %s", self.name)
                await self.bleMqtt(Api.mqttCloud if self.connection.value == 0 else Api.mqttLocal)

    async def dataRefresh(self, _update_count: int) -> None:
        """Refresh the device data."""
        from .api import Api

        if self.lastseen != datetime.min:
            # a completely idle device is refreshed every third minute (the time-out is 5 minutes)
            if self.idle and _update_count % 3 != 0:
                return
            self.mqttPublish(self.topic_read, {"properties": ["getAll"]}, self.mqtt)
        else:
            self.mqttPublish(self.topic_read, {"properties": ["getAll"]}, Api.mqttCloud)
            self.mqttPublish(self.topic_read, {"properties": ["getAll"]}, Api.mqttLocal)

    def mqttMessage(self, topic: str, payload: Any) -> bool:
        if topic == "register/replay":
            _LOGGER.debug("Register replay for %s => %s", self.name, payload)
            return True

        return super().mqttMessage(topic, payload)


class HttpStatusError(Exception):
    """The device answered with an HTTP status other than 200."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP status {status}")
        self.status = status


class ZendureZenSdk(ZendureDevice):
    """Zendure Zen SDK class for devices.

    The local zenSDK runs on a small ESP32 with very little RAM, so every request is serialized
    per device, spaced at least Http.MIN_INTERVAL apart, and a device that does not answer is left
    alone for a while (back-off) instead of being hammered.
    """

    # these properties are written with smartMode=1: they do not reach the flash of the device
    RAM_PROPERTIES = frozenset({"inputLimit", "outputLimit", "acMode"})

    def __init__(self, hass: HomeAssistant, deviceId: str, name: str, model: str, definition: dict[str, str], parent: str | None = None) -> None:
        """Initialize Device."""
        self.session = async_get_clientsession(hass, verify_ssl=False)
        self._http_errors: set[str] = set()
        self._http_lock = asyncio.Lock()
        self._http_last = 0.0
        self._http_failures = 0
        self._http_retry_after = 0.0
        self._http_issue = False
        self._poll_last = -Http.POLL_IDLE
        super().__init__(hass, deviceId, name, model, definition, parent)
        self.connection = ZendureRestoreSelect(self, "connection", {0: "cloud", 2: "zenSDK"}, self.mqttSelect, 0)
        self.httpid = 0
        self.httpErrors = ZendureSensor(self, "httpErrors", None, None, None, "total_increasing", 0, state=0, category=EntityCategory.DIAGNOSTIC)
        self.ipAddressSensor = ZendureSensor(self, "ipAddress", state=self.ipAddress, category=EntityCategory.DIAGNOSTIC)

    def set_address(self, address: str) -> None:
        """Change the address of the device (manual override or discovery)."""
        if address and address != self.ipAddress:
            _LOGGER.info("Address of %s changed from %s to %s", self.name, self.ipAddress, address)
            self.ipAddress = address
            self.ipAddressSensor.update_value(address)

    async def mqttSelect(self, select: Any, _value: Any) -> None:
        from .api import Api

        self.mqtt = None
        match select.value:
            case 0:
                # cloud: listen to the cloud topics of this device again
                Api.mqttCloud.subscribe(f"/{self.prodkey}/{self.deviceId}/#")
                Api.mqttCloud.subscribe(f"iot/{self.prodkey}/{self.deviceId}/#")

            case 2:
                # zenSDK: the device is reached over HTTP, stop listening to the cloud topics
                Api.mqttCloud.unsubscribe(f"/{self.prodkey}/{self.deviceId}/#")
                Api.mqttCloud.unsubscribe(f"iot/{self.prodkey}/{self.deviceId}/#")

        _LOGGER.debug("Mqtt selected %s", self.name)

    def _is_ram_property(self, name: str) -> bool:
        return name in self.RAM_PROPERTIES

    def _write_properties(self, name: str, value: Any) -> dict[str, Any]:
        return {name: value, "smartMode": 1} if name in self.RAM_PROPERTIES else {name: value}

    async def _send_properties(self, properties: dict[str, Any]) -> bool:
        if self.online and self.connection.value == 0:
            return await super()._send_properties(properties)
        return await self.httpPost("properties/write", {"properties": properties})

    # ------------------------------------------------------------------------------------------
    # polling
    # ------------------------------------------------------------------------------------------
    async def poll(self, force: bool = False) -> None:
        """Read the report of the device; a completely idle device is read less often."""
        now = monotonic()
        if not force and now - self._poll_last < (Http.POLL_IDLE if self.idle else Http.POLL_ACTIVE):
            return
        self._poll_last = now
        if json := await self.httpGet("properties/report"):
            await self.mqttProperties(json)

    async def dataRefresh(self, update_count: int) -> None:
        if (update_count == 0 and not self.online) or self.connection.value == SmartMode.ZENSDK:
            await self.poll(force=update_count == 0)

    async def power_get(self) -> bool:
        """Get the current power."""
        if self.connection.value != 0:
            await self.poll()
        result = await super().power_get()
        await self._flash_tick()
        return result

    # ------------------------------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------------------------------
    async def charge(self, power: int, _off: bool = False) -> int:
        """Set charge power."""
        _LOGGER.debug("Power charge %s => %s", self.name, power)
        if power == -SmartMode.POWER_START and self.limitInput.asInt >= SmartMode.POWER_START and self.homeInput.asInt == 0:
            power = -min(self.limitInput.asInt + 4, 2 * SmartMode.POWER_START)
            _LOGGER.debug("Power charge kickstart %s => %s", self.name, power)
        await self._power_command(1, 0, -power)
        return power

    async def discharge(self, power: int) -> int:
        _LOGGER.debug("Power discharge %s => %s", self.name, power)
        if power == SmartMode.POWER_START and self.limitOutput.asInt >= SmartMode.POWER_START and self.homeOutput.asInt == 0:
            power = min(self.limitOutput.asInt + 4, 2 * SmartMode.POWER_START)
            _LOGGER.debug("Power discharge kickstart %s => %s", self.name, power)
        await self._power_command(2, power, 0)
        return power

    async def power_off(self) -> None:
        """Set the power off. The first time the safe 0 W state is also stored in the flash of the device."""
        await self._power_command(2, 0, 0, persist=True)

    async def _power_command(self, ac_mode: int, output: int, input_: int, persist: bool = False) -> None:
        """Send a power command.

        smartMode=1 keeps the command in RAM, so the constant stream of power changes does not wear out
        the flash of the device. Only the safe 0 W state is stored in flash (smartMode=0), at most once:
        when the manager is switched off or after the device has been idle for Flash.IDLE_PERSIST seconds.
        After a reboot the device then starts at 0 W instead of at the last (high) setpoint.
        """
        persist = persist and self.persist_safe_state and self.pwr_offgrid == 0 and self._flash_safe is not True
        properties = {"smartMode": 0 if persist else 1, "acMode": ac_mode, "outputLimit": output, "inputLimit": input_}
        if await self.doCommand({"properties": properties}) and persist:
            self._flash_safe = True
            self._flash_last = monotonic()
            self._count_flash_write()
            _LOGGER.debug("Safe 0 W state stored in the flash of %s", self.name)

    async def _flash_tick(self) -> None:
        """Store the safe 0 W state once, after a long idle period (needs one flash write instead of one per stop)."""
        now = monotonic()
        if not self.online or self.pwr_offgrid != 0 or not self.idle:
            self._idle_since = None
            return
        if self._idle_since is None:
            self._idle_since = now
        elif now - self._idle_since >= Flash.IDLE_PERSIST and self._flash_safe is not True and now - self._flash_last >= Flash.MIN_INTERVAL:
            await self._power_command(2, 0, 0, persist=True)

    async def doCommand(self, command: Any) -> bool:
        if self.connection.value != 0:
            return await self.httpPost("properties/write", command)
        self.mqttPublish(self.topic_write, command, self.mqtt)
        return self.mqtt is not None  # without a connection the command is lost, do not count it as stored

    # ------------------------------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------------------------------
    async def _http(self, method: str, path: str, command: Any = None) -> tuple[bool, Any]:
        """One serialized request. Returns (success, parsed json or None)."""
        async with self._http_lock:
            now = monotonic()
            if now < self._http_retry_after:
                return False, None  # back-off: the device did not answer, give it some time

            if (wait := self._http_last + Http.MIN_INTERVAL - now) > 0:
                await asyncio.sleep(wait)
            kind = method.lower()
            try:
                url = f"http://{self.ipAddress}/{path}"
                async with self.session.request(method, url, json=command, headers=CONST_HEADER, timeout=CONST_TIMEOUT) as response:
                    if response.status != HTTP_OK:
                        raise HttpStatusError(response.status)
                    body = await response.text()
                payload = json.loads(body) if method == "GET" else None
            except Exception as err:
                self._http_failed(kind, err)
                return False, None
            finally:
                self._http_last = monotonic()

            self._http_succeeded(kind)
            return True, payload

    def _http_failed(self, kind: str, err: Exception) -> None:
        self._http_failures += 1
        self._http_retry_after = monotonic() + min(Http.BACKOFF_MAX, Http.BACKOFF_BASE * 2 ** (self._http_failures - 1))
        self._logHttpError(kind, err)
        if self._http_failures >= Http.OFFLINE_AFTER:
            self.lastseen = datetime.min
        if self._http_failures >= Http.ISSUE_AFTER:
            self._update_issue(True)

    def _http_succeeded(self, kind: str) -> None:
        self._http_errors.discard(kind)
        if self._http_failures:
            _LOGGER.info("%s answers again after %s failed requests", self.name, self._http_failures)
        self._http_failures = 0
        self._http_retry_after = 0.0
        self._update_issue(False)

    def _update_issue(self, failing: bool) -> None:
        """Raise a repair issue while the device does not answer, remove it when it does."""
        issue_id = f"zensdk_unreachable_{self.snNumber.lower()}"
        if failing and not self._http_issue:
            self._http_issue = True
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="zensdk_unreachable",
                translation_placeholders={"name": self.name, "address": self.ipAddress},
            )
        elif not failing and self._http_issue:
            self._http_issue = False
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def httpGet(self, url: str, key: str | None = None) -> dict[str, Any]:
        ok, payload = await self._http("GET", url)
        if not ok or not isinstance(payload, dict):
            return {}
        self.lastseen = datetime.now()
        return payload if key is None else payload.get(key, {})

    def _logHttpError(self, kind: str, err: Exception) -> None:
        """Log the first failure of a request type as error, repeats at debug level until it succeeds again."""
        level = logging.DEBUG if kind in self._http_errors else logging.ERROR
        self._http_errors.add(kind)
        self.httpErrors.update_value(self.httpErrors.asInt + 1)
        _LOGGER.log(level, "%s for %s during http%s%s", type(err).__name__, self.name, kind.capitalize(), f": {err}" if str(err) else "!")

    async def httpPost(self, url: str, command: Any) -> bool:
        self.httpid += 1
        command["id"] = self.httpid
        command["sn"] = self.snNumber
        ok, _ = await self._http("POST", url, command)
        return ok


@dataclass
class DeviceSettings:
    device_id: str
    fuseGroup: str
    limitCharge: int
    limitDischarge: int
    maxSolar: int
    kWh: float = 0.0
    socSet: float = 100
    minSoc: float = 0
