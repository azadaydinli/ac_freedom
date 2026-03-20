"""Fan platform for AC Freedom.

Provides a fan entity with preset_modes (sleep, health, eco, clean)
that shares the same device as the climate entity, so HomeKit groups
them together in one card.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud_api.const import (
    AC_CLEAN,
    AC_FAN_SPEED,
    AC_HEALTH,
    AC_MILDEW_PROOF,
    AC_POWER,
    AC_SLEEP,
    ACFanSpeed,
)
from .const import (
    CONF_CONN_MODE,
    CONN_CLOUD,
    CONN_LOCAL,
    DOMAIN,
    FanSpeed,
)

_LOGGER = logging.getLogger(__name__)

# ── Preset mode constants (same as climate.py) ─────────────────────
PRESET_NONE = "none"
PRESET_SLEEP = "sleep"
PRESET_HEALTH = "health"
PRESET_ECO = "eco"
PRESET_CLEAN = "clean"

ALL_PRESETS = [PRESET_SLEEP, PRESET_HEALTH, PRESET_ECO, PRESET_CLEAN]

CONF_ENABLED_PRESETS = "enabled_presets"

# Local state attribute names for presets
LOCAL_PRESET_MAP = {
    PRESET_SLEEP: "sleep",
    PRESET_HEALTH: "health",
    PRESET_ECO: "mildew",
    PRESET_CLEAN: "clean",
}

# Cloud param keys for presets
CLOUD_PRESET_MAP = {
    PRESET_SLEEP: AC_SLEEP,
    PRESET_HEALTH: AC_HEALTH,
    PRESET_ECO: AC_MILDEW_PROOF,
    PRESET_CLEAN: AC_CLEAN,
}

# ── Fan speed mappings ──────────────────────────────────────────────
# Local: ordered speeds for percentage calculation
LOCAL_SPEED_LIST = [
    ("mute", FanSpeed.LOW, 0, 1),
    ("low", FanSpeed.LOW, 0, 0),
    ("medium", FanSpeed.MEDIUM, 0, 0),
    ("high", FanSpeed.HIGH, 0, 0),
    ("turbo", FanSpeed.HIGH, 1, 0),
]
LOCAL_SPEED_COUNT = len(LOCAL_SPEED_LIST)

# Cloud: ordered speeds for percentage calculation
CLOUD_SPEED_LIST = [
    ("silent", ACFanSpeed.MUTE),
    ("low", ACFanSpeed.LOW),
    ("medium", ACFanSpeed.MEDIUM),
    ("high", ACFanSpeed.HIGH),
    ("turbo", ACFanSpeed.TURBO),
]
CLOUD_SPEED_COUNT = len(CLOUD_SPEED_LIST)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the AC Freedom fan entity."""
    data = hass.data[DOMAIN][entry.entry_id]
    conn_mode = data.get("mode", CONN_LOCAL)

    entities = []

    if conn_mode == CONN_CLOUD:
        coordinator = data["coordinator"]
        for dev in data.get("devices", []):
            entities.append(CloudAcFan(coordinator, dev, entry))
    else:
        for dev_entry in data.get("local_devices", []):
            entities.append(
                LocalAcFan(dev_entry["coordinator"], entry, dev_entry["info"])
            )

    async_add_entities(entities)


# ═══════════════════════════════════════════════════════════════════
# LOCAL FAN ENTITY
# ═══════════════════════════════════════════════════════════════════

class LocalAcFan(CoordinatorEntity, FanEntity):
    """Fan entity for local AC Freedom unit — exposes preset_modes."""

    _attr_has_entity_name = True
    _attr_name = "Fan"
    _attr_speed_count = LOCAL_SPEED_COUNT
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
    )

    def __init__(self, coordinator, entry: ConfigEntry, dev_info: dict) -> None:
        super().__init__(coordinator)
        self._api = coordinator.api
        ip = dev_info[CONF_IP_ADDRESS]
        mac = dev_info.get("mac", "ac")
        self._attr_unique_id = f"{ip}_{mac}_fan"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{ip}_{mac}")},
            name=dev_info.get(CONF_NAME, f"AC Freedom ({ip})"),
            manufacturer="AUX",
            model="AC Freedom (Local)",
        )
        # Build preset list from options
        enabled = entry.options.get(CONF_ENABLED_PRESETS, ALL_PRESETS)
        self._enabled_presets = {
            k: v for k, v in LOCAL_PRESET_MAP.items() if k in enabled
        }
        self._attr_preset_modes = [PRESET_NONE] + list(self._enabled_presets.keys())

    @property
    def is_on(self) -> bool:
        return bool(self._api.state.power)

    @property
    def percentage(self) -> int | None:
        if not self._api.state.power:
            return 0
        # Determine current speed index
        if self._api.state.mute:
            idx = 0  # mute
        elif self._api.state.turbo:
            idx = 4  # turbo
        else:
            speed_idx = {
                FanSpeed.LOW: 1,
                FanSpeed.MEDIUM: 2,
                FanSpeed.HIGH: 3,
                FanSpeed.AUTO: 2,  # map auto to medium
            }
            idx = speed_idx.get(self._api.state.fan_speed, 2)
        return math.ceil((idx + 1) * 100 / LOCAL_SPEED_COUNT)

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage == 0:
            self._api.state.power = 0
            await self._api.set_state()
            await self.coordinator.async_request_refresh()
            return
        # Map percentage to speed index
        idx = max(0, math.ceil(percentage * LOCAL_SPEED_COUNT / 100) - 1)
        idx = min(idx, LOCAL_SPEED_COUNT - 1)
        _, speed, turbo, mute = LOCAL_SPEED_LIST[idx]
        self._api.state.fan_speed = speed
        self._api.state.turbo = turbo
        self._api.state.mute = mute
        await self._api.set_state()
        await self.coordinator.async_request_refresh()

    @property
    def preset_mode(self) -> str | None:
        for preset, attr in self._enabled_presets.items():
            if getattr(self._api.state, attr, 0):
                return preset
        return PRESET_NONE

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        # Turn off all presets first (including disabled ones)
        for attr in LOCAL_PRESET_MAP.values():
            setattr(self._api.state, attr, 0)
        # Activate selected preset
        if preset_mode != PRESET_NONE and preset_mode in self._enabled_presets:
            setattr(self._api.state, self._enabled_presets[preset_mode], 1)
        await self._api.set_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(
        self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any
    ) -> None:
        self._api.state.power = 1
        if percentage is not None:
            await self.async_set_percentage(percentage)
            return
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
            return
        await self._api.set_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._api.state.power = 0
        await self._api.set_state()
        await self.coordinator.async_request_refresh()


# ═══════════════════════════════════════════════════════════════════
# CLOUD FAN ENTITY
# ═══════════════════════════════════════════════════════════════════

class CloudAcFan(CoordinatorEntity, FanEntity):
    """Fan entity for cloud AC Freedom unit — exposes preset_modes."""

    _attr_has_entity_name = True
    _attr_name = "Fan"
    _attr_speed_count = CLOUD_SPEED_COUNT
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
    )

    def __init__(self, coordinator, device: dict, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._device = device
        self._did = device["endpointId"]
        self._cloud_api = coordinator.cloud_api
        self._attr_unique_id = f"cloud_{self._did}_fan"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._did)},
            name=device.get("friendlyName", "AUX AC"),
            manufacturer="AUX",
            model="AC Freedom (Cloud)",
        )
        # Build preset list from options
        enabled = entry.options.get(CONF_ENABLED_PRESETS, ALL_PRESETS)
        self._enabled_presets = {
            k: v for k, v in CLOUD_PRESET_MAP.items() if k in enabled
        }
        self._attr_preset_modes = [PRESET_NONE] + list(self._enabled_presets.keys())

    def _params(self) -> dict:
        if self.coordinator.data and self._did in self.coordinator.data:
            return self.coordinator.data[self._did].get("params", {})
        return self._device.get("params", {})

    @property
    def available(self) -> bool:
        return len(self._params()) > 0

    @property
    def is_on(self) -> bool:
        return bool(self._params().get(AC_POWER, 0))

    @property
    def percentage(self) -> int | None:
        if not self.is_on:
            return 0
        val = self._params().get(AC_FAN_SPEED)
        speed_idx = {
            ACFanSpeed.MUTE: 0,
            ACFanSpeed.LOW: 1,
            ACFanSpeed.MEDIUM: 2,
            ACFanSpeed.HIGH: 3,
            ACFanSpeed.TURBO: 4,
            ACFanSpeed.AUTO: 2,  # map auto to medium
        }
        idx = speed_idx.get(val, 2)
        return math.ceil((idx + 1) * 100 / CLOUD_SPEED_COUNT)

    async def _set_cloud(self, params: dict) -> None:
        if self.coordinator.data and self._did in self.coordinator.data:
            self._device = self.coordinator.data[self._did]
        await self._cloud_api.set_device_params(self._device, params)
        await self.coordinator.async_request_refresh()

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage == 0:
            await self._set_cloud({AC_POWER: 0})
            return
        idx = max(0, math.ceil(percentage * CLOUD_SPEED_COUNT / 100) - 1)
        idx = min(idx, CLOUD_SPEED_COUNT - 1)
        _, aux_speed = CLOUD_SPEED_LIST[idx]
        await self._set_cloud({AC_FAN_SPEED: aux_speed})

    @property
    def preset_mode(self) -> str | None:
        params = self._params()
        for preset, cloud_key in self._enabled_presets.items():
            if params.get(cloud_key, 0):
                return preset
        return PRESET_NONE

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        update = {v: 0 for v in CLOUD_PRESET_MAP.values()}
        if preset_mode != PRESET_NONE and preset_mode in self._enabled_presets:
            update[self._enabled_presets[preset_mode]] = 1
        await self._set_cloud(update)

    async def async_turn_on(
        self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any
    ) -> None:
        if percentage is not None:
            await self._set_cloud({AC_POWER: 1})
            await self.async_set_percentage(percentage)
            return
        if preset_mode is not None:
            await self._set_cloud({AC_POWER: 1})
            await self.async_set_preset_mode(preset_mode)
            return
        await self._set_cloud({AC_POWER: 1})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_cloud({AC_POWER: 0})
