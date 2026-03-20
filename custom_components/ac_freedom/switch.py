"""Switch platform for AC Freedom.

Provides a toggle switch for the AC display (screen on/off).
Other features (sleep, health, clean, mildew) are handled as
preset_modes inside the climate entity.
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

from .cloud_api.const import AC_SCREEN_DISPLAY
from .const import CONN_CLOUD, CONN_LOCAL, DOMAIN, SWITCH_DISPLAY

_LOGGER = logging.getLogger(__name__)


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
            entities.append(CloudDisplaySwitch(coordinator, dev, entry))
    else:
        for dev_entry in data.get("local_devices", []):
            entities.append(
                LocalDisplaySwitch(
                    dev_entry["coordinator"], entry, dev_entry["info"],
                )
            )

    if entities:
        async_add_entities(entities)


class LocalDisplaySwitch(CoordinatorEntity, SwitchEntity):
    """A switch for the local AC display (screen on/off)."""

    _attr_has_entity_name = True
    _attr_name = "Display"
    _attr_icon = "mdi:monitor"

    def __init__(self, coordinator, entry, dev_info: dict) -> None:
        super().__init__(coordinator)
        self._api = coordinator.api
        ip = dev_info[CONF_IP_ADDRESS]
        mac = dev_info.get("mac", "ac")
        dev_name = dev_info.get(CONF_NAME, f"AC Freedom ({ip})")
        self._attr_unique_id = f"{ip}_{mac}_{SWITCH_DISPLAY}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{ip}_{mac}")},
            name=dev_name,
            manufacturer="AUX",
            model="AC Freedom (Local)",
            sw_version="2.2.1",
        )

    @property
    def is_on(self) -> bool:
        return bool(getattr(self._api.state, "display", 0))

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._api.state.display = 1
        await self._api.set_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._api.state.display = 0
        await self._api.set_state()
        await self.coordinator.async_request_refresh()


class CloudDisplaySwitch(CoordinatorEntity, SwitchEntity):
    """A switch for the cloud AC display (screen on/off)."""

    _attr_has_entity_name = True
    _attr_name = "Display"
    _attr_icon = "mdi:monitor"

    def __init__(self, coordinator, device, entry) -> None:
        super().__init__(coordinator)
        self._device = device
        self._did = device["endpointId"]
        self._cloud_api = coordinator.cloud_api
        self._attr_unique_id = f"cloud_{self._did}_{SWITCH_DISPLAY}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._did)},
            name=device.get("friendlyName", "AUX AC"),
            manufacturer="AUX",
            model="AC Freedom (Cloud)",
            sw_version="2.2.1",
        )

    def _params(self) -> dict:
        if self.coordinator.data and self._did in self.coordinator.data:
            return self.coordinator.data[self._did].get("params", {})
        return self._device.get("params", {})

    @property
    def is_on(self) -> bool:
        return bool(self._params().get(AC_SCREEN_DISPLAY, 0))

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.coordinator.data and self._did in self.coordinator.data:
            self._device = self.coordinator.data[self._did]
        await self._cloud_api.set_device_params(self._device, {AC_SCREEN_DISPLAY: 1})
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.coordinator.data and self._did in self.coordinator.data:
            self._device = self.coordinator.data[self._did]
        await self._cloud_api.set_device_params(self._device, {AC_SCREEN_DISPLAY: 0})
        await self.coordinator.async_request_refresh()
