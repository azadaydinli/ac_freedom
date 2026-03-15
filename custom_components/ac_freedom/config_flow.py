"""Config flow for AC Freedom integration.

Supports two connection modes:
  - Local: classic Broadlink UDP discovery / manual IP+MAC entry
  - Cloud: AUX Cloud login → device selection
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_IP_ADDRESS, CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .broadlink_ac_api import BroadlinkAcApi, DiscoveredDevice, discover_devices
from .cloud_api import AuxCloudAPI, AuxApiError
from .const import (
    CONF_CLOUD_DEVICES,
    CONF_CLOUD_EMAIL,
    CONF_CLOUD_FAMILY,
    CONF_CLOUD_PASSWORD,
    CONF_CLOUD_REGION,
    CONF_CONN_MODE,
    CONF_MAC,
    CONF_SWING,
    CONF_TEMP_STEP,
    CONN_CLOUD,
    CONN_LOCAL,
    DOMAIN,
    SWING_BOTH,
    SWING_HORIZONTAL,
    SWING_VERTICAL,
    TEMP_STEP_FULL,
    TEMP_STEP_HALF,
)

_LOGGER = logging.getLogger(__name__)

MAC_REGEX = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


class BroadlinkAcConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AC Freedom."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_devices: list[DiscoveredDevice] = []
        self._available_devices: list[DiscoveredDevice] = []
        self._selected_devices: list[DiscoveredDevice] = []
        # Cloud flow state
        self._cloud_api: AuxCloudAPI | None = None
        self._cloud_devices: list[dict] = []
        self._cloud_families: list[dict] = []
        self._cloud_email: str = ""
        self._cloud_password: str = ""
        self._cloud_region: str = "eu"

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BroadlinkAcOptionsFlow:
        return BroadlinkAcOptionsFlow(config_entry)

    # ── Step 1: Discover & show menu ────────────────────────────────
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """First step: scan local network, show menu with action buttons."""
        # Run UDP discovery
        self._discovered_devices = await discover_devices(timeout=5.0)

        existing_uids = {
            entry.unique_id
            for entry in self._async_current_entries()
            if entry.unique_id
        }
        self._available_devices = [
            d for d in self._discovered_devices
            if d.unique_id not in existing_uids
        ]

        # Build menu: only show "select_devices" if we found any
        menu_options: list[str] = []
        if self._available_devices:
            menu_options.append("select_devices")
        menu_options.extend(["rescan", "manual", "cloud_login"])

        return self.async_show_menu(
            step_id="user",
            menu_options=menu_options,
            description_placeholders={
                "count": str(len(self._available_devices)),
            },
        )

    # ── Step 1a: Rescan network ─────────────────────────────────────
    async def async_step_rescan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-run UDP discovery and return to menu."""
        return await self.async_step_user()

    # ── Step 1b: Select from discovered devices ─────────────────────
    async def async_step_select_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show discovered devices as a multi-select list."""
        if user_input is not None:
            selected = user_input.get("devices", [])
            self._selected_devices = [
                d for d in self._available_devices
                if d.unique_id in selected
            ]
            if not self._selected_devices:
                return self.async_abort(reason="no_devices_selected")
            return await self._create_entries_for_selected()

        select_options: list[SelectOptionDict] = []
        for dev in self._available_devices:
            select_options.append(
                SelectOptionDict(value=dev.unique_id, label=dev.display_name)
            )

        return self.async_show_form(
            step_id="select_devices",
            data_schema=vol.Schema({
                vol.Required("devices"): SelectSelector(
                    SelectSelectorConfig(
                        options=select_options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }),
            description_placeholders={
                "count": str(len(self._available_devices)),
            },
        )

    # ── Local: create single entry for all selected devices ─────────
    async def _create_entries_for_selected(self) -> FlowResult:
        verified: list[dict] = []
        for device in self._selected_devices:
            api = BroadlinkAcApi(device.ip, device.mac)
            try:
                connected = await api.connect()
                if connected:
                    await api.disconnect()
                    verified.append({
                        CONF_NAME: f"AC Freedom ({device.ip})",
                        CONF_IP_ADDRESS: device.ip,
                        CONF_MAC: device.mac,
                    })
            except Exception:
                _LOGGER.exception("Error connecting to %s", device.ip)
            finally:
                await api.disconnect()

        if not verified:
            return self.async_abort(reason="cannot_connect_any")

        # Use combined unique_id for the local group
        uid = "local_" + "_".join(
            d[CONF_MAC].replace(":", "") for d in verified
        )
        await self.async_set_unique_id(uid)
        self._abort_if_unique_id_configured()

        count = len(verified)
        title = f"AC Freedom (Local · {count})"
        return self.async_create_entry(
            title=title,
            data={
                CONF_CONN_MODE: CONN_LOCAL,
                "local_devices": verified,
            },
        )

    # ── Local: manual entry ─────────────────────────────────────────
    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            ip = user_input[CONF_IP_ADDRESS].strip()
            mac = user_input[CONF_MAC].strip().upper()
            name = user_input[CONF_NAME].strip() or f"AC Freedom ({ip})"

            if not MAC_REGEX.match(mac):
                errors[CONF_MAC] = "invalid_mac"
            else:
                api = BroadlinkAcApi(ip, mac)
                try:
                    connected = await api.connect()
                    if connected:
                        await api.disconnect()
                        uid = f"local_{mac.replace(':', '')}"
                        await self.async_set_unique_id(uid)
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=f"AC Freedom (Local · 1)",
                            data={
                                CONF_CONN_MODE: CONN_LOCAL,
                                "local_devices": [{
                                    CONF_NAME: name,
                                    CONF_IP_ADDRESS: ip,
                                    CONF_MAC: mac,
                                }],
                            },
                        )
                    errors["base"] = "cannot_connect"
                except Exception:
                    errors["base"] = "cannot_connect"
                finally:
                    await api.disconnect()

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default="AC Freedom"): str,
                vol.Required(CONF_IP_ADDRESS): str,
                vol.Required(CONF_MAC): str,
            }),
            errors=errors,
        )

    # ── Cloud: login ────────────────────────────────────────────────
    async def async_step_cloud_login(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_CLOUD_EMAIL].strip()
            password = user_input[CONF_CLOUD_PASSWORD]
            region = user_input[CONF_CLOUD_REGION]

            self._cloud_api = AuxCloudAPI(region=region)
            try:
                await self._cloud_api.login(email, password)
                self._cloud_email = email
                self._cloud_password = password
                self._cloud_region = region
                return await self.async_step_cloud_devices()
            except AuxApiError as exc:
                _LOGGER.error("Cloud login failed: %s", exc)
                errors["base"] = "cloud_login_failed"
            except Exception as exc:
                _LOGGER.exception("Unexpected cloud error: %s", exc)
                errors["base"] = "cloud_login_failed"

        return self.async_show_form(
            step_id="cloud_login",
            data_schema=vol.Schema({
                vol.Required(CONF_CLOUD_EMAIL): str,
                vol.Required(CONF_CLOUD_PASSWORD): str,
                vol.Required(CONF_CLOUD_REGION, default="eu"): vol.In({
                    "eu": "Europe",
                    "usa": "USA",
                    "cn": "China",
                    "rus": "Russia",
                }),
            }),
            errors=errors,
        )

    # ── Cloud: select devices ───────────────────────────────────────
    async def async_step_cloud_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = user_input.get("cloud_devices", [])
            if not selected:
                return self.async_abort(reason="no_devices_selected")

            await self.async_set_unique_id(f"cloud_{self._cloud_email}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"AUX Cloud ({self._cloud_email})",
                data={
                    CONF_CONN_MODE: CONN_CLOUD,
                    CONF_CLOUD_EMAIL: self._cloud_email,
                    CONF_CLOUD_PASSWORD: self._cloud_password,
                    CONF_CLOUD_REGION: self._cloud_region,
                    CONF_CLOUD_DEVICES: selected,
                },
            )

        # Fetch device list
        try:
            self._cloud_families = await self._cloud_api.get_families()
            self._cloud_devices = []
            for fam in self._cloud_families:
                devs = await self._cloud_api.get_devices(fam["familyid"])
                self._cloud_devices.extend(devs)
                # Also fetch shared devices
                try:
                    shared = await self._cloud_api.get_devices(
                        fam["familyid"], shared=True
                    )
                    self._cloud_devices.extend(shared)
                except Exception:
                    pass
        except AuxApiError as exc:
            _LOGGER.error("Failed to fetch devices: %s", exc)
            errors["base"] = "cloud_fetch_failed"
            return await self.async_step_cloud_login()

        if not self._cloud_devices:
            return self.async_abort(reason="no_cloud_devices")

        select_options = []
        for dev in self._cloud_devices:
            did = dev["endpointId"]
            fname = dev.get("friendlyName", "AUX Device")
            online = "online" if dev.get("state") == 1 else "offline"
            select_options.append(
                SelectOptionDict(
                    value=did,
                    label=f"{fname} ({online})",
                )
            )

        return self.async_show_form(
            step_id="cloud_devices",
            data_schema=vol.Schema({
                vol.Required("cloud_devices"): SelectSelector(
                    SelectSelectorConfig(
                        options=select_options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }),
            errors=errors,
        )


class BroadlinkAcOptionsFlow(OptionsFlow):
    """Handle options for AC Freedom."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._config_entry.data, **self._config_entry.options}
        conn_mode = current.get(CONF_CONN_MODE, CONN_LOCAL)

        if conn_mode == CONN_CLOUD:
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({
                    vol.Optional(
                        CONF_TEMP_STEP,
                        default=current.get(CONF_TEMP_STEP, TEMP_STEP_HALF),
                    ): vol.In({TEMP_STEP_HALF: "0.5 C", TEMP_STEP_FULL: "1 C"}),
                }),
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_TEMP_STEP,
                    default=current.get(CONF_TEMP_STEP, TEMP_STEP_HALF),
                ): vol.In({TEMP_STEP_HALF: "0.5 C", TEMP_STEP_FULL: "1 C"}),
                vol.Optional(
                    CONF_SWING,
                    default=current.get(CONF_SWING, SWING_BOTH),
                ): vol.In({
                    SWING_HORIZONTAL: "Horizontal",
                    SWING_VERTICAL: "Vertical",
                    SWING_BOTH: "Both",
                }),
            }),
        )
