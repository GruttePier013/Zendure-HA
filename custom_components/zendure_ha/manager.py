"""Coordinator for Zendure integration."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import traceback
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from time import monotonic, perf_counter
from typing import Any

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.auth.providers import homeassistant as auth_ha
from homeassistant.components import bluetooth, persistent_notification
from homeassistant.components.number import NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .api import Api, mqtt_password
from .binary_sensor import ZendureBinarySensor
from .const import (
    CONF_AUTO_MQTT_USER,
    CONF_DEVICE_IPS,
    CONF_FLASH_SAFE,
    CONF_FLIP_DELAY,
    CONF_MANUAL_INTERVAL,
    CONF_MIN_RUN,
    CONF_P1_STDDEV_MIN,
    CONF_P1_HOMEWIZARD,
    CONF_P1_RECORD,
    CONF_P1_TOKEN,
    CONF_P1METER,
    CONF_PEAK_LIMIT,
    CONF_POWER_START,
    CONF_POWER_TOLERANCE,
    CONF_EXPORT_BIAS,
    CONF_FILL_LEVEL,
    CONF_READY_RATE,
    CONF_ROLE_MARGIN,
    CONF_SLEW_DOWN,
    CONF_SLEW_ENABLED,
    CONF_SLEW_UP,
    CONF_TIMEFAST,
    CONF_TIMEZERO,
    CONTROL_DEFAULTS,
    DOMAIN,
    DeviceState,
    ManagerMode,
    ManagerState,
    Http,
    SmartMode,
)
from .device import DeviceSettings, ZendureDevice, ZendureLegacy, ZendureZenSdk
from .p1direct import HomeWizardP1, homewizard_entry
from .p1record import P1Recorder
from .discovery import SEARCH_INTERVAL, ZendureDiscovery, search_subnet
from .dispatch import DEFAULT_ROLE_MARGIN, P1Tracker, RoleSelector, Volatility, peak_setpoint
from .entity import EntityDevice, quiet
from .fusegroup import FuseGroup
from .number import ZendureRestoreNumber
from .select import ZendureRestoreSelect, ZendureSelect
from .sensor import ZendureSensor
from .simlog import SimulationLog
from .slew import DEFAULT_DOWN, DEFAULT_FLIP_DELAY, DEFAULT_MIN_RUN, DEFAULT_READY_UP, DEFAULT_UP

SCAN_INTERVAL = timedelta(seconds=60)

_LOGGER = logging.getLogger(__name__)

type ZendureConfigEntry = ConfigEntry[ZendureManager]


# activity of a role holder, shown in the role sensors (in the language of Home Assistant)
ACTIVITY_LABELS = {
    "en": {"charging": "charging", "discharging": "discharging", "standby": "standby", "reserve": "standby, cloud reserve", "not_responding": "not responding"},
    "nl": {"charging": "opladen", "discharging": "ontladen", "standby": "stand-by", "reserve": "stand-by, wolkenreserve", "not_responding": "reageert niet"},
    "de": {"charging": "lädt", "discharging": "entlädt", "standby": "Standby", "reserve": "Standby, Wolkenreserve", "not_responding": "antwortet nicht"},
    "fr": {"charging": "charge", "discharging": "décharge", "standby": "veille", "reserve": "veille, réserve nuages", "not_responding": "ne répond pas"},
    "pl": {"charging": "ładuje", "discharging": "rozładowuje", "standby": "czuwanie", "reserve": "czuwanie, rezerwa chmur", "not_responding": "nie odpowiada"},
}


class ZendureManager(DataUpdateCoordinator[None], EntityDevice):
    """Class to regular update devices."""

    simulation: bool = False
    fill_level: int = SmartMode.FILL_LEVEL
    charge_release_since: datetime | None = None
    discharge_release_since: datetime | None = None
    p1direct: HomeWizardP1 | None = None  # HomeWizard P1 meter read directly (optional)
    p1record: P1Recorder | None = None  # recording of P1 every second until midnight (optional)
    cycle_errors: int = 0  # control cycles that failed or did not finish
    last_cycle_error: str | None = None
    last_cycle_stuck: str | None = None  # where the last cycle that did not finish was waiting
    p1_values: int = 0  # P1 values handled since the start
    _search_task: asyncio.Task | None = None
    crossflow_cycles: int = 0
    crossflow_since: datetime | None = None
    crossflow_warned: bool = False
    cycle_total: int = 0
    export_bias: int = 100
    cloud_reserve: str | None = None  # the device kept out of charging in cloud mode

    def __init__(self, hass: HomeAssistant, entry: ZendureConfigEntry) -> None:
        """Initialize Zendure Manager."""
        super().__init__(hass, _LOGGER, name="Zendure Manager", update_interval=SCAN_INTERVAL, config_entry=entry)
        EntityDevice.__init__(self, hass, "Zendure Manager", "Zendure Manager")
        self.api = Api()
        self.devices: list[ZendureDevice] = []
        self.fuseGroups: list[FuseGroup] = []
        self.operation: ManagerMode = ManagerMode.OFF
        self.zero_next = datetime.min
        self.zero_fast = datetime.min
        self.check_reset = datetime.min
        self.p1meterEvent: Callable[[], None] | None = None
        self.p1direct: HomeWizardP1 | None = None
        self.p1meter: str | None = None
        self.p1_last = 0
        self._ip_overrides: dict[str, str] = {}
        self.last_cycle: datetime | None = None
        self._followup: asyncio.TimerHandle | None = None
        self.p1tracker = P1Tracker()
        self.roles = RoleSelector(DEFAULT_ROLE_MARGIN)
        self.role_margin = DEFAULT_ROLE_MARGIN
        self.peak_limit = 0
        self.manual_interval = float(SmartMode.MANUAL_INTERVAL)
        self.simlog: SimulationLog | None = None
        self.discovery: ZendureDiscovery | None = None
        self._search_last = -SEARCH_INTERVAL  # monotonic time of the last search for a device on the network
        self._search_task: asyncio.Task | None = None
        self._mqtt_down = {"cloud": 0, "local": 0}
        self.p1_factor = 1
        self.update_count = 0

        self.charge: list[ZendureDevice] = []
        self.charge_limit = 0
        self.charge_optimal = 0
        self.charge_time = datetime.max
        self.charge_last = datetime.min
        self.charge_weight = 0

        self.discharge: list[ZendureDevice] = []
        self.discharge_bypass = 0
        self.discharge_produced = 0
        self.discharge_limit = 0
        self.discharge_optimal = 0
        self.discharge_weight = 0

        self.idle: list[ZendureDevice] = []
        self.idle_lvlmax = 0
        self.idle_lvlmin = 0
        self.produced = 0
        self.pwr_low = 0

    async def loadDevices(self) -> None:
        if self.config_entry is None:
            return
        data = await Api.Connect(self.hass, dict(self.config_entry.data), True)
        if not data or (mqtt := data.get("mqtt")) is None:
            # Home Assistant retries the setup with an increasing delay
            raise ConfigEntryNotReady("The Zendure API is not reachable and there is no cached device list yet")

        # get version number from integration
        integration = await async_get_integration(self.hass, DOMAIN)
        if integration is None:
            _LOGGER.error("Integration not found for domain: %s", DOMAIN)
            return
        self.attr_device_info["sw_version"] = integration.manifest.get("version", "unknown")
        self.simlog = SimulationLog(self.hass)

        self.operationmode = (
            ZendureRestoreSelect(
                self,
                "Operation",
                {0: "off", 1: "manual", 2: "smart", 3: "discharge", 4: "charge", 5: "store_solar"},
                self.update_operation,
                renamed={"smart_discharging": "discharge", "smart_charging": "charge"},  # option names up to 1.5.4
            ),
        )
        self.operationstate = ZendureSensor(self, "operation_state")
        self.manualpower = ZendureRestoreNumber(self, "manual_power", self.update_manualpower, None, "W", "power", 12000, -12000, NumberMode.BOX, True)
        self.availableKwh = ZendureSensor(self, "available_kwh", None, "kWh", "energy_storage", None, 1)
        self.totalKwh = ZendureSensor(self, "total_kwh", None, "kWh", "energy_storage", "measurement", 2)
        self.power = ZendureSensor(self, "power", None, "W", "power", "measurement", 0)
        self.globalSoc = ZendureSensor(self, "global_soc", None, "%", "battery", "measurement", 1)

        # diagnostics
        diag = EntityCategory.DIAGNOSTIC
        self.cycleDuration = ZendureSensor(self, "cycle_duration", None, "ms", "duration", "measurement", 0, category=diag)
        self.cycleInterval = ZendureSensor(self, "cycle_interval", None, "s", "duration", "measurement", 1, category=diag)
        self.cycleCount = ZendureSensor(self, "cycle_count", None, None, None, "total_increasing", 0, state=0, category=diag)
        self.lastCycle = ZendureSensor(self, "last_cycle", None, None, "timestamp", None, category=diag)
        self.p1Value = ZendureSensor(self, "p1_value", None, "W", "power", "measurement", 0, category=diag)
        self.dispatchSetpoint = ZendureSensor(self, "dispatch_setpoint", None, "W", "power", "measurement", 0, category=diag)
        self.roleDischarge = ZendureSensor(self, "role_discharge", category=diag)
        self.roleCharge = ZendureSensor(self, "role_charge", category=diag)
        # updated every cycle: disabled by default, every state change is a row in the recorder database
        quiet(self.cycleDuration, self.cycleInterval, self.cycleCount, self.lastCycle, self.p1Value, self.dispatchSetpoint)
        self.devicesOnline = ZendureSensor(self, "devices_online", None, None, None, "measurement", 0, state=0, category=diag)
        self.devicesCharging = ZendureSensor(self, "devices_charging", None, None, None, "measurement", 0, state=0, category=diag)
        self.devicesDischarging = ZendureSensor(self, "devices_discharging", None, None, None, "measurement", 0, state=0, category=diag)
        self.devicesIdle = ZendureSensor(self, "devices_idle", None, None, None, "measurement", 0, state=0, category=diag)
        self.devicesRamping = ZendureSensor(self, "devices_ramping", None, None, None, "measurement", 0, state=0, category=diag)
        self.devicesNotResponding = ZendureSensor(self, "devices_not_responding", None, None, None, "measurement", 0, state=0, category=diag)
        self.mqttCloudConnected = ZendureBinarySensor(self, "mqtt_cloud", None, "connectivity", category=diag)
        self.cloudMode = ZendureBinarySensor(self, "cloud_mode", None, None, category=diag)
        self.mqttLocalConnected = ZendureBinarySensor(self, "mqtt_local", None, "connectivity", category=diag)

        # load devices
        for dev in data["deviceList"]:
            try:
                if (deviceId := dev["deviceKey"]) is None or (prodModel := dev["productModel"]) is None:
                    continue
                _LOGGER.info("Adding device: %s %s", dev.get("deviceName", prodModel), prodModel)
                _LOGGER.debug("Device definition (serial numbers are not logged): %s", {k: v for k, v in dev.items() if k not in {"snNumber", "deviceKey", "ip"}})

                init = Api.createdevice.get(prodModel.lower().replace(" ", ""), None)
                if init is None:
                    _LOGGER.info("Device %s is not supported!", prodModel)
                    continue

                # create the device and mqtt server
                device = init(self.hass, deviceId, dev.get("deviceName", prodModel), dev)
                device.discharge_start = device.discharge_limit // 10
                device.discharge_optimal = device.discharge_limit // 4
                Api.devices[deviceId] = device

                # Check if we should automatically manage MQTT users (opt-in)
                auto_mqtt = self.config_entry.data.get(CONF_AUTO_MQTT_USER, False)
                if auto_mqtt and Api.localServer is not None and Api.localServer != "":
                    try:
                        psw = mqtt_password(deviceId)
                        provider: auth_ha.HassAuthProvider = auth_ha.async_get_provider(self.hass)
                        credentials = await provider.async_get_or_create_credentials({"username": deviceId.lower()})
                        user = await self.hass.auth.async_get_user_by_credentials(credentials)
                        if user is None:
                            # Enforce local_only=True for technical MQTT accounts
                            user = await self.hass.auth.async_create_user(deviceId, group_ids=[GROUP_ID_USER], local_only=True)
                            await provider.async_add_auth(deviceId.lower(), psw)
                            await self.hass.auth.async_link_user(user, credentials)
                        else:
                            await provider.async_change_password(deviceId.lower(), psw)

                        _LOGGER.info("Managed MQTT user for device: %s", deviceId)

                    except Exception as err:
                        _LOGGER.error("Failed to manage MQTT user for %s: %s", deviceId, err)
                elif auto_mqtt:
                    _LOGGER.debug("Skipping auto MQTT user creation for %s: Local server not configured.", deviceId)

            except Exception as e:
                _LOGGER.error("Unable to create device %s!", e)
                _LOGGER.error(traceback.format_exc())

        self.devices = list(Api.devices.values())
        self.apply_settings(self.config_entry.data)
        _LOGGER.info("Loaded %s devices", len(self.devices))

        # mDNS discovery keeps the address of zenSDK devices up to date (a manual address always wins)
        self.discovery = ZendureDiscovery(self.hass, self._discovered, lambda: {d.snNumber: d.name for d in self.devices if isinstance(d, ZendureZenSdk)})
        await self.discovery.async_start()

        # initialize the api & p1 meter
        self.api.Init(self.config_entry.data, mqtt)
        await self.update_fusegroups()
        self.update_p1meter(self.config_entry.data.get(CONF_P1METER, "sensor.power_actual"), self.config_entry.data)
        await asyncio.sleep(1)  # allow other tasks to run

    async def update_fusegroups(self) -> None:
        _LOGGER.info("Update fusegroups")

        # updateFuseGroup callback
        async def updateFuseGroup(_entity: ZendureRestoreSelect, _value: Any) -> None:
            await self.update_fusegroups()

        fuseGroups: dict[str, FuseGroup] = {}
        for device in self.devices:
            try:
                if device.fuseGroup.onchanged is None:
                    device.fuseGroup.onchanged = updateFuseGroup

                fg: FuseGroup | None = None
                match device.fuseGroup.state:
                    case "owncircuit" | "group3600":
                        fg = FuseGroup(device.name, 3600, -3600)
                    case "group800":
                        fg = FuseGroup(device.name, 800, -1200)
                    case "group800_2400":
                        fg = FuseGroup(device.name, 800, -2400)
                    case "group1200":
                        fg = FuseGroup(device.name, 1200, -1200)
                    case "group2000":
                        fg = FuseGroup(device.name, 2000, -2000)
                    case "group2400":
                        fg = FuseGroup(device.name, 2400, -2400)
                    case "group4000":
                        fg = FuseGroup(device.name, 4000, -4000)
                    case "group5000":
                        fg = FuseGroup(device.name, 5000, -5000)
                    case "unused":
                        # only switch off, if Manager is used
                        if self.operation != ManagerMode.OFF:
                            await device.switch_off()
                        continue
                    case _:
                        _LOGGER.debug("Device %s has unsupported fuseGroup state: %s", device.name, device.fuseGroup.state)
                        continue

                if fg is not None:
                    fg.devices.append(device)
                    fuseGroups[device.deviceId] = fg
            except AttributeError as err:
                _LOGGER.error("Device %s missing fuseGroup attribute: %s", device.name, err)
            except Exception as err:
                _LOGGER.error("Unable to create fusegroup for device %s (%s): %s", device.name, device.deviceId, err, exc_info=True)

        # Update the fusegroups and select options for each device
        for device in self.devices:
            try:
                fusegroups: dict[Any, str] = {
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
                for deviceId, fg in fuseGroups.items():
                    if deviceId != device.deviceId:
                        fusegroups[deviceId] = f"Part of {fg.name} fusegroup"
                device.fuseGroup.setDict(fusegroups)
            except AttributeError as err:
                _LOGGER.error("Device %s missing fuseGroup attribute: %s", device.name, err)
            except Exception as err:
                _LOGGER.error("Unable to update fusegroup options for device %s (%s): %s", device.name, device.deviceId, err, exc_info=True)

        # Add devices to fusegroups
        for device in self.devices:
            if fg := fuseGroups.get(device.fuseGroup.value):
                device.fuseGrp = fg
                fg.devices.append(device)
            device.setStatus()

        # check if we can split fuse groups
        self.fuseGroups.clear()
        for fg in fuseGroups.values():
            if len(fg.devices) > 1 and fg.maxpower >= sum(d.discharge_limit for d in fg.devices) and fg.minpower <= sum(d.charge_limit for d in fg.devices):
                for d in fg.devices:
                    self.fuseGroups.append(FuseGroup(d.name, d.discharge_limit, d.charge_limit, [d]))
            else:
                for d in fg.devices:
                    d.fuseGrp = fg
                self.fuseGroups.append(fg)

    async def update_operation(self, entity: ZendureSelect, _operation: Any) -> None:
        operation = ManagerMode(entity.value)
        _LOGGER.info("Update operation: %s from: %s", operation, self.operation)

        self.operation = operation
        if self.p1meterEvent is not None:
            if operation != ManagerMode.OFF and (len(self.devices) == 0 or all(not d.online for d in self.devices)):
                # The mode is kept: at startup Home Assistant restores it before the devices are online,
                # it starts working as soon as a device is online.
                _LOGGER.info("No devices online yet, operation %s starts as soon as a device is online", operation.name)
                if self.hass.is_running:
                    persistent_notification.async_create(self.hass, "No devices online yet, the operation starts as soon as a device is online", "Zendure", "zendure_ha")
                return

            match self.operation:
                case ManagerMode.OFF:
                    await self.ramp_off()

                case ManagerMode.MANUAL:
                    # do not wait for the next P1 change, apply the manual power right away
                    await self.dispatch_manual()

    async def update_manualpower(self, _entity: ZendureRestoreNumber, _value: Any) -> None:
        """Apply a changed manual power value immediately when the manager is in manual mode."""
        await self.dispatch_manual()

    async def dispatch_manual(self) -> None:
        """Run a power update now, without waiting for the next P1 meter change (manual mode only)."""
        # zero_fast == datetime.max means an update is already running; it reads the manual power itself
        if not self.hass.is_running or self.operation != ManagerMode.MANUAL or self.zero_fast == datetime.max:
            return

        p1 = 0  # the P1 value is not used in manual mode, only for logging
        if self.p1direct is not None and self.p1direct.healthy:
            p1 = self.p1_last
        elif self.p1meter and (state := self.hass.states.get(self.p1meter)) is not None:
            try:
                p1 = int(self.p1_factor * float(state.state))
            except (ValueError, OverflowError):
                p1 = 0
        await self._run_power_update(p1, True, datetime.now())

    async def _async_update_data(self) -> None:

        def isBleDevice(device: ZendureDevice, si: bluetooth.BluetoothServiceInfoBleak) -> bool:
            for d in si.manufacturer_data.values():
                try:
                    if d is None or len(d) <= 1:
                        continue
                    sn = d.decode("utf8")[:-1]
                    if device.snNumber.endswith(sn):
                        _LOGGER.info("Found Zendure Bluetooth device: %s", si)
                        device.attr_device_info["connections"] = {("bluetooth", str(si.address))}
                        return True
                except Exception:  # noqa: S112
                    continue
            return False

        time = datetime.now()
        kwh = 0
        for device in self.devices:
            try:
                kwh += device.kWh
                if isinstance(device, ZendureLegacy) and device.bleMac is None:
                    for si in bluetooth.async_discovered_service_info(self.hass, False):
                        if isBleDevice(device, si):
                            break

                _LOGGER.debug("Update device: %s (%s)", device.name, device.deviceId)
                await device.dataRefresh(self.update_count)
                if device.hemsState.is_on and (time - device.hemsStateUpdated).total_seconds() > SmartMode.HEMSOFF_TIMEOUT:
                    device.hemsState.update_value(0)
                device.setStatus()
            except Exception as err:  # one device must not stop the refresh of the others
                _LOGGER.error("Unable to refresh %s: %s", device.name, err)
        self.update_count += 1
        self.totalKwh.update_value(kwh)
        self._check_mqtt()
        self._search_lost_devices()

        # Manually update the timer
        if self.hass and self.hass.loop.is_running():
            self._schedule_refresh()

    def update_p1meter(self, p1meter: str | None, data: Mapping[str, Any] | None = None) -> None:
        """Update the P1 meter sensor, and the HomeWizard P1 meter that is read directly (optional)."""
        data = data or {}
        host = str(data.get(CONF_P1_HOMEWIZARD) or "").strip() if p1meter else ""
        token = str(data.get(CONF_P1_TOKEN) or "").strip() or None
        if host and token is None:
            hw_host, hw_token = homewizard_entry(self.hass, p1meter)
            token = hw_token if hw_host == host else None  # the token of the HomeWizard integration (API v2)
        _LOGGER.debug("Updating P1 meter to: %s (HomeWizard: %s)", p1meter, host or "-")
        if self.p1direct is not None and (self.p1direct.host != host or self.p1direct.token != token):
            self.p1direct.stop()
            self.p1direct = None
        if host and self.p1direct is None:
            self.p1direct = HomeWizardP1(self.hass, host, token, self._p1_direct, self._homewizard_moved)
            self.p1direct.start()
        self.p1meter = p1meter
        self.p1_factor = 1
        if self.p1meterEvent:
            self.p1meterEvent()
        if p1meter:
            self.p1meterEvent = async_track_state_change_event(self.hass, [p1meter], self._p1_changed)
            if (entity := self.hass.states.get(p1meter)) is not None and entity.attributes.get("unit_of_measurement", "W") in ("kW", "kilowatt", "kilowatts"):
                self.p1_factor = 1000
        else:
            self.p1meterEvent = None

    def writeSimulation(self, time: datetime, p1: int) -> None:
        """Queue one line for the simulation log (written in batches, see simlog.py)."""
        if self.simlog is None:
            return
        header = (
            "Time;P1;Operation;Battery;Solar;Home;SetPoint;--;"
            + ";".join(
                f"bat;Prod;Home;{json.dumps(DeviceSettings(d.name, d.fuseGrp.name, d.charge_limit, d.discharge_limit, d.maxSolar, d.kWh, d.socSet.asNumber, d.minSoc.asNumber), default=vars)}"
                for d in self.devices
            )
            + "\n"
        )
        data = ""
        tbattery = tsolar = thome = 0
        for d in self.devices:
            tbattery += (pwr_battery := d.batteryOutput.asInt - d.batteryInput.asInt)
            tsolar += (pwr_solar := d.solarInput.asInt)
            thome += (pwr_home := d.homeOutput.asInt - d.homeInput.asInt)
            data += f";{pwr_battery};{pwr_solar};{pwr_home};{d.electricLevel.asInt}"

        self.simlog.add(f"{time};{p1};{self.operation};{tbattery};{tsolar};{thome};{self.manualpower.asNumber};" + data + "\n", header)

    def _update_p1_record(self, day: str) -> None:
        """Start or stop the P1 recording: it runs while the stored date is today."""
        today = dt_util.now().date().isoformat()
        if day and day != today and getattr(self, "config_entry", None) is not None and self.config_entry.data.get(CONF_P1_RECORD) == day:
            # Home Assistant was not running at midnight: switch the recording of that day off now
            self.hass.config_entries.async_update_entry(self.config_entry, data={**self.config_entry.data, CONF_P1_RECORD: ""})
        if day == today:
            if self.p1record is None:
                self.p1record = P1Recorder(self.hass, self._p1_record_done)
                self.p1record.start()
        elif self.p1record is not None:
            recorder, self.p1record = self.p1record, None
            self.hass.async_create_task(recorder.stop())

    def _p1_record_done(self) -> None:
        """Midnight: the recording switches itself off."""
        self.p1record = None
        if self.config_entry.data.get(CONF_P1_RECORD):
            self.hass.config_entries.async_update_entry(self.config_entry, data={**self.config_entry.data, CONF_P1_RECORD: ""})

    def _homewizard_moved(self) -> str | None:
        """The current address of the HomeWizard P1 meter according to the HomeWizard integration; stored when it changed."""
        host, _token = homewizard_entry(self.hass, self.p1meter)
        if host and host != self.config_entry.data.get(CONF_P1_HOMEWIZARD):
            self.hass.config_entries.async_update_entry(self.config_entry, data={**self.config_entry.data, CONF_P1_HOMEWIZARD: host})
        return host

    async def _p1_direct(self, p1: int) -> None:
        """A value read directly from the HomeWizard P1 meter (every second)."""
        if self.hass.is_running and not self.hass.is_stopping:
            await self._handle_p1(p1, "homewizard")

    async def _p1_changed(self, event: Event[EventStateChangedData]) -> None:
        # exit if there is nothing to do
        if not self.hass.is_running or self.hass.is_stopping or (new_state := event.data["new_state"]) is None:
            return
        # the HomeWizard P1 meter is read directly: the (slower) sensor is only the fallback
        if self.p1direct is not None and self.p1direct.healthy:
            return

        # The unit is read from every state: a sensor that was not available at startup, or that
        # changes its unit, must not give a factor 1000 error.
        self.p1_factor = 1000 if new_state.attributes.get("unit_of_measurement") in ("kW", "kilowatt", "kilowatts") else 1
        try:  # convert the state to a float
            p1 = int(self.p1_factor * float(new_state.state))
        except ValueError:
            return
        await self._handle_p1(p1, "sensor")

    async def _handle_p1(self, p1: int, source: str = "sensor") -> None:
        """Process one P1 value (W, positive = import), from the sensor or read directly from the meter."""
        self.p1_last = p1
        self.p1_values += 1
        if self.p1record is not None:
            self.p1record.add(p1, sum(d.homeOutput.asInt - d.homeInput.asInt for d in self.devices), source)

        # Get time & update simulation
        time = datetime.now()
        if ZendureManager.simulation:
            self.writeSimulation(time, p1)

        # Follow the P1 meter closely when matching; a fixed manual power or an off manager does not need that
        run, isFast = self.p1tracker.evaluate(p1, time, self.zero_fast, self.zero_next, SmartMode.P1_STDDEV_FACTOR, SmartMode.P1_STDDEV_MIN)
        if self.operation in (ManagerMode.MANUAL, ManagerMode.OFF):
            isFast = False
            run = run and time > self.zero_next
        if run:
            await self._run_power_update(p1, isFast, time)
        else:
            await self.fast_track(p1, time)

    async def _run_power_update(self, p1: int, isFast: bool, time: datetime) -> None:
        """Reset the distribution state and run one power update."""
        started = perf_counter()
        previous, self.last_cycle = self.last_cycle, time
        try:
            # prevent updates during power distribution changes
            self.zero_fast = datetime.max
            self.charge.clear()
            self.charge_limit = 0
            self.charge_optimal = 0
            self.charge_weight = 0
            self.discharge.clear()
            self.discharge_bypass = 0
            self.discharge_limit = 0
            self.discharge_optimal = 0
            self.discharge_produced = 0
            self.discharge_weight = 0
            self.idle.clear()
            self.idle_lvlmax = 0
            self.idle_lvlmin = 100
            self.produced = 0
            for fg in self.fuseGroups:
                fg.initPower = True
            # a control cycle normally takes well under a second; one that hangs would block all following cycles
            task = asyncio.current_task()
            watchdog = self.hass.loop.call_later(SmartMode.CYCLE_TIMEOUT - 0.5, self._cycle_stuck, task) if task is not None else None
            try:
                async with asyncio.timeout(SmartMode.CYCLE_TIMEOUT):
                    await self.powerChanged(p1, isFast, time)
            finally:
                if watchdog is not None:
                    watchdog.cancel()
        except TimeoutError:
            self.cycle_errors += 1
            self.last_cycle_error = f"{dt_util.now().isoformat(timespec='seconds')}: cycle did not finish within {SmartMode.CYCLE_TIMEOUT} s"
        except Exception as err:
            self.cycle_errors += 1
            self.last_cycle_error = f"{dt_util.now().isoformat(timespec='seconds')}: {type(err).__name__}: {err}"
            _LOGGER.error("Control cycle failed: %s\n%s", err, traceback.format_exc())
        finally:
            # always release the update lock, also when the task is cancelled
            ramping = sum(1 for d in self.devices if d.slew.pending)
            time = datetime.now()
            zero, fast = self.cadence()
            self.zero_next = time + timedelta(seconds=zero)
            self.zero_fast = time + timedelta(seconds=fast)

        # diagnostics
        self.cycleDuration.update_value(round((perf_counter() - started) * 1000))
        if previous is not None:
            self.cycleInterval.update_value(round((self.last_cycle - previous).total_seconds(), 1))
        self.cycle_total += 1
        self.cycleCount.update_value(self.cycle_total)
        self.lastCycle.update_value(dt_util.now())
        self.p1Value.update_value(p1)
        self.devicesRamping.update_value(ramping)
        if self.followup_needed(p1):
            self._schedule_followup()

    def _cycle_stuck(self, task: asyncio.Task) -> None:
        """Log where a control cycle waits when it does not finish (it is stopped right after this)."""
        self.last_cycle_stuck = await_chain(task)
        _LOGGER.error(
            "Control cycle does not finish within %s s and is stopped; it waits in: %s",
            SmartMode.CYCLE_TIMEOUT,
            self.last_cycle_stuck,
        )

    def cadence(self) -> tuple[float, float]:
        """(normal, fast) update interval in seconds for the current operation.

        Matching follows the P1 meter closely. A fixed manual power and an off manager do not need
        that, which saves commands to the devices and requests to their small ESP32.
        """
        match self.operation:
            case ManagerMode.MANUAL:
                interval = max(self.manual_interval, SmartMode.TIMEZERO)
                return interval, interval
            case ManagerMode.OFF:
                return SmartMode.OFF_INTERVAL, SmartMode.OFF_INTERVAL
            case _:
                return SmartMode.TIMEZERO, SmartMode.TIMEFAST

    def _check_mqtt(self) -> None:
        """Update the MQTT status sensors and raise a repair issue when a broker stays unreachable."""
        for key, client, entity, configured in (
            ("cloud", Api.mqttCloud, self.mqttCloudConnected, True),
            ("local", Api.mqttLocal, self.mqttLocalConnected, bool(Api.localServer)),
        ):
            connected = client.is_connected()
            entity.update_value(1 if connected else 0)
            issue_id = f"mqtt_{key}_disconnected"
            if connected or not configured:
                if self._mqtt_down[key] >= SmartMode.MQTT_ISSUE_AFTER:
                    ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                self._mqtt_down[key] = 0
                continue
            self._mqtt_down[key] += 1
            if self._mqtt_down[key] == SmartMode.MQTT_ISSUE_AFTER:
                server = Api.localServer if key == "local" else Api.cloudServer
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=f"mqtt_{key}_disconnected",
                    translation_placeholders={"server": server},
                )

    def _discovered(self, serial: str, address: str) -> None:
        """A zenSDK device was found on the network.

        A manually configured address wins while the device answers there. When it no longer answers
        (DHCP gave it another address), the new address is used and stored as the manual address.
        """
        for d in self.devices:
            if not isinstance(d, ZendureZenSdk) or d.snNumber != serial or address == d.ipAddress:
                continue
            if (manual := self._ip_overrides.get(serial)) is None:
                d.set_address(address, "discovery")
            elif d._http_failures >= Http.OFFLINE_AFTER:
                _LOGGER.warning(
                    "%s does not answer at its address %s but was found at %s: the address is changed. A fixed address (DHCP reservation) in the router prevents this",
                    d.name,
                    manual,
                    address,
                )
                self._ip_overrides[serial] = address
                d.set_address(address, "manual")
                ips = dict(self.config_entry.data.get(CONF_DEVICE_IPS) or {})
                ips[serial] = address
                self.hass.config_entries.async_update_entry(self.config_entry, data={**self.config_entry.data, CONF_DEVICE_IPS: ips})
            else:
                _LOGGER.debug("%s found at %s, the manual address %s still answers", d.name, address, manual)

    def _search_lost_devices(self) -> None:
        """Look for zenSDK devices that no longer answer at their address in the network (mDNS did not find them)."""
        if self._search_task is not None or monotonic() - self._search_last < SEARCH_INTERVAL:
            return
        lost = [
            d
            for d in self.devices
            if isinstance(d, ZendureZenSdk) and d.connection.value != 0 and d.ipAddress and d._http_failures >= Http.OFFLINE_AFTER
        ]
        if not lost:
            return
        self._search_last = monotonic()

        async def search() -> None:
            try:
                for address in {d.ipAddress for d in lost}:
                    serials = {d.snNumber for d in lost if d.ipAddress == address}
                    _LOGGER.info("Looking for %s in the network of %s", ", ".join(d.name for d in lost if d.ipAddress == address), address)
                    for sn, found in (await search_subnet(self.hass, address, serials)).items():
                        self._discovered(sn, found)
            finally:
                self._search_task = None

        self._search_task = self.hass.async_create_background_task(search(), "zendure_ha search devices")

    async def async_shutdown_manager(self) -> None:
        """Stop timers and background tasks when the integration is unloaded."""
        self.cancel_followup()
        if self._search_task is not None:
            self._search_task.cancel()
            self._search_task = None
        if self.p1direct is not None:
            self.p1direct.stop()
            self.p1direct = None
        if self.p1record is not None:
            await self.p1record.stop()
            self.p1record = None
        if self.discovery is not None:
            await self.discovery.async_stop()
            self.discovery = None
        if self.simlog is not None:
            await self.simlog.flush()
        for d in self.devices:
            d.cancel_timers()

    def apply_settings(self, data: Mapping[str, Any]) -> None:
        """Apply the power control settings (options menu) to the controller and the devices."""
        timezero = float(data.get(CONF_TIMEZERO, CONTROL_DEFAULTS[CONF_TIMEZERO]))
        SmartMode.TIMEZERO = timezero
        SmartMode.TIMEFAST = min(float(data.get(CONF_TIMEFAST, CONTROL_DEFAULTS[CONF_TIMEFAST])), timezero)
        SmartMode.P1_STDDEV_MIN = int(data.get(CONF_P1_STDDEV_MIN, CONTROL_DEFAULTS[CONF_P1_STDDEV_MIN]))
        SmartMode.POWER_START = int(data.get(CONF_POWER_START, CONTROL_DEFAULTS[CONF_POWER_START]))
        SmartMode.POWER_TOLERANCE = int(data.get(CONF_POWER_TOLERANCE, CONTROL_DEFAULTS[CONF_POWER_TOLERANCE]))
        self.manual_interval = float(data.get(CONF_MANUAL_INTERVAL, CONTROL_DEFAULTS[CONF_MANUAL_INTERVAL]))
        self.peak_limit = max(0, int(data.get(CONF_PEAK_LIMIT, CONTROL_DEFAULTS[CONF_PEAK_LIMIT])))
        self.role_margin = max(0, int(data.get(CONF_ROLE_MARGIN, CONTROL_DEFAULTS[CONF_ROLE_MARGIN])))
        self.roles.margin = self.role_margin
        self.fill_level = min(100, max(20, int(data.get(CONF_FILL_LEVEL, CONTROL_DEFAULTS[CONF_FILL_LEVEL]))))
        self.export_bias = max(0, int(data.get(CONF_EXPORT_BIAS, CONTROL_DEFAULTS[CONF_EXPORT_BIAS])))
        self._update_p1_record(str(data.get(CONF_P1_RECORD) or ""))

        # manual addresses of zenSDK devices (serial number -> address); an empty value uses the cloud / discovery address
        self._ip_overrides = {sn: str(ip).strip() for sn, ip in (data.get(CONF_DEVICE_IPS) or {}).items() if str(ip).strip()}
        for d in self.devices:
            d.configure_slew(
                bool(data.get(CONF_SLEW_ENABLED, True)),
                float(data.get(CONF_SLEW_UP, DEFAULT_UP)),
                float(data.get(CONF_SLEW_DOWN, DEFAULT_DOWN)),
                float(data.get(CONF_FLIP_DELAY, DEFAULT_FLIP_DELAY)),
                float(data.get(CONF_MIN_RUN, DEFAULT_MIN_RUN)),
                float(data.get(CONF_READY_RATE, DEFAULT_READY_UP)),
            )
            d.persist_safe_state = bool(data.get(CONF_FLASH_SAFE, True))
            if isinstance(d, ZendureZenSdk) and (ip := self._ip_overrides.get(d.snNumber)):
                d.set_address(ip, "manual")

    async def ramp_off(self) -> None:
        """Ramp all devices down to 0 W (slew limited) and switch them off."""
        ramping = False
        for d in self.devices:
            ramping |= await d.power_off_ramped()
        if ramping:
            self._schedule_followup()

    def _schedule_followup(self) -> None:
        """Continue a ramp without waiting for the next P1 change."""
        if self._followup is None and self.hass.is_running:
            self._followup = self.hass.loop.call_later(SmartMode.RAMP_INTERVAL, self._followup_fire)

    def _followup_fire(self) -> None:
        self._followup = None
        self.hass.async_create_task(self._followup_run())

    async def _followup_run(self) -> None:
        if self.operation == ManagerMode.OFF:
            await self.ramp_off()
        elif self.zero_fast != datetime.max:
            await self._run_power_update(self.p1_last, True, datetime.now())

    def cancel_followup(self) -> None:
        if self._followup is not None:
            self._followup.cancel()
            self._followup = None

    async def powerChanged(self, p1: int, isFast: bool, time: datetime) -> None:
        """Return the distribution setpoint."""
        availableKwh = 0
        setpoint = p1
        power = 0
        totalStoredkWh = 0
        onlinekWh = 0

        for d in self.devices:
            if await d.power_get():
                # get power production
                d.pwr_produced = min(0, d.batteryOutput.asInt + d.homeInput.asInt - d.batteryInput.asInt - d.homeOutput.asInt)
                if d.state == DeviceState.SOCFULL and -d.solarInput.asInt < d.pwr_produced:
                    d.pwr_produced = -d.solarInput.asInt
                self.produced -= d.pwr_produced

                # only positive pwr_offgrid must be taken into account, negative values count a solarInput
                # a few W (within the power tolerance) count as standby: otherwise a device that is left with a tiny
                # charge command stays 'charging', its stop command falls within the tolerance and nothing starts
                tiny = SmartMode.POWER_TOLERANCE if d.pwr_offgrid == 0 else 0
                if (home := -d.homeInput.asInt + max(0, d.pwr_offgrid)) < -tiny:
                    self.charge.append(d)
                    self.charge_limit += d.fuseGrp.charge_limit(d)
                    self.charge_optimal += d.charge_optimal
                    self.charge_weight += d.pwr_max * (100 - d.electricLevel.asInt)
                    # Credit only the portion of homeInput that reaches the battery; AC
                    # drawn but not stored is real demand on the home bus, not surplus.
                    setpoint -= min(d.homeInput.asInt, d.batteryInput.asInt)
                # SOCEMPTY means, it could not discharge the battery, but it is still possible to feed into the home using solarpower or offGrid
                elif (home := d.homeOutput.asInt) > tiny:
                    self.discharge.append(d)
                    # Cap the bypass at the homeOutput actually added to the setpoint for this
                    # device: pwr_produced can exceed homeOutput (internal trickle charge, sensor
                    # skew), and subtracting more than was added fabricates a phantom negative
                    # setpoint — the root cause of #1151.
                    if d.state == DeviceState.SOCFULL and d.exports_bypass:
                        self.discharge_bypass += min(-d.pwr_produced, home)
                    self.discharge_limit += d.fuseGrp.discharge_limit(d)
                    self.discharge_optimal += d.discharge_optimal
                    self.discharge_produced -= d.pwr_produced
                    self.discharge_weight += d.pwr_max * d.electricLevel.asInt
                    setpoint += home

                else:
                    self.idle.append(d)
                    self.idle_lvlmax = max(self.idle_lvlmax, d.electricLevel.asInt)
                    self.idle_lvlmin = min(self.idle_lvlmin, d.electricLevel.asInt if d.state != DeviceState.SOCFULL else 100)

                availableKwh += d.actualKwh
                power += d.pwr_offgrid + home + d.pwr_produced
                totalStoredkWh += d.electricLevel.asNumber / 100 * d.kWh
                onlinekWh += d.kWh

        # Update the power entities (the count, with the names of the devices as attribute)
        not_responding = [d for d in self.devices if d not in self.charge and d not in self.discharge and d not in self.idle]
        self._set_devices(self.devicesOnline, [*self.charge, *self.discharge, *self.idle])
        self._set_devices(self.devicesCharging, self.charge)
        self._set_devices(self.devicesDischarging, self.discharge)
        self._set_devices(self.devicesIdle, self.idle)
        self._set_devices(getattr(self, "devicesNotResponding", None), not_responding)
        self.power.update_value(power)
        self.availableKwh.update_value(availableKwh)
        self.globalSoc.update_value((totalStoredkWh / onlinekWh * 100) if onlinekWh > 0 else 0)

        # Bypass production of SOCFULL devices is non-dispatchable: it keeps flowing
        # to the home regardless of the distribution (power_charge skips devices with
        # byPass > 0). Remove it from the dispatchable setpoint. Because the per-device
        # bypass is capped at its homeOutput contribution, this subtraction can never
        # push the setpoint below "p1 - real charge credits": with no device charging
        # and p1 >= 0, the result stays >= 0 — the #1151 guarantee holds structurally.
        setpoint -= self.discharge_bypass
        setpoint = self._cloud_adjust(setpoint, time)
        self.dispatchSetpoint.update_value(setpoint)
        self._check_crossflow(time)

        # Update power distribution.
        _LOGGER.debug("P1 ======> p1:%s isFast:%s, setpoint:%sW stored:%sW", p1, isFast, setpoint, self.produced)
        self._update_roles()
        await self._prepare_roles()
        match self.operation:
            case ManagerMode.MATCHING:
                if setpoint < 0:
                    await self.power_charge(setpoint, time)
                else:
                    # peak shaving: with a limit the batteries only discharge above it (solar pass-through always)
                    await self.power_discharge(peak_setpoint(setpoint, self.peak_limit, self.produced), time)

            case ManagerMode.MATCHING_DISCHARGE:
                # Discharge to cover demand and always pass through available solar; never charge
                await self.power_discharge(max(self.produced, peak_setpoint(setpoint, self.peak_limit, self.produced)), time)

            case ManagerMode.MATCHING_CHARGE | ManagerMode.STORE_SOLAR:
                # Allow discharge of produced power in MATCHING_CHARGE-Mode, otherwise only charge
                # d.pwr_produced is negative, but self.produced is positive
                if setpoint > 0 and self.produced > SmartMode.POWER_START and self.operation == ManagerMode.MATCHING_CHARGE:
                    await self.power_discharge(min(self.produced, setpoint), time)
                else:
                    await self.power_charge(min(0, setpoint), time)

            case ManagerMode.MANUAL:
                # Manual power into or from home
                if (setpoint := int(self.manualpower.asNumber)) > 0:
                    await self.power_discharge(setpoint, time)
                else:
                    await self.power_charge(setpoint, time, hysteresis=False)

            case ManagerMode.OFF:
                self.operationstate.update_value(ManagerState.OFF.value)

    # ----------------------------------------------------------------------------------------
    # changing clouds
    # ----------------------------------------------------------------------------------------
    def _volatility(self) -> Volatility:
        return self.__dict__.setdefault("volatility", Volatility())

    @property
    def cloud_mode(self) -> bool:
        return self._volatility().active

    def _cloud_adjust(self, setpoint: int, time: datetime) -> int:
        """During changing clouds, prefer a little export over import (C).

        A trend feed-forward (B) was tried as well; next to the fast adjustment of running devices it gave only
        a little less import but many more device starts in the simulation, so it is not used.
        """
        self.measured_setpoint = setpoint  # without the export margin: used for decisions about devices
        volatility = self._volatility()
        was = volatility.active
        volatility.add(time.timestamp(), setpoint)
        if volatility.active != was:
            _LOGGER.info(
                "Changing clouds mode %s (%s edges of %s W or more in 10 minutes, peak surplus %s W)",
                "on" if volatility.active else "off",
                volatility.edges,
                volatility.threshold,
                volatility.peak_surplus,
            )
        if (sensor := getattr(self, "cloudMode", None)) is not None:
            sensor.update_value(1 if self.cloud_mode else 0)
        if self.operation not in (ManagerMode.MATCHING, ManagerMode.MATCHING_CHARGE, ManagerMode.MATCHING_DISCHARGE, ManagerMode.STORE_SOLAR):
            return setpoint
        adjusted = setpoint
        if self.cloud_mode and (margin := self.cloud_margin):
            # aim at a small export instead of 0 W, without changing the direction of the batteries
            if adjusted < 0:
                adjusted = min(0, adjusted + margin)
            elif adjusted > 0 and SmartMode.CLOUD_MARGIN_DISCHARGE:
                adjusted += margin
        return adjusted

    @property
    def start_threshold(self) -> int:
        """Power (W) the running devices cannot deliver before another device is started.

        During changing clouds a small shortage lasts only until the sun is back: starting a device for it
        costs a relay switch and runs the inverter at a low, inefficient load.
        """
        return max(SmartMode.POWER_START, SmartMode.CLOUD_POWER_START) if self.cloud_mode else SmartMode.POWER_START

    @property
    def cloud_margin(self) -> int:
        """Export margin (W) during changing clouds: the setting, but at most a share of the recent peak surplus.

        With little sun a fixed 100 W is a large part of the production that is then not stored.
        """
        return int(min(self.export_bias, SmartMode.CLOUD_MARGIN_SHARE * self._volatility().peak_surplus))

    def followup_needed(self, _p1: int) -> bool:
        """Run the next control cycle after RAMP_INTERVAL while a device is still ramping.

        Also running every second while P1 moves at a cloud edge was tried (option A): in the simulation it gave
        more device starts and not less import, because the control reacted to every intermediate value. The
        fast adjustment of running devices (fast_track) follows every P1 update instead, without starts.
        """
        return any(d.slew.pending for d in self.devices)

    async def fast_track(self, p1: int, _time: datetime | None = None) -> None:
        """Follow every P1 update with the devices that already run in the right direction (option 4).

        Between two control cycles only the power of running devices is changed, in their current AC mode and
        direction: nothing is started, stopped or switched, so no relay switches and no AC mode change. All
        of that stays with the control cycle (every few seconds, with the 0 W confirmation and the mode steps).
        Power that was already commanded but is not measured yet is taken into account, because the P1 value
        does not show it yet; otherwise the same difference would be corrected twice.
        """
        if self.operation not in (ManagerMode.MATCHING, ManagerMode.MATCHING_CHARGE, ManagerMode.MATCHING_DISCHARGE, ManagerMode.STORE_SOLAR):
            return
        if self.zero_fast == datetime.max or (self.charge and self.discharge):
            return  # a control cycle runs, or a hand-over between charging and discharging is going on
        direction = 1 if self.discharge else -1 if self.charge else 0
        if direction == 0 or (direction > 0 and self.operation in (ManagerMode.MATCHING_CHARGE, ManagerMode.STORE_SOLAR)):
            return
        candidates = self.discharge if direction > 0 else self.charge
        devices = [
            d
            for d in candidates
            if d.slew.current * direction >= SmartMode.FAST_MIN
            and not d.slew.pending
            # only with a report that is newer than the last command: otherwise the power that is still on its way
            # cannot be known, and the fast adjustment would work against the control cycle
            and getattr(d, "mqtt_last", None) is not None
            and getattr(d, "command_time", float("-inf")) < d.mqtt_last
            and d.pwr_offgrid == 0
            and d.byPass.asInt == 0
            and d.state not in (DeviceState.OFFLINE, DeviceState.SOCFULL if direction < 0 else DeviceState.SOCEMPTY)
        ]
        if not devices:
            return
        target = -self.cloud_margin if self.cloud_mode and (direction < 0 or SmartMode.CLOUD_MARGIN_DISCHARGE) else 0
        pending = sum(d.slew.current - (d.homeOutput.asInt - d.homeInput.asInt) for d in devices)
        error = p1 - target - pending  # > 0: the home needs more power from the batteries
        if abs(error) < SmartMode.FAST_DEADBAND:
            return

        def limit(d: Any) -> int:
            return abs(d.discharge_limit if direction > 0 else d.charge_limit)

        # share the correction by the room each device has in that direction
        grow = error * direction > 0  # the magnitude of the power goes up
        room = {d: (limit(d) - abs(d.slew.current)) if grow else (abs(d.slew.current) - SmartMode.FAST_MIN) for d in devices}
        total = sum(max(0, r) for r in room.values())
        if total <= 0:
            return
        last = self.__dict__.setdefault("fast_last", {})
        moment = (_time or datetime.now()).timestamp()
        for d in devices:
            if moment - last.get(d.deviceId, -1e9) < SmartMode.FAST_INTERVAL:
                continue  # this device was adjusted a moment ago: spare its ESP32
            share = error * max(0, room[d]) / total
            new = int(d.slew.current + share)
            new = max(SmartMode.FAST_MIN, min(limit(d), new)) if direction > 0 else -max(SmartMode.FAST_MIN, min(limit(d), -new))
            if abs(new - d.slew.current) < SmartMode.POWER_TOLERANCE:
                continue
            last[d.deviceId] = moment
            if direction > 0:
                await d.power_discharge(new, ready=d.deviceId == self.roles.discharger)
            else:
                await d.power_charge(new)

    async def _keep_cloud_reserve(self, setpoint: int, time: datetime | None = None) -> None:
        """Cloud mode (E): at most all devices but one charge; the discharge role device stays ready.

        With changing clouds a cloud or an appliance needs power at once. The device with the discharge role
        then does not charge, it waits in output mode, so it can take over without a direction change.
        When the surplus is more than the other devices can take (above RESERVE_RELEASE % of their capacity,
        e.g. above ~4500 W with two other 2400 W devices) the reserve charges too; it is kept out again below
        RESERVE_KEEP %, so it does not switch on and off around the limit.
        """
        reserve = self.roles.discharger if self.cloud_mode else None
        if reserve is not None:
            # nominal capacity of all other online devices (also those still discharging at a cloud edge):
            # the reserve only joins above what they can take together
            others = [d for d in self.devices if d.deviceId != reserve and d.state != DeviceState.OFFLINE]
            capacity = sum(abs(getattr(d, "charge_limit", d.pwr_max)) for d in others)
            released = self.__dict__.get("cloud_reserve_released", False)
            level = SmartMode.RESERVE_KEEP if released else SmartMode.RESERVE_RELEASE
            # the measured surplus, without the export margin
            surplus = abs(min(0, self.__dict__.get("measured_setpoint", setpoint)))
            wants = not others or surplus > capacity * level / 100
            # the surplus overshoots for a moment while chargers ramp (the P1 value lags the device readings):
            # only switch after the condition held for a while
            now = time or datetime.now()
            if wants != released:
                since = self.__dict__.setdefault("cloud_reserve_since", now)
                hold = SmartMode.RESERVE_DELAY if wants else SmartMode.CONSOLIDATE_DELAY
                if (now - since).total_seconds() >= hold or not others:
                    released = wants
                    self.__dict__.pop("cloud_reserve_since", None)
            else:
                self.__dict__.pop("cloud_reserve_since", None)
            self.cloud_reserve_released = released
            if released:
                reserve = None
        self.cloud_reserve = reserve
        if reserve is None or len(self.charge) + len(self.idle) < 2:
            return
        for d in list(self.charge):
            if d.deviceId != reserve or d.state == DeviceState.SOCEMPTY:
                continue
            self.charge.remove(d)
            self.idle.append(d)
            self.charge_limit -= d.pwr_max
            self.charge_optimal -= d.charge_optimal
            self.charge_weight -= d.pwr_max * (100 - d.electricLevel.asInt)
            await d.power_charge(0)  # ramps down; it is prepared for output once it is at 0 W

    async def _prepare_roles(self) -> None:
        """Put the idle role holders in the AC mode of their role, so they are ready when they are needed."""
        if self.operation not in (ManagerMode.MATCHING, ManagerMode.MATCHING_CHARGE, ManagerMode.MATCHING_DISCHARGE, ManagerMode.STORE_SOLAR):
            return
        for d in self.idle:
            prepare = getattr(d, "prepare", None)
            if prepare is None:
                continue
            if d.deviceId == self.roles.charger and d.state != DeviceState.SOCFULL:
                await prepare(-1)
            elif d.deviceId == self.roles.discharger and d.state != DeviceState.SOCEMPTY:
                await prepare(1)

    def _check_crossflow(self, time: datetime | None = None) -> None:
        """One device charging while another discharges is normal for a few seconds (a parallel hand-over).

        When it lasts longer, a device did not follow its stop command and one battery is charging the other
        (round trip losses for nothing). The next cycles compensate from the measured values, but it is logged.
        """
        active = [d for d in self.discharge if d.byPass.asInt <= 0 and d.pwr_offgrid <= 0]
        now = time or datetime.now()
        if not (self.charge and active):
            self.crossflow_cycles = 0
            self.crossflow_since = None
            self.crossflow_warned = False
            return
        self.crossflow_cycles += 1
        if self.crossflow_since is None:
            self.crossflow_since = now
        elif not self.crossflow_warned and (now - self.crossflow_since).total_seconds() >= SmartMode.CROSSFLOW_WARN:
            self.crossflow_warned = True
            _LOGGER.warning(
                "%s charging while %s discharging for %s s: a device does not follow its power command",
                ", ".join(d.name for d in self.charge),
                ", ".join(d.name for d in active),
                int((now - self.crossflow_since).total_seconds()),
            )

    def _update_roles(self) -> None:
        """One device is ready to discharge and another one to charge; roles only move after a clear SoC difference."""
        if len(self.devices) < 2 or not self.roles.active:
            self.roles.reset()
            self._show_roles()
            return
        dischargeable = [(d.deviceId, d.electricLevel.asInt) for d in self.devices if d.state not in (DeviceState.OFFLINE, DeviceState.SOCEMPTY)]
        chargeable = [(d.deviceId, d.electricLevel.asInt) for d in self.devices if d.state not in (DeviceState.OFFLINE, DeviceState.SOCFULL)]
        self.roles.update(dischargeable, chargeable)
        self._show_roles()

    def _activity(self, device: ZendureDevice) -> str:
        """What a device does now, from the same classification as the device count sensors."""
        if device in self.charge:
            return "charging"
        if device in self.discharge:
            return "discharging"
        if device in self.idle:
            return "reserve" if device.deviceId == self.cloud_reserve else "standby"
        return "not_responding"

    def _show_roles(self) -> None:
        """Role sensors: the device that has the role and what it does now, e.g. 'L2 SolarFlow 2400AC (standby)'.

        A role means 'ready for': the device with the charge role is the first to charge when there is a
        surplus, it does not have to be charging now. Showing the activity next to the name keeps the role
        sensors in line with the 'Devices charging / discharging / idle' sensors.
        """
        language = (getattr(getattr(getattr(self, "hass", None), "config", None), "language", None) or "en").split("-")[0]
        labels = ACTIVITY_LABELS.get(language, ACTIVITY_LABELS["en"])
        for sensor, device_id in ((self.roleDischarge, self.roles.discharger), (self.roleCharge, self.roles.charger)):
            device = next((d for d in self.devices if d.deviceId == device_id), None)
            sensor.update_value("-" if device is None else f"{device.name} ({labels[self._activity(device)]})")
        # the same per device: Activity and Role sensors in the diagnostics of every device
        for d in self.devices:
            if (activity := getattr(d, "activity", None)) is not None and hasattr(activity, "update_value"):
                activity.update_value(self._activity(d))
            if (role := getattr(d, "role", None)) is not None and hasattr(role, "update_value"):
                role.update_value("discharge" if d.deviceId == self.roles.discharger else "charge" if d.deviceId == self.roles.charger else "none")

    @staticmethod
    def _set_devices(sensor: Any, devices: list[Any]) -> None:
        """Count sensor with the names of the devices as attribute 'devices'."""
        if sensor is None:
            return
        names = sorted(d.name for d in devices)
        changed = getattr(sensor, "_attr_extra_state_attributes", None) != {"devices": names}
        sensor._attr_extra_state_attributes = {"devices": names}  # noqa: SLF001
        hass = getattr(sensor, "hass", None)
        if not sensor.update_value(len(devices)) and changed and hass and hass.loop.is_running():
            sensor.schedule_update_ha_state()

    def _release_charger(self, device: ZendureDevice, demand: int, time: datetime) -> bool:
        """True when the other charging devices can carry the load, so this device can go back to standby."""
        return self._release("charge_release_since", len(self.charge), device, demand, time)

    def _release_discharger(self, device: ZendureDevice, demand: int, time: datetime) -> bool:
        """True when the other discharging devices can carry the load (for example after a peak has ended)."""
        return self._release("discharge_release_since", len(self.discharge), device, demand, time)

    def _release(self, timer: str, running: int, device: ZendureDevice, demand: int, time: datetime) -> bool:
        """Consolidate the load on as few devices as possible, in both directions.

        Another device is only started when the running ones carry ``fill_level`` % of their limit, and a device
        is only released when the rest would stay below ``fill_level - FILL_HYSTERESIS`` % for at least
        ``CONSOLIDATE_DELAY`` seconds, so a short dip (a kettle that switches on and off) does not make it flap.
        The rest must also be able to carry the highest load of the last ``RELEASE_LOOKBACK`` seconds: with
        passing clouds the sun returns within a few minutes, and a released device would be started again.
        """
        remaining = running - 1
        release_level = max(10, self.effective_fill - SmartMode.FILL_HYSTERESIS)
        load = max(abs(demand), self._recent_peak(timer == "discharge_release_since", time))
        if abs(demand) <= SmartMode.POWER_START or load >= remaining * abs(device.pwr_max) * release_level / 100:
            setattr(self, timer, None)
            return False
        since = getattr(self, timer)
        if since is None:
            setattr(self, timer, time)
            return False
        if (time - since).total_seconds() < SmartMode.CONSOLIDATE_DELAY:
            return False
        setattr(self, timer, None)
        self._released()[(device.deviceId, timer == "discharge_release_since")] = time
        return True

    def _record_demand(self, discharge: bool, demand: int, time: datetime) -> None:
        """Remember the requested power of this cycle (per direction) for the release decision."""
        history = self.__dict__.setdefault("demand_history", {True: deque(), False: deque()})[discharge]
        history.append((time, abs(demand)))
        while history and (time - history[0][0]).total_seconds() > SmartMode.RELEASE_LOOKBACK:
            history.popleft()

    def _recent_peak(self, discharge: bool, time: datetime) -> int:
        history = self.__dict__.get("demand_history", {}).get(discharge, ())
        return max((value for when, value in history if (time - when).total_seconds() <= SmartMode.RELEASE_LOOKBACK), default=0)

    def _released(self) -> dict[tuple[str, bool], datetime]:
        return self.__dict__.setdefault("released_at", {})

    def _hold_released(self, running: list[ZendureDevice], time: datetime, discharge: bool) -> None:
        """A device that was just released stays in standby for RELEASE_HOLD seconds.

        Its report can lag behind (it still shows some output for a moment); without this hold it would be
        counted as running again and get a share in the next cycle, so it would start and stop every 30 s.
        It can still be started by the normal start rule when the load needs it.
        """
        released = self._released()
        for d in list(running):
            if (since := released.get((d.deviceId, discharge))) is None:
                continue
            if (time - since).total_seconds() >= SmartMode.RELEASE_HOLD:
                del released[(d.deviceId, discharge)]
                continue
            running.remove(d)
            self.idle.append(d)
            level = d.electricLevel.asInt
            self.idle_lvlmax = max(self.idle_lvlmax, level)
            self.idle_lvlmin = min(self.idle_lvlmin, level if d.state != DeviceState.SOCFULL else 100)
            if discharge:
                self.discharge_limit -= d.pwr_max
                self.discharge_optimal -= d.discharge_optimal
                self.discharge_weight -= d.pwr_max * level
                self.discharge_produced += d.pwr_produced
            else:
                self.charge_limit -= d.pwr_max
                self.charge_optimal -= d.charge_optimal
                self.charge_weight -= d.pwr_max * (100 - level)

    @staticmethod
    def _headroom(d: ZendureDevice, used: dict[int, int], discharge: bool) -> int:
        """Power (W, positive) a device may still take within its fuse group, after the other devices of the group."""
        group = getattr(d, "fuseGrp", None)
        if group is None:
            return 1_000_000
        limit = group.maxpower if discharge else -group.minpower
        return max(0, limit - used.get(id(group), 0))

    @staticmethod
    def _use(d: ZendureDevice, used: dict[int, int], power: int) -> None:
        if (group := getattr(d, "fuseGrp", None)) is not None:
            used[id(group)] = used.get(id(group), 0) + abs(power)

    @property
    def effective_fill(self) -> int:
        """Fill level in use: the setting (default 85 %, efficient at part load) or a lower one in cloud mode."""
        if self.cloud_mode:
            return min(self.fill_level, SmartMode.CLOUD_FILL_LEVEL)
        return self.fill_level

    def _fill(self, optimal: int) -> int:
        """Load at which a device counts as 'full enough' to start another one.

        ``optimal`` is a quarter of the device limit; at the old default of 50 % this equals ``optimal * 2``.
        With a higher fill level fewer devices share the load and the others stay idle and ready for peaks.
        """
        return int(optimal * 4 * self.effective_fill / 100)

    @property
    def start_margin(self) -> int:
        """SoC points a standby device must lead before it is started only because of its SoC (not for power)."""
        return max(3, self.role_margin)

    async def power_charge(self, setpoint: int, time: datetime, hysteresis: bool = True) -> None:
        """Charge devices. Manual mode passes hysteresis=False so the chosen power is applied immediately."""
        _LOGGER.debug("Charge => setpoint %sW", setpoint)

        # stop discharging devices, in parallel with starting / scaling the charging devices
        # (bypassing devices are not stopped; a gridOff device keeps -10 W so it does not use power from the grid)
        stopping = [asyncio.ensure_future(d.power_discharge(0 if d.pwr_offgrid == 0 else -10)) for d in self.discharge if d.byPass.asInt <= 0]
        try:
            await self._charge_devices(setpoint, time, hysteresis)
        finally:
            await self._finish(stopping)

    async def _finish(self, stopping: list[asyncio.Future]) -> None:
        """Wait for the devices that are scaled down in parallel; a failing device must not stop the others."""
        if not stopping:
            return
        for result in await asyncio.gather(*stopping, return_exceptions=True):
            if isinstance(result, BaseException):
                _LOGGER.error("Stopping a device failed: %s", result)

    async def _charge_devices(self, setpoint: int, time: datetime, hysteresis: bool) -> None:
        self._hold_released(self.charge, time, False)
        await self._keep_cloud_reserve(setpoint, time)
        # prevent hysteria
        if hysteresis and self.charge_time > time:
            if self.charge_time == datetime.max:
                # during changing clouds a cloud is followed by sun within a minute: wait shorter before charging again
                delay = SmartMode.CLOUD_CHARGE_DELAY if self.cloud_mode else SmartMode.CHARGE_DELAY
                self.charge_time = time + timedelta(seconds=SmartMode.CHARGE_DELAY_FIRST if (time - self.charge_last).total_seconds() > SmartMode.CHARGE_DELAY_WINDOW else delay)
                self.charge_last = self.charge_time
                self.pwr_low = 0
            setpoint = 0
        self.operationstate.update_value(ManagerState.CHARGE.value if setpoint < 0 else ManagerState.IDLE.value)

        # distribute charging devices
        dev_start = min(0, setpoint - self._fill(self.charge_optimal)) if setpoint < -self.start_threshold else 0
        limit = self.charge_limit
        requested = setpoint
        if setpoint < 0:
            self._record_demand(False, setpoint, time)
        setpoint = max(limit, setpoint)
        demand = setpoint  # the total charge power to distribute (negative)
        used: dict[int, int] = {}  # power per fuse group commanded in this cycle
        for i, d in enumerate(sorted(self.charge, key=lambda d: d.electricLevel.asInt, reverse=True)):
            # Weight per device: pwr_max * remaining capacity (100 - SOC%).
            # Devices with lower SOC get a larger share of the charge power.
            # Guard against division by zero: charge_weight can be 0 when all
            # remaining devices are at 100% SOC (nothing left to charge) or when
            # it drops to 0 mid-iteration after subtracting previous devices.
            device_weight = d.pwr_max * (100 - d.electricLevel.asInt)
            if self.charge_weight != 0:
                pwr = int(setpoint * device_weight / self.charge_weight)
            else:
                # all remaining devices at 100% SOC — skip charging
                pwr = 0
            self.charge_weight -= device_weight

            # adjust the limit, make sure we have 'enough' power to charge
            limit -= d.pwr_max
            pwr = max(pwr, setpoint, d.pwr_max)
            if limit > setpoint - pwr:
                pwr = max(setpoint - limit, setpoint, d.pwr_max)

            # make sure we have devices in optimal working range: the device with the highest SoC (the one that is
            # kept ready to discharge) is released as soon as the others can carry the load, so it can react to a peak
            if len(self.charge) > 1 and i == 0 and self._release_charger(d, demand, time):
                pwr = 0

            self._use(d, used, pwr)
            setpoint -= await d.power_charge(pwr)
            dev_start += -1 if pwr != 0 and d.electricLevel.asInt > self.idle_lvlmin + self.start_margin else 0

        # start idle device if needed
        if dev_start < 0 and len(self.idle) > 0:
            # the device with the charge role first, then the lowest SoC
            self.idle.sort(key=lambda d: (d.deviceId != self.roles.charger, d.electricLevel.asInt))
            missing = requested - (demand - setpoint)  # the (negative) part that the running devices cannot take
            starts = []
            for d in self.idle:
                if d.deviceId == self.cloud_reserve:
                    continue  # cloud mode: this device stays ready to discharge
                # offGrid device need to be started with at least their offgrid power, otherwise they will not be recognized as charging
                # but should not be started with more than pwr_offgrid if they are full
                # if a offGrid device need to be started, the output power is set to 0 and it take all offGrid power from grid
                # a started device takes the surplus the running devices cannot take; above its limit the next one takes the rest
                if self._headroom(d, used, False) < SmartMode.POWER_START:
                    continue  # its fuse group is already at the limit
                start_pwr = max(SmartMode.POWER_START, min(-missing, abs(d.charge_limit), self._headroom(d, used, False)))
                self._use(d, used, start_pwr)
                self._released().pop((d.deviceId, False), None)
                starts.append(d.power_charge(-start_pwr - max(0, d.pwr_offgrid) if d.state != DeviceState.SOCFULL else -max(0, d.pwr_offgrid)))
                missing += start_pwr
                if (dev_start := dev_start + max(-self._fill(d.charge_optimal), start_pwr)) >= 0:
                    break
            # all new devices are started at the same time
            await asyncio.gather(*starts)
            self.pwr_low: int = 0

    async def power_discharge(self, setpoint: int, time: datetime | None = None) -> None:
        """Discharge devices."""
        _LOGGER.debug("Discharge => setpoint %sW", setpoint)
        self.operationstate.update_value(ManagerState.DISCHARGE.value if setpoint > 0 and self.discharge else ManagerState.IDLE.value)

        # reset hysteria time
        if self.charge_time != datetime.max:
            self.charge_time = datetime.max
            self.pwr_low = 0

        # stop charging devices, in parallel with scaling up the discharging devices: at a peak the charger ramps
        # down while the device that was kept ready ramps up, instead of one after the other.
        # SF 2400 may show more gridInputPower than offGridPower and will be recognized as charging, so set power to 10 instead of 0
        stopping = [asyncio.ensure_future(d.power_discharge(0 if max(0, d.pwr_offgrid) == 0 else 10)) for d in self.charge]
        try:
            await self._discharge_devices(setpoint, time or datetime.now())
        finally:
            await self._finish(stopping)

    async def _discharge_devices(self, setpoint: int, time: datetime) -> None:
        self._hold_released(self.discharge, time, True)
        # distribute discharging devices, use produced power first, before adding another device
        requested = setpoint
        if setpoint > 0:
            self._record_demand(True, setpoint, time)
        dev_start = max(0, setpoint - self._fill(self.discharge_optimal) - self.discharge_produced) if setpoint > self.start_threshold else 0
        solaronly = self.discharge_produced >= setpoint
        limit = self.discharge_produced if solaronly else self.discharge_limit
        setpoint = min(limit, setpoint)
        served = setpoint
        used: dict[int, int] = {}  # power per fuse group commanded in this cycle
        for i, d in enumerate(sorted(self.discharge, key=lambda d: d.electricLevel.asInt, reverse=False)):
            # Weight per device: pwr_max * SOC%. Devices with higher SOC get a
            # larger share of the discharge power.
            # Guard against division by zero: discharge_weight can be 0 when all
            # remaining devices are at 0% SOC, or when it drops to 0 mid-iteration.
            # In that case, distribute the remaining setpoint evenly across the
            # remaining devices so they can still pass through solar production.
            device_weight = d.pwr_max * d.electricLevel.asInt
            if self.discharge_weight != 0:
                pwr = int(setpoint * device_weight / self.discharge_weight)
            elif len(self.discharge) > i:
                pwr = int(setpoint / (len(self.discharge) - i))
            else:
                pwr = 0
            # SOCFULL devices should only pass through solar, not drain battery
            if pwr < -d.pwr_produced and d.state == DeviceState.SOCFULL:
                pwr = -d.pwr_produced
            self.discharge_weight -= device_weight

            # adjust the limit, make sure we have 'enough' power to discharge
            limit -= -d.pwr_produced if solaronly else d.pwr_max
            if limit < setpoint - pwr:
                pwr = max(setpoint - limit, 0 if d.state != DeviceState.SOCFULL else -d.pwr_produced)
            pwr = min(pwr, setpoint, d.pwr_max)

            # make sure we have devices in optimal working range: the device with the lowest SoC (the one that is
            # kept ready to charge) is released as soon as the others can carry the load again
            if len(self.discharge) > 1 and i == 0 and d.state != DeviceState.SOCFULL and self._release_discharger(d, served, time):
                pwr = 0

            self._use(d, used, pwr)
            if d.deviceId == self.roles.discharger:
                setpoint -= await d.power_discharge(pwr, ready=True)
            else:
                setpoint -= await d.power_discharge(pwr)
            dev_start += 1 if pwr != 0 and d.electricLevel.asInt + self.start_margin < self.idle_lvlmax else 0

        # start idle device if needed
        if dev_start > 0 and len(self.idle) > 0:
            # the device with the discharge role first, then the highest SoC
            self.idle.sort(key=lambda d: (d.deviceId != self.roles.discharger, -d.electricLevel.asInt))
            missing = requested - (served - setpoint)  # the part that the running devices cannot deliver
            starts = []
            for d in self.idle:
                if d.state != DeviceState.SOCEMPTY:
                    # a started device takes the power that the running devices cannot deliver (the device that was
                    # kept ready first); above its limit the next device takes the rest. The slew limiter protects them.
                    start = max(SmartMode.POWER_START, min(missing, d.discharge_limit, self._headroom(d, used, True)))
                    if self._headroom(d, used, True) < SmartMode.POWER_START:
                        continue  # its fuse group is already at the limit
                    self._use(d, used, start)
                    self._released().pop((d.deviceId, True), None)
                    # the device that was kept ready may ramp up faster (only from standby, not on a direction change)
                    if d.deviceId == self.roles.discharger:
                        starts.append(d.power_discharge(start, ready=True))
                    else:
                        starts.append(d.power_discharge(start))
                    missing -= start
                    if (dev_start := dev_start - max(self._fill(d.discharge_optimal), start)) <= 0:
                        break
            # all new devices are started at the same time
            await asyncio.gather(*starts)
            self.pwr_low: int = 0


def await_chain(task: asyncio.Task) -> str:
    """The chain of awaits a task is suspended in, e.g. "powerChanged (manager.py:812) -> _http (device.py:1225) -> Future"."""
    parts: list[str] = []
    awaitable: Any = task.get_coro()
    for _ in range(40):
        frame = getattr(awaitable, "cr_frame", None) or getattr(awaitable, "gi_frame", None)
        if frame is None:
            parts.append(type(awaitable).__name__)
            break
        parts.append(f"{frame.f_code.co_name} ({frame.f_code.co_filename.rsplit('/', 1)[-1]}:{frame.f_lineno})")
        awaitable = getattr(awaitable, "cr_await", None) or getattr(awaitable, "gi_yieldfrom", None)
        if awaitable is None:
            break
    return " -> ".join(parts)
