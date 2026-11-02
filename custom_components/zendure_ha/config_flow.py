"""Config flow for Zendure HA-GP013."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .api import Api, ApiError
from .const import (
    CONF_APPTOKEN,
    CONF_AUTO_MQTT_USER,
    CONF_DEVICE_IPS,
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

    def _setting(self, key: str, default: Any = None) -> Any:
        return self.config_entry.data.get(key, default)

    @staticmethod
    def _number(minimum: float, maximum: float, step: float, unit: str | None = None) -> selector.NumberSelector:
        config = selector.NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=selector.NumberSelectorMode.BOX)
        if unit:
            config["unit_of_measurement"] = unit
        return selector.NumberSelector(config)

    def _summary(self) -> dict[str, str]:
        """Overview of the current settings, shown above the menu."""
        data = self.config_entry.data
        local = data.get(CONF_MQTTLOCAL, False)
        slew = data.get(CONF_SLEW_ENABLED, True)
        return {
            "p1meter": str(data.get(CONF_P1METER) or "-"),
            "token": "set" if data.get(CONF_APPTOKEN) else "missing",
            "mqtt": f"local ({data.get(CONF_MQTTSERVER, '?')}:{data.get(CONF_MQTTPORT, 1883)})" if local else "cloud",
            "control": (
                f"{data.get(CONF_TIMEZERO, CONTROL_DEFAULTS[CONF_TIMEZERO])} s / "
                f"{data.get(CONF_TIMEFAST, CONTROL_DEFAULTS[CONF_TIMEFAST])} s, "
                f"start {data.get(CONF_POWER_START, CONTROL_DEFAULTS[CONF_POWER_START])} W"
            ),
            "slew": (
                f"{data.get(CONF_SLEW_UP, DEFAULT_UP)} / {data.get(CONF_SLEW_DOWN, DEFAULT_DOWN)} W/s, {data.get(CONF_FLIP_DELAY, DEFAULT_FLIP_DELAY)} s"
                if slew
                else "off"
            ),
            "lifetime": (
                f"role margin {data.get(CONF_ROLE_MARGIN, CONTROL_DEFAULTS[CONF_ROLE_MARGIN])}%, "
                f"fill level {data.get(CONF_FILL_LEVEL, CONTROL_DEFAULTS[CONF_FILL_LEVEL])}%, "
                f"flash safe state {'on' if data.get(CONF_FLASH_SAFE, True) else 'off'}"
            ),
            "addresses": str(len(data.get(CONF_DEVICE_IPS) or {})),
            "logging": "on" if data.get(CONF_MQTTLOG, False) else "off",
            "simulation": "on" if data.get(CONF_SIM, False) else "off",
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
        if user_input is not None:
            self._store(user_input)
            return await self.async_step_init()

        schema = vol.Schema({vol.Required(CONF_P1METER): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))})
        return self.async_show_form(step_id="p1_meter", data_schema=self.add_suggested_values_to_schema(schema, self.config_entry.data))

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
    def _zensdk_devices(self) -> dict[str, str]:
        """Serial number -> name of the devices that are read over the local zenSDK."""
        from .device import ZendureZenSdk

        manager = getattr(self.config_entry, "runtime_data", None)
        return {d.snNumber: d.name for d in getattr(manager, "devices", []) if isinstance(d, ZendureZenSdk)}

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
        listing = "\n".join(f"{serial} = {devices.get(serial, '?')}" for serial in serials) or "-"
        return self.async_show_form(
            step_id="device_ips",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or known),
            errors=errors,
            description_placeholders={"devices": listing},
        )

    # ----------------------------------------------------------------------------------------
    # diagnostics / logging
    # ----------------------------------------------------------------------------------------
    async def async_step_diagnostics(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._store(user_input)
            return await self.async_step_init()

        schema = vol.Schema({vol.Required(CONF_MQTTLOG): bool, vol.Required(CONF_SIM): bool})
        current = {CONF_MQTTLOG: self._setting(CONF_MQTTLOG, False), CONF_SIM: self._setting(CONF_SIM, False)}
        return self.async_show_form(step_id="diagnostics", data_schema=self.add_suggested_values_to_schema(schema, current))


class ZendureConnectionError(HomeAssistantError):
    """Error to indicate there is a connection issue with Zendure HA-GP013."""

    def __init__(self) -> None:
        """Initialize the connection error."""
        super().__init__("Zendure HA-GP013")
