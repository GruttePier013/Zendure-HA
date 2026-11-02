"""Config flow for Zendure HA-GP013."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .api import Api, ApiError
from .const import (
    CONF_APPTOKEN,
    CONF_AUTO_MQTT_USER,
    CONF_DEVICE_IPS,
    CONF_EXPORT_BIAS,
    CONF_FILL_LEVEL,
    CONF_FLASH_SAFE,
    CONF_FLIP_DELAY,
    CONF_MANUAL_INTERVAL,
    CONF_MIN_RUN,
    CONF_MQTTLOCAL,
    CONF_MQTTLOG,
    CONF_MQTTPORT,
    CONF_MQTTPSW,
    CONF_MQTTSERVER,
    CONF_MQTTUSER,
    CONF_P1_STDDEV_MIN,
    CONF_P1_HOMEWIZARD,
    CONF_P1_RECORD,
    CONF_P1_TOKEN,
    CONF_P1METER,
    CONF_PEAK_LIMIT,
    CONF_READY_RATE,
    CONF_POWER_START,
    CONF_POWER_TOLERANCE,
    CONF_ROLE_MARGIN,
    CONF_SIM,
    CONF_SLEW_DOWN,
    CONF_SLEW_ENABLED,
    CONF_SLEW_UP,
    CONF_TIMEFAST,
    CONF_TIMEZERO,
    CONF_WIFIPSW,
    CONF_WIFISSID,
    CONTROL_DEFAULTS,
    DOMAIN,
)
from .manager import ZendureConfigEntry
from .slew import DEFAULT_DOWN, DEFAULT_FLIP_DELAY, DEFAULT_MIN_RUN, DEFAULT_READY_UP, DEFAULT_UP

_LOGGER = logging.getLogger(__name__)


class ZendureConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zendure HA-GP013."""

    VERSION = 1
    MINOR_VERSION = 8
    _input_data: dict[str, Any]
    data_schema = vol.Schema(
        {
            vol.Required(CONF_APPTOKEN): str,
            vol.Required(CONF_P1METER, description={"suggested_value": "sensor.power_actual"}): selector.EntitySelector(),
            vol.Required(CONF_MQTTLOG): bool,
            vol.Required(CONF_MQTTLOCAL): bool,
        }
    )
    mqtt_schema = vol.Schema(
        {
            vol.Required(CONF_MQTTSERVER): str,
            vol.Required(CONF_MQTTPORT, default=1883): int,
            vol.Required(CONF_MQTTUSER): str,
            vol.Optional(CONF_MQTTPSW): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                ),
            ),
            vol.Optional(CONF_AUTO_MQTT_USER, default=False): bool,
            vol.Optional(CONF_WIFISSID): str,
            vol.Optional(CONF_WIFIPSW): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                ),
            ),
        }
    )

    def __init__(self) -> None:
        """Initialize."""
        self._user_input: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step when user initializes a integration."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            self._user_input = user_input

            try:
                await Api.Connect(self.hass, self._user_input, False)
            except ApiError as err:
                errors["base"] = err.key
                placeholders["detail"] = err.detail
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Unexpected exception: %s", err)
                errors["base"] = "unknown"
            else:
                localmqtt = user_input[CONF_MQTTLOCAL]
                if localmqtt:
                    return await self.async_step_local()

                await self.async_set_unique_id("Zendure", raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Zendure HA-GP013", data=self._user_input)

        return self.async_show_form(step_id="user", data_schema=self.data_schema, errors=errors, description_placeholders=placeholders)

    async def async_step_local(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None and user_input.get(CONF_MQTTSERVER, None) is not None:
            try:
                self._user_input = self._user_input | user_input if self._user_input else user_input
                await Api.Connect(self.hass, self._user_input, False)
            except ApiError as err:
                errors["base"] = err.key
                placeholders["detail"] = err.detail
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Unexpected exception: %s", err)
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id("Zendure", raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Zendure HA-GP013", data=self._user_input)

        return self.async_show_form(step_id="local", data_schema=self.mqtt_schema, errors=errors, description_placeholders=placeholders)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add reconfigure step to allow to reconfigure a config entry."""
        errors: dict[str, str] = {}

        entry = self._get_reconfigure_entry()
        schema = self.data_schema
        placeholders: dict[str, str] = {}
        if user_input is not None:
            self._user_input = self._user_input | user_input
            use_mqtt = user_input.get(CONF_MQTTLOCAL, False)
            if use_mqtt:
                schema = self.mqtt_schema
            else:
                try:
                    await Api.Connect(self.hass, self._user_input, False)
                except ApiError as err:
                    errors["base"] = err.key
                    placeholders["detail"] = err.detail
                except Exception as err:  # pylint: disable=broad-except
                    _LOGGER.error("Unexpected exception: %s", err)
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id("Zendure", raise_on_progress=False)
                    self._abort_if_unique_id_mismatch()

                    # merge: settings that are not on this form (simulation, slew rate, addresses, ...) are kept
                    return self.async_update_reload_and_abort(entry, data_updates=self._user_input)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                data_schema=schema,
                suggested_values=entry.data | (user_input or {}),
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(_config_entry: ZendureConfigEntry) -> ZendureOptionsFlowHandler:
        """Get the options flow for this handler."""
        return ZendureOptionsFlowHandler()


class ZendureOptionsFlowHandler(OptionsFlow):
    """Options flow: a menu with one entry per group of settings.

    Every step stores its values directly in the config entry and returns to the menu. Changes
    that need a new connection (token, MQTT) trigger one reload when the menu is finished.
    """

    MENU = ["connection", "mqtt", "p1_meter", "power_control", "slew", "lifetime", "device_ips", "diagnostics", "finish"]

    def __init__(self) -> None:
        """Initialize."""
        self._reload = False

    # ----------------------------------------------------------------------------------------
    # helpers
    # ----------------------------------------------------------------------------------------
    def _store(self, values: dict[str, Any], reload: bool = False) -> None:
        """Merge values into the config entry data."""
        self.hass.config_entries.async_update_entry(self.config_entry, data={**self.config_entry.data, **values})
        self._reload |= reload
        if not reload and self.config_entry.state is ConfigEntryState.LOADED:
            from . import apply_entry_settings

            apply_entry_settings(self.config_entry)

    def _setting(self, key: str, default: Any = None) -> Any:
        return self.config_entry.data.get(key, default)

    @staticmethod
    def _number(minimum: float, maximum: float, step: float, unit: str | None = None) -> selector.NumberSelector:
        config = selector.NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=selector.NumberSelectorMode.BOX)
        if unit:
            config["unit_of_measurement"] = unit
        return selector.NumberSelector(config)

    def _word(self, key: str) -> str:
        """A word of the settings overview in the language of Home Assistant (English when unknown)."""
        language = (self.hass.config.language or "en").split("-")[0]
        return SUMMARY_WORDS.get(language, SUMMARY_WORDS["en"]).get(key, SUMMARY_WORDS["en"][key])

    def _record_summary(self) -> str:
        """The P1 recording, when it runs today."""
        from homeassistant.util import dt as dt_util

        if self._setting(CONF_P1_RECORD, "") != dt_util.now().date().isoformat():
            return ""
        return f", {self._word('recording')} p1_{dt_util.now().date().isoformat()}.csv"

    def _p1_summary(self) -> str:
        """How the HomeWizard P1 meter is read: API version, whose token, and whether it answers now."""
        from .p1direct import homewizard_entry

        data = self.config_entry.data
        if not (host := data.get(CONF_P1_HOMEWIZARD)):
            return ""
        if data.get(CONF_P1_TOKEN):
            how = f"API v2, {self._word('own_token')}"
        else:
            hw_host, hw_token = homewizard_entry(self.hass, data.get(CONF_P1METER))
            how = f"API v2, {self._word('hw_token')}" if hw_token and hw_host == host else "API v1"
        meter = getattr(getattr(self.config_entry, "runtime_data", None), "p1direct", None)
        if meter is None:
            state = self._word("not_running")
        elif meter.healthy:
            state = self._word("every_second")
        elif meter.readings == 0 and meter.failures == 0:
            state = self._word("connecting")
        else:
            state = self._word("not_answering")
        return f" + HomeWizard {host} ({how}, {state})"

    def _summary(self) -> dict[str, str]:
        """Overview of the current settings, shown above the menu."""
        data = self.config_entry.data
        local = data.get(CONF_MQTTLOCAL, False)
        slew = data.get(CONF_SLEW_ENABLED, True)
        w = self._word

        def onoff(value: bool) -> str:
            return w("on") if value else w("off")

        return {
            "p1meter": str(data.get(CONF_P1METER) or "-") + self._p1_summary(),
            "token": w("set") if data.get(CONF_APPTOKEN) else w("missing"),
            "mqtt": f"{w('local')} ({data.get(CONF_MQTTSERVER, '?')}:{data.get(CONF_MQTTPORT, 1883)})" if local else w("cloud"),
            "control": (
                f"{_n(data.get(CONF_TIMEZERO, CONTROL_DEFAULTS[CONF_TIMEZERO]))} s / "
                f"{_n(data.get(CONF_TIMEFAST, CONTROL_DEFAULTS[CONF_TIMEFAST]))} s, "
                f"{w('start')} {_n(data.get(CONF_POWER_START, CONTROL_DEFAULTS[CONF_POWER_START]))} W"
            ),
            "slew": (
                f"{_n(data.get(CONF_SLEW_UP, DEFAULT_UP))} / {_n(data.get(CONF_SLEW_DOWN, DEFAULT_DOWN))} W/s, {_n(data.get(CONF_FLIP_DELAY, DEFAULT_FLIP_DELAY))} s"
                if slew
                else w("off")
            ),
            "lifetime": (
                f"{w('spread')} {_n(data.get(CONF_FILL_LEVEL, CONTROL_DEFAULTS[CONF_FILL_LEVEL]))}%, "
                f"{w('role_margin')} {_n(data.get(CONF_ROLE_MARGIN, CONTROL_DEFAULTS[CONF_ROLE_MARGIN]))}%, "
                f"{w('flash')} {onoff(data.get(CONF_FLASH_SAFE, True))}"
            ),
            "addresses": str(len(data.get(CONF_DEVICE_IPS) or {})),
            "logging": onoff(data.get(CONF_MQTTLOG, False)),
            "simulation": onoff(data.get(CONF_SIM, False)) + self._record_summary(),
        }

    # ----------------------------------------------------------------------------------------
    # menu
    # ----------------------------------------------------------------------------------------
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the overview and the menu with the groups of settings."""
        return self.async_show_menu(step_id="init", menu_options=self.MENU, description_placeholders=self._summary())

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Close the menu; reload once when a connection setting was changed."""
        if self._reload:
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return self.async_create_entry(title="", data={})

    # ----------------------------------------------------------------------------------------
    # connection (token) - validated against the Zendure API
    # ----------------------------------------------------------------------------------------
    async def async_step_connection(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"detail": ""}
        if user_input is not None:
            candidate = {**self.config_entry.data, **user_input}
            try:
                await Api.Connect(self.hass, candidate, False)
            except ApiError as err:
                errors["base"] = err.key
                placeholders["detail"] = err.detail
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Unexpected exception: %s", err)
                errors["base"] = "unknown"
            else:
                self._store(user_input, reload=user_input.get(CONF_APPTOKEN) != self._setting(CONF_APPTOKEN))
                return await self.async_step_init()

        schema = vol.Schema({vol.Required(CONF_APPTOKEN): str})
        return self.async_show_form(
            step_id="connection",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or self.config_entry.data),
            errors=errors,
            description_placeholders=placeholders,
        )

    # ----------------------------------------------------------------------------------------
    # MQTT (cloud / local broker, wifi used for provisioning)
    # ----------------------------------------------------------------------------------------
    async def async_step_mqtt(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"detail": ""}
        password = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
        if user_input is not None:
            values = dict(user_input)
            # an empty password field keeps the stored password
            for key in (CONF_MQTTPSW, CONF_WIFIPSW):
                if not values.get(key):
                    values.pop(key, None)
            candidate = {**self.config_entry.data, **values}
            if values.get(CONF_MQTTLOCAL) and not values.get(CONF_MQTTSERVER):
                errors[CONF_MQTTSERVER] = "mqtt_server_required"
            else:
                try:
                    await Api.Connect(self.hass, candidate, False)
                except ApiError as err:
                    errors["base"] = err.key
                    placeholders["detail"] = err.detail
                except Exception as err:  # pylint: disable=broad-except
                    _LOGGER.error("Unexpected exception: %s", err)
                    errors["base"] = "unknown"
                else:
                    changed = any(values.get(k) != self._setting(k) for k in values)
                    self._store(values, reload=changed)
                    return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required(CONF_MQTTLOCAL, default=self._setting(CONF_MQTTLOCAL, False)): bool,
                vol.Optional(CONF_MQTTSERVER): str,
                vol.Optional(CONF_MQTTPORT, default=self._setting(CONF_MQTTPORT, 1883)): self._number(1, 65535, 1),
                vol.Optional(CONF_MQTTUSER): str,
                vol.Optional(CONF_MQTTPSW): password,
                vol.Optional(CONF_AUTO_MQTT_USER, default=self._setting(CONF_AUTO_MQTT_USER, False)): bool,
                vol.Optional(CONF_WIFISSID): str,
                vol.Optional(CONF_WIFIPSW): password,
            }
        )
        suggested = {k: v for k, v in (user_input or self.config_entry.data).items() if k not in (CONF_MQTTPSW, CONF_WIFIPSW)}
        return self.async_show_form(
            step_id="mqtt",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders=placeholders,
        )

    # ----------------------------------------------------------------------------------------
    # P1 meter
    # ----------------------------------------------------------------------------------------
    async def async_step_p1_meter(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        from .p1direct import UnauthorizedError, homewizard_entry, read_once

        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input.get(CONF_P1_HOMEWIZARD) or "").strip()
            sensor = user_input[CONF_P1METER]
            if not host:  # an emptied field is not sent: store "" to switch reading the meter directly off
                self._store({CONF_P1METER: sensor, CONF_P1_HOMEWIZARD: "", CONF_P1_TOKEN: ""})
                return await self.async_step_init()

            # a token that is known for this address: ours, or the one of the HomeWizard integration (API v2)
            token = self._setting(CONF_P1_TOKEN) if host == self._setting(CONF_P1_HOMEWIZARD) else None
            hw_host, hw_token = homewizard_entry(self.hass, sensor)
            for candidate, own in ((token, True), (hw_token if hw_host == host else None, False)):
                if not candidate:
                    continue
                try:
                    if await read_once(self.hass, host, candidate) is not None:
                        self._store({CONF_P1METER: sensor, CONF_P1_HOMEWIZARD: host, CONF_P1_TOKEN: candidate if own else ""})
                        return await self.async_step_init()
                except UnauthorizedError:
                    continue
                except Exception:  # noqa: BLE001 - tried again with a token of our own in the next step
                    continue
            self._p1_pending = {CONF_P1METER: sensor, CONF_P1_HOMEWIZARD: host}
            return await self.async_step_p1_token()

        schema = vol.Schema(
            {
                vol.Required(CONF_P1METER): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional(CONF_P1_HOMEWIZARD): selector.TextSelector(),
            }
        )
        current = dict(self.config_entry.data)
        if not current.get(CONF_P1_HOMEWIZARD) and (found := homewizard_entry(self.hass, current.get(CONF_P1METER))[0]):
            current[CONF_P1_HOMEWIZARD] = found  # suggested: the address of the HomeWizard meter of the P1 sensor
        return self.async_show_form(step_id="p1_meter", data_schema=self.add_suggested_values_to_schema(schema, current), errors=errors)

    async def async_step_p1_token(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Get an API v2 token from the HomeWizard P1 meter (press its button), or use API v1."""
        from .p1direct import read_once, request_token

        pending = getattr(self, "_p1_pending", None)
        if pending is None:
            return await self.async_step_p1_meter()
        host = pending[CONF_P1_HOMEWIZARD]
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("use_v1"):
                try:
                    ok = await read_once(self.hass, host, None) is not None
                except Exception:  # noqa: BLE001
                    ok = False
                if ok:
                    self._store({**pending, CONF_P1_TOKEN: ""})
                    return await self.async_step_init()
                errors["base"] = "homewizard_v1_unreachable"
            else:
                token, reason = await request_token(self.hass, host)
                if token:
                    self._store({**pending, CONF_P1_TOKEN: token})
                    return await self.async_step_init()
                errors["base"] = "homewizard_press_button" if reason == "button" else "homewizard_unreachable"
                if reason != "button":
                    _LOGGER.warning("No token from HomeWizard P1 %s: %s", host, reason)

        schema = vol.Schema({vol.Optional("use_v1", default=False): selector.BooleanSelector()})
        return self.async_show_form(step_id="p1_token", data_schema=schema, errors=errors, description_placeholders={"host": host})

    # ----------------------------------------------------------------------------------------
    # power control (how fast and how sensitive the manager follows the P1 meter)
    # ----------------------------------------------------------------------------------------
    async def async_step_power_control(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if float(user_input[CONF_TIMEFAST]) > float(user_input[CONF_TIMEZERO]):
                errors[CONF_TIMEFAST] = "fast_longer_than_normal"
            else:
                self._store(user_input)
                return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required(CONF_TIMEZERO): self._number(1, 60, 0.1, "s"),
                vol.Required(CONF_TIMEFAST): self._number(0.5, 60, 0.1, "s"),
                vol.Required(CONF_P1_STDDEV_MIN): self._number(1, 500, 1, "W"),
                vol.Required(CONF_POWER_START): self._number(10, 500, 1, "W"),
                vol.Required(CONF_POWER_TOLERANCE): self._number(0, 100, 1, "W"),
                vol.Required(CONF_MANUAL_INTERVAL): self._number(4, 300, 1, "s"),
                vol.Required(CONF_PEAK_LIMIT): self._number(0, 20000, 50, "W"),
                vol.Required(CONF_EXPORT_BIAS): self._number(0, 500, 10, "W"),
            }
        )
        current = {key: self._setting(key, default) for key, default in CONTROL_DEFAULTS.items()}
        return self.async_show_form(
            step_id="power_control",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or current),
            errors=errors,
        )

    # ----------------------------------------------------------------------------------------
    # slew rate / direction change protection
    # ----------------------------------------------------------------------------------------
    async def async_step_slew(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._store(user_input)
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required(CONF_SLEW_ENABLED): bool,
                vol.Required(CONF_SLEW_UP): self._number(50, 5000, 10, "W/s"),
                vol.Required(CONF_SLEW_DOWN): self._number(50, 10000, 10, "W/s"),
                vol.Required(CONF_FLIP_DELAY): self._number(0, 60, 1, "s"),
                vol.Required(CONF_MIN_RUN): self._number(0, 120, 1, "s"),
                vol.Required(CONF_READY_RATE): self._number(50, 5000, 10, "W/s"),
            }
        )
        current = {
            CONF_SLEW_ENABLED: self._setting(CONF_SLEW_ENABLED, True),
            CONF_SLEW_UP: self._setting(CONF_SLEW_UP, DEFAULT_UP),
            CONF_SLEW_DOWN: self._setting(CONF_SLEW_DOWN, DEFAULT_DOWN),
            CONF_FLIP_DELAY: self._setting(CONF_FLIP_DELAY, DEFAULT_FLIP_DELAY),
            CONF_MIN_RUN: self._setting(CONF_MIN_RUN, DEFAULT_MIN_RUN),
            CONF_READY_RATE: self._setting(CONF_READY_RATE, DEFAULT_READY_UP),
        }
        return self.async_show_form(step_id="slew", data_schema=self.add_suggested_values_to_schema(schema, current))

    # ----------------------------------------------------------------------------------------
    # lifetime: roles of multiple devices and flash protection
    # ----------------------------------------------------------------------------------------
    async def async_step_lifetime(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._store(user_input)
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required(CONF_ROLE_MARGIN): self._number(0, 50, 1, "%"),
                vol.Required(CONF_FILL_LEVEL): self._number(20, 100, 5, "%"),
                vol.Required(CONF_FLASH_SAFE): bool,
            }
        )
        current = {
            CONF_ROLE_MARGIN: self._setting(CONF_ROLE_MARGIN, CONTROL_DEFAULTS[CONF_ROLE_MARGIN]),
            CONF_FILL_LEVEL: self._setting(CONF_FILL_LEVEL, CONTROL_DEFAULTS[CONF_FILL_LEVEL]),
            CONF_FLASH_SAFE: self._setting(CONF_FLASH_SAFE, True),
        }
        return self.async_show_form(step_id="lifetime", data_schema=self.add_suggested_values_to_schema(schema, current))

    # ----------------------------------------------------------------------------------------
    # fixed addresses of zenSDK devices (cloud / mDNS does not report one across VLANs)
    # ----------------------------------------------------------------------------------------
    def _zensdk_devices(self) -> dict[str, Any]:
        """Serial number -> device, for the devices that can be read over the local zenSDK."""
        from .device import ZendureZenSdk

        manager = getattr(self.config_entry, "runtime_data", None)
        return {d.snNumber: d for d in getattr(manager, "devices", []) if isinstance(d, ZendureZenSdk)}

    async def async_step_device_ips(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        devices = self._zensdk_devices()
        known = dict(self._setting(CONF_DEVICE_IPS) or {})
        serials = list(dict.fromkeys([*devices, *known]))
        if user_input is not None:
            addresses: dict[str, str] = {}
            for serial in serials:
                value = str(user_input.get(serial, "")).strip()
                if value and not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", value):
                    errors[serial] = "invalid_address"
                elif value:
                    addresses[serial] = value
            if not errors:
                self._store({CONF_DEVICE_IPS: addresses})
                return await self.async_step_init()

        schema = vol.Schema({vol.Optional(serial): str for serial in serials})
        sources = {"cloud": "cloud", "manual": "manual", "discovery": "mDNS", "guess": "GUESSED name, probably not reachable"}

        def describe(serial: str) -> str:
            if (d := devices.get(serial)) is None:
                return f"{serial} = ? (device not loaded)"
            return f"{serial} = {d.name}: {d.ipAddress} ({sources.get(getattr(d, 'address_source', ''), '?')})"

        listing = "\n".join(describe(serial) for serial in serials) or "-"
        # prefill with the address in use when it is a real one (cloud or mDNS), so it can be checked or fixed
        suggested = {
            serial: d.ipAddress for serial, d in devices.items() if getattr(d, "address_source", "") in ("cloud", "discovery")
        }
        suggested.update(known)
        return self.async_show_form(
            step_id="device_ips",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or suggested),
            errors=errors,
            description_placeholders={"devices": listing},
        )

    # ----------------------------------------------------------------------------------------
    # diagnostics / logging
    # ----------------------------------------------------------------------------------------
    async def async_step_diagnostics(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        from homeassistant.util import dt as dt_util

        today = dt_util.now().date().isoformat()
        if user_input is not None:
            # the recording runs on the stored date only and switches itself off at midnight
            user_input[CONF_P1_RECORD] = today if user_input.get(CONF_P1_RECORD) else ""
            self._store(user_input)
            return await self.async_step_init()

        schema = vol.Schema({vol.Required(CONF_MQTTLOG): bool, vol.Required(CONF_SIM): bool, vol.Optional(CONF_P1_RECORD, default=False): bool})
        current = {
            CONF_MQTTLOG: self._setting(CONF_MQTTLOG, False),
            CONF_SIM: self._setting(CONF_SIM, False),
            CONF_P1_RECORD: self._setting(CONF_P1_RECORD, "") == today,
        }
        return self.async_show_form(step_id="diagnostics", data_schema=self.add_suggested_values_to_schema(schema, current))


class ZendureConnectionError(HomeAssistantError):
    """Error to indicate there is a connection issue with Zendure HA-GP013."""

    def __init__(self) -> None:
        """Initialize the connection error."""
        super().__init__("Zendure HA-GP013")


def _n(value: Any) -> str:
    """A number without a needless .0 (70.0 -> 70, 4.5 -> 4.5)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


# words of the settings overview above the options menu (the placeholders are not translated by Home Assistant)
SUMMARY_WORDS: dict[str, dict[str, str]] = {
    "en": {"recording": "P1 recording until 00:00 in", "set": "set", "missing": "missing", "local": "local", "cloud": "cloud", "start": "start", "on": "on", "off": "off",
           "spread": "spread threshold", "role_margin": "role switch margin", "flash": "safe state in flash",
           "own_token": "own token", "hw_token": "token of the HomeWizard integration", "every_second": "every second",
           "connecting": "connecting", "not_answering": "not answering, the sensor is used", "not_running": "not running"},
    "nl": {"recording": "P1-opname tot 00:00 in", "set": "ingesteld", "missing": "ontbreekt", "local": "lokaal", "cloud": "cloud", "start": "start", "on": "aan", "off": "uit",
           "spread": "spreiddrempel", "role_margin": "rolwisselmarge", "flash": "veilige stand in flash",
           "own_token": "eigen token", "hw_token": "token van de HomeWizard-integratie", "every_second": "elke seconde",
           "connecting": "verbinden", "not_answering": "antwoordt niet, de sensor wordt gebruikt", "not_running": "niet actief"},
    "de": {"recording": "P1-Aufzeichnung bis 00:00 in", "set": "gesetzt", "missing": "fehlt", "local": "lokal", "cloud": "Cloud", "start": "Start", "on": "an", "off": "aus",
           "spread": "Verteilschwelle", "role_margin": "Rollenwechsel-Marge", "flash": "sicherer Zustand im Flash",
           "own_token": "eigenes Token", "hw_token": "Token der HomeWizard-Integration", "every_second": "jede Sekunde",
           "connecting": "verbinde", "not_answering": "antwortet nicht, der Sensor wird verwendet", "not_running": "nicht aktiv"},
    "fr": {"recording": "enregistrement P1 jusqu'à 00:00 dans", "set": "défini", "missing": "manquant", "local": "local", "cloud": "cloud", "start": "démarrage", "on": "activé", "off": "désactivé",
           "spread": "seuil de répartition", "role_margin": "marge de changement de rôle", "flash": "état sûr en flash",
           "own_token": "jeton propre", "hw_token": "jeton de l'intégration HomeWizard", "every_second": "chaque seconde",
           "connecting": "connexion", "not_answering": "ne répond pas, le capteur est utilisé", "not_running": "inactif"},
    "pl": {"recording": "nagrywanie P1 do 00:00 w", "set": "ustawiony", "missing": "brak", "local": "lokalny", "cloud": "chmura", "start": "start", "on": "wł.", "off": "wył.",
           "spread": "próg rozkładu", "role_margin": "margines zmiany roli", "flash": "stan bezpieczny we flashu",
           "own_token": "własny token", "hw_token": "token integracji HomeWizard", "every_second": "co sekundę",
           "connecting": "łączenie", "not_answering": "nie odpowiada, używany jest czujnik", "not_running": "nieaktywny"},
}
