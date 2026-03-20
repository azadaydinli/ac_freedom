"""Switch platform for AC Freedom.

Provides toggle switches for AC features:
  - Display (screen on/off)
  - Sleep Mode
  - Health / Ionizer
  - Self Clean
  - Eco / Mildew Prevention

These share the same DeviceInfo as the climate entity so
HomeKit bridge groups them into one accessory card.
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
from .const import CONN_CLOUD, CONN_LOCAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

# key -> (name, icon, local_state_attr, cloud_param_key)
SWITCH_TYPES = {
    "display": ("Display", "mdi:monitor", "display", AC_SCREEN_DISPLAY),
    "sleep": ("Sleep Mode", "mdi:power-sleep", "sleep", AC_SLEEP),
    "health": ("Health", "mdi:air-filter", "health", AC_HEALTH),
    "clean": ("Self Clean", "mdi:vacuum", "clean", AC_CLEAN),
    "mildew": ("Eco", "mdi:water-off", "mildew", AC_MILDEW_PROOF),
}


def _local_device_info(dev_info: dict) -> DeviceInfo:
    ip = dev_info[CONF_IP_ADDRESS]
    mac = dev_info.get("mac", "ac")
    return DeviceInfo(
        identifiers={(DOMAIN, f"{ip}_{mac}")},
        name=dev_info.get(CONF_NAME, f"AC Freedom ({ip})"),
        manufacturer="AUX",
        model="AC Freedom (Local)",
    )


def _cloud_device_info(device: dict) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, device["endpointId"])},
        name=device.get("friendlyName", "AUX AC"),
        manufacturer="AUX",
        model="AC Freedom (Cloud)",
    )


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
            info = _cloud_device_info(dev)
            for key, (name, icon, _, cloud_key) in SWITCH_TYPES.items():
                entities.append(
                    CloudSwitch(coordinator, dev, info, key, name, icon, cloud_key)
                )
    else:
        for dev_entry in data.get("local_devices", []):
            info = _local_device_info(dev_entry["info"])
            raw = dev_entry["info"]
            for key, (name, icon, state_attr, _) in SWITCH_TYPES.items():
                entities.append(
                    LocalSwitch(
                        dev_entry["coordinator"], info, raw, key, name, icon, state_attr
                    )
                )

    if entities:
        async_add_entities(entities)


class LocalSwitch(CoordinatorEntity, SwitchEntity):
    """Switch for a local AC feature."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator, dev_info: DeviceInfo, raw_info: dict,
        key: str, name: str, icon: str, state_attr: str,
    ) -> None:
        super().__init__(coordinator)
        self._api = coordinator.api
        self._state_attr = state_attr
        ip = raw_info[CONF_IP_ADDRESS]
        mac = raw_info.get("mac", "ac")
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{ip}_{mac}_{key}"
        self._attr_device_info = dev_info

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
    """Switch for a cloud AC feature."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator, device: dict, dev_info: DeviceInfo,
        key: str, name: str, icon: str, cloud_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._did = device["endpointId"]
        self._cloud_api = coordinator.cloud_api
        self._cloud_key = cloud_key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"cloud_{self._did}_{key}"
        self._attr_device_info = dev_info

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
