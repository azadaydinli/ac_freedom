"""Switch platform for AC Freedom.

Provides toggle switches for AC features:
  - Display (screen on/off)
  - Sleep mode
  - Health / Ionizer
  - Self Clean
  - Mildew Prevention (Eco)

These features also appear as climate preset_modes in HA.
The switches are kept because HomeKit cannot bridge preset_modes
(Apple HAP protocol limitation). Switches sync with preset state.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud_api.const import (
    AC_CLEAN,
    AC_HEALTH,
    AC_MILDEW_PROOF,
    AC_SCREEN_DISPLAY,
    AC_SLEEP,
)
from .const import (
    CONN_CLOUD,
    CONN_LOCAL,
    DOMAIN,
    SWITCH_CLEAN,
    SWITCH_DISPLAY,
    SWITCH_HEALTH,
    SWITCH_MILDEW,
    SWITCH_SLEEP,
)

_LOGGER = logging.getLogger(__name__)

# Local switch types: key → (name, icon, state_attr)
LOCAL_SWITCH_TYPES = {
    SWITCH_DISPLAY: ("Display", "mdi:monitor", "display"),
    SWITCH_SLEEP: ("Sleep Mode", "mdi:power-sleep", "sleep"),
    SWITCH_HEALTH: ("Health / Ionizer", "mdi:air-filter", "health"),
    SWITCH_CLEAN: ("Self Clean", "mdi:vacuum", "clean"),
    SWITCH_MILDEW: ("Eco / Mildew Prevention", "mdi:water-off", "mildew"),
}

# Cloud switch types: key → (name, icon, cloud_param_key)
CLOUD_SWITCH_TYPES = {
    SWITCH_DISPLAY: ("Display", "mdi:monitor", AC_SCREEN_DISPLAY),
    SWITCH_SLEEP: ("Sleep Mode", "mdi:power-sleep", AC_SLEEP),
    SWITCH_HEALTH: ("Health / Ionizer", "mdi:air-filter", AC_HEALTH),
    SWITCH_CLEAN: ("Self Clean", "mdi:vacuum", AC_CLEAN),
    SWITCH_MILDEW: ("Eco / Mildew Prevention", "mdi:water-off", AC_MILDEW_PROOF),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AC Freedom switch entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    conn_mode = data.get("mode", CONN_LOCAL)

    entities = []

    if conn_mode == CONN_CLOUD:
        coordinator = data["coordinator"]
        for dev in data.get("devices", []):
            for key, (name, icon, cloud_key) in CLOUD_SWITCH_TYPES.items():
                entities.append(
                    CloudSwitch(coordinator, dev, entry, key, name, icon, cloud_key)
                )
    else:
        for dev_entry in data.get("local_devices", []):
            for key, (name, icon, state_attr) in LOCAL_SWITCH_TYPES.items():
                entities.append(
                    LocalSwitch(
                        dev_entry["coordinator"], entry,
                        key, name, icon, state_attr, dev_entry["info"],
                    )
                )

    if entities:
        async_add_entities(entities)


class LocalSwitch(CoordinatorEntity, SwitchEntity):
    """A switch for local AC features."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator, entry, key, name, icon, state_attr, dev_info: dict,
    ) -> None:
        super().__init__(coordinator)
        self._api = coordinator.api
        self._key = key
        self._state_attr = state_attr
        ip = dev_info[CONF_IP_ADDRESS]
        mac = dev_info.get("mac", "ac")
        dev_name = dev_info.get(CONF_NAME, f"AC Freedom ({ip})")
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{ip}_{mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{ip}_{mac}")},
            name=dev_name,
            manufacturer="AUX",
            model="AC Freedom (Local)",
            sw_version="2.1.0",
        )

    @property
    def is_on(self) -> bool:
        return bool(getattr(self._api.state, self._state_attr, 0))

    async def async_turn_on(self, **kwargs: Any) -> None:
        setattr(self._api.state, self._state_attr, 1)
        await self._api.set_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        setattr(self._api.state, self._state_attr, 0)
        await self._api.set_state()
        await self.coordinator.async_request_refresh()


class CloudSwitch(CoordinatorEntity, SwitchEntity):
    """A switch for cloud AC features."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator, device, entry, key, name, icon, cloud_key,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._did = device["endpointId"]
        self._cloud_api = coordinator.cloud_api
        self._cloud_key = cloud_key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"cloud_{self._did}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._did)},
            name=device.get("friendlyName", "AUX AC"),
            manufacturer="AUX",
            model="AC Freedom (Cloud)",
            sw_version="2.1.0",
        )

    def _params(self) -> dict:
        if self.coordinator.data and self._did in self.coordinator.data:
            return self.coordinator.data[self._did].get("params", {})
        return self._device.get("params", {})

    @property
    def is_on(self) -> bool:
        return bool(self._params().get(self._cloud_key, 0))

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.coordinator.data and self._did in self.coordinator.data:
            self._device = self.coordinator.data[self._did]
        await self._cloud_api.set_device_params(self._device, {self._cloud_key: 1})
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.coordinator.data and self._did in self.coordinator.data:
            self._device = self.coordinator.data[self._did]
        await self._cloud_api.set_device_params(self._device, {self._cloud_key: 0})
        await self.coordinator.async_request_refresh()
