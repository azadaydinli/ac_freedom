"""Climate platform for AC Freedom.

Supports both:
  - Local: BroadlinkAcClimate (classic UDP, reads api.state.*)
  - Cloud: CloudAcClimate (AUX Cloud API, reads device params dict)
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate.const import (
    FAN_AUTO,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    SWING_BOTH,
    SWING_HORIZONTAL,
    SWING_OFF,
    SWING_VERTICAL,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_IP_ADDRESS,
    CONF_NAME,
    UnitOfTemperature,
)
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
    AC_SCREEN_DISPLAY,
    AC_SLEEP,
    AC_SWING_HORIZONTAL as CLOUD_SWING_H,
    AC_SWING_VERTICAL as CLOUD_SWING_V,
    AC_TEMPERATURE_AMBIENT,
    AC_TEMPERATURE_TARGET,
    ACFanSpeed,
    AUX_MODE,
)
from .const import (
    CONF_CONN_MODE,
    CONF_TEMP_STEP,
    CONN_CLOUD,
    CONN_LOCAL,
    DOMAIN,
    TEMP_MAX,
    TEMP_MIN,
    TEMP_STEP_HALF,
    AcMode,
    FanSpeed,
    Fixation,
)

_LOGGER = logging.getLogger(__name__)

# ── Local mode mappings ─────────────────────────────────────────────
HVAC_MODE_TO_AC = {
    HVACMode.AUTO: AcMode.AUTO,
    HVACMode.COOL: AcMode.COOLING,
    HVACMode.HEAT: AcMode.HEATING,
    HVACMode.DRY: AcMode.DRY,
    HVACMode.FAN_ONLY: AcMode.FAN_ONLY,
}

AC_MODE_TO_HVAC = {v: k for k, v in HVAC_MODE_TO_AC.items()}

LOCAL_FAN_MODES = ["auto", "low", "medium", "high", "turbo", "mute"]

FAN_MODE_TO_DEVICE = {
    "auto": (FanSpeed.AUTO, 0, 0),
    "low": (FanSpeed.LOW, 0, 0),
    "medium": (FanSpeed.MEDIUM, 0, 0),
    "high": (FanSpeed.HIGH, 0, 0),
    "turbo": (FanSpeed.HIGH, 1, 0),
    "mute": (FanSpeed.LOW, 0, 1),
}

LOCAL_SWING_MODES = ["off", "vertical", "horizontal", "both"]

# ── Preset modes (shared between local & cloud) ───────────────────
PRESET_NONE = "none"
PRESET_SLEEP = "sleep"
PRESET_HEALTH = "health"
PRESET_ECO = "eco"
PRESET_CLEAN = "clean"

ALL_PRESETS = [PRESET_SLEEP, PRESET_HEALTH, PRESET_ECO, PRESET_CLEAN]

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

# Options key for enabled presets
CONF_ENABLED_PRESETS = "enabled_presets"

# ── Cloud mode mappings ─────────────────────────────────────────────
# AUX cloud: 0=COOL, 1=HEAT, 2=DRY, 3=FAN, 4=AUTO
CLOUD_MODE_TO_HVAC = {
    4: HVACMode.AUTO,
    0: HVACMode.COOL,
    1: HVACMode.HEAT,
    2: HVACMode.DRY,
    3: HVACMode.FAN_ONLY,
}
HVAC_TO_CLOUD_MODE = {v: k for k, v in CLOUD_MODE_TO_HVAC.items()}

CLOUD_FAN_MODES = [FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH, "turbo", "silent"]
CLOUD_FAN_HA_TO_AUX = {
    FAN_AUTO: ACFanSpeed.AUTO,
    FAN_LOW: ACFanSpeed.LOW,
    FAN_MEDIUM: ACFanSpeed.MEDIUM,
    FAN_HIGH: ACFanSpeed.HIGH,
    "turbo": ACFanSpeed.TURBO,
    "silent": ACFanSpeed.MUTE,
}
CLOUD_FAN_AUX_TO_HA = {v: k for k, v in CLOUD_FAN_HA_TO_AUX.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the AC Freedom climate entity."""
    data = hass.data[DOMAIN][entry.entry_id]
    conn_mode = data.get("mode", CONN_LOCAL)

    entities = []

    if conn_mode == CONN_CLOUD:
        coordinator = data["coordinator"]
        for dev in data.get("devices", []):
            entities.append(CloudAcClimate(coordinator, dev, entry))
    else:
        # One entity per local device
        for dev_entry in data.get("local_devices", []):
            entities.append(
                BroadlinkAcClimate(
                    dev_entry["coordinator"], entry, dev_entry["info"],
                )
            )

    async_add_entities(entities)


# ═════════════════════════════════════════════════════════════════════
# LOCAL CLIMATE ENTITY (classic Broadlink UDP)
# ═════════════════════════════════════════════════════════════════════

class BroadlinkAcClimate(CoordinatorEntity, ClimateEntity):
    """Representation of a local AC Freedom unit via UDP."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = TEMP_MIN
    _attr_max_temp = TEMP_MAX
    _attr_hvac_modes = [
        HVACMode.OFF, HVACMode.AUTO, HVACMode.COOL,
        HVACMode.HEAT, HVACMode.DRY, HVACMode.FAN_ONLY,
    ]
    _attr_fan_modes = LOCAL_FAN_MODES
    _attr_swing_modes = LOCAL_SWING_MODES
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _enable_turn_on_off_backwards_compat = False

    def __init__(self, coordinator, entry: ConfigEntry, dev_info: dict) -> None:
        super().__init__(coordinator)
        self._api = coordinator.api
        ip = dev_info[CONF_IP_ADDRESS]
        mac = dev_info.get("mac", "ac")
        name = dev_info.get(CONF_NAME, f"AC Freedom ({ip})")
        self._attr_unique_id = f"{ip}_{mac}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{ip}_{mac}")},
            name=name,
            manufacturer="AUX",
            model="AC Freedom (Local)",
        )
        step = entry.options.get(CONF_TEMP_STEP, entry.data.get(CONF_TEMP_STEP, TEMP_STEP_HALF))
        self._attr_target_temperature_step = step
        # Build preset list from options
        enabled = entry.options.get(CONF_ENABLED_PRESETS, ALL_PRESETS)
        self._enabled_presets = {k: v for k, v in LOCAL_PRESET_MAP.items() if k in enabled}
        self._attr_preset_modes = [PRESET_NONE] + list(self._enabled_presets.keys())

    @property
    def current_temperature(self) -> float | None:
        temp = self._api.state.ambient_temp
        return temp if temp > 0 else None

    @property
    def target_temperature(self) -> float | None:
        return self._api.state.temperature

    @property
    def hvac_mode(self) -> HVACMode:
        if not self._api.state.power:
            return HVACMode.OFF
        return AC_MODE_TO_HVAC.get(self._api.state.mode, HVACMode.AUTO)

    @property
    def hvac_action(self) -> HVACAction | None:
        if not self._api.state.power:
            return HVACAction.OFF
        actions = {
            AcMode.COOLING: HVACAction.COOLING,
            AcMode.HEATING: HVACAction.HEATING,
            AcMode.DRY: HVACAction.DRYING,
            AcMode.FAN_ONLY: HVACAction.FAN,
        }
        return actions.get(self._api.state.mode, HVACAction.IDLE)

    @property
    def fan_mode(self) -> str | None:
        if self._api.state.mute:
            return "mute"
        if self._api.state.turbo:
            return "turbo"
        speed_map = {
            FanSpeed.AUTO: "auto", FanSpeed.LOW: "low",
            FanSpeed.MEDIUM: "medium", FanSpeed.HIGH: "high",
        }
        return speed_map.get(self._api.state.fan_speed, "auto")

    @property
    def swing_mode(self) -> str | None:
        v_on = self._api.state.vertical_fixation == Fixation.ON
        h_on = self._api.state.horizontal_fixation == Fixation.ON
        if v_on and h_on:
            return "both"
        if v_on:
            return "vertical"
        if h_on:
            return "horizontal"
        return "off"

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            self._api.state.power = 0
        else:
            self._api.state.power = 1
            ac_mode = HVAC_MODE_TO_AC.get(hvac_mode)
            if ac_mode is not None:
                self._api.state.mode = ac_mode
        await self._api.set_state()
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        self._api.state.temperature = max(TEMP_MIN, min(TEMP_MAX, temp))
        await self._api.set_state()
        await self.coordinator.async_request_refresh()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        config = FAN_MODE_TO_DEVICE.get(fan_mode)
        if config is None:
            return
        speed, turbo, mute = config
        self._api.state.fan_speed = speed
        self._api.state.turbo = turbo
        self._api.state.mute = mute
        await self._api.set_state()
        await self.coordinator.async_request_refresh()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        v = Fixation.ON if swing_mode in ("vertical", "both") else Fixation.OFF
        h = Fixation.ON if swing_mode in ("horizontal", "both") else Fixation.OFF
        self._api.state.vertical_fixation = v
        self._api.state.horizontal_fixation = h
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

    async def async_turn_on(self) -> None:
        self._api.state.power = 1
        await self._api.set_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        self._api.state.power = 0
        await self._api.set_state()
        await self.coordinator.async_request_refresh()


# ═════════════════════════════════════════════════════════════════════
# CLOUD CLIMATE ENTITY (AUX Cloud API)
# ═════════════════════════════════════════════════════════════════════

class CloudAcClimate(CoordinatorEntity, ClimateEntity):
    """Representation of an AUX AC unit via cloud API."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = TEMP_MIN
    _attr_max_temp = TEMP_MAX
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = [
        HVACMode.OFF, HVACMode.AUTO, HVACMode.COOL,
        HVACMode.HEAT, HVACMode.DRY, HVACMode.FAN_ONLY,
    ]
    _attr_fan_modes = CLOUD_FAN_MODES
    _attr_swing_modes = [SWING_OFF, SWING_VERTICAL, SWING_HORIZONTAL, SWING_BOTH]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _enable_turn_on_off_backwards_compat = False

    def __init__(self, coordinator, device: dict, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._device = device
        self._did = device["endpointId"]
        self._cloud_api = coordinator.cloud_api
        self._attr_name = device.get("friendlyName", "AUX AC")
        self._attr_unique_id = f"cloud_{self._did}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._did)},
            name=device.get("friendlyName", "AUX AC"),
            manufacturer="AUX",
            model="AC Freedom (Cloud)",
        )
        step = entry.options.get(CONF_TEMP_STEP, TEMP_STEP_HALF)
        self._attr_target_temperature_step = step
        # Build preset list from options
        enabled = entry.options.get(CONF_ENABLED_PRESETS, ALL_PRESETS)
        self._enabled_presets = {k: v for k, v in CLOUD_PRESET_MAP.items() if k in enabled}
        self._attr_preset_modes = [PRESET_NONE] + list(self._enabled_presets.keys())

    def _params(self) -> dict:
        """Get current device params from coordinator data."""
        if self.coordinator.data and self._did in self.coordinator.data:
            return self.coordinator.data[self._did].get("params", {})
        return self._device.get("params", {})

    @property
    def available(self) -> bool:
        return len(self._params()) > 0

    @property
    def current_temperature(self) -> float | None:
        val = self._params().get(AC_TEMPERATURE_AMBIENT)
        return val / 10 if val is not None else None

    @property
    def target_temperature(self) -> float | None:
        val = self._params().get(AC_TEMPERATURE_TARGET)
        return val / 10 if val is not None else None

    @property
    def hvac_mode(self) -> HVACMode:
        params = self._params()
        if not params.get(AC_POWER, 0):
            return HVACMode.OFF
        mode = params.get(AUX_MODE)
        return CLOUD_MODE_TO_HVAC.get(mode, HVACMode.OFF)

    @property
    def hvac_action(self) -> HVACAction | None:
        mode = self.hvac_mode
        if mode == HVACMode.OFF:
            return HVACAction.OFF
        return {
            HVACMode.COOL: HVACAction.COOLING,
            HVACMode.HEAT: HVACAction.HEATING,
            HVACMode.DRY: HVACAction.DRYING,
            HVACMode.FAN_ONLY: HVACAction.FAN,
        }.get(mode, HVACAction.IDLE)

    @property
    def fan_mode(self) -> str | None:
        val = self._params().get(AC_FAN_SPEED)
        return CLOUD_FAN_AUX_TO_HA.get(val, FAN_AUTO)

    @property
    def swing_mode(self) -> str | None:
        params = self._params()
        h = bool(params.get(CLOUD_SWING_H, 0))
        v = bool(params.get(CLOUD_SWING_V, 0))
        if h and v:
            return SWING_BOTH
        if h:
            return SWING_HORIZONTAL
        if v:
            return SWING_VERTICAL
        return SWING_OFF

    async def _set_cloud(self, params: dict) -> None:
        """Send params to cloud and refresh."""
        # Update the device reference from coordinator
        if self.coordinator.data and self._did in self.coordinator.data:
            self._device = self.coordinator.data[self._did]
        await self._cloud_api.set_device_params(self._device, params)
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self._set_cloud({AC_POWER: 0})
        else:
            aux_mode = HVAC_TO_CLOUD_MODE.get(hvac_mode)
            if aux_mode is not None:
                await self._set_cloud({AC_POWER: 1, AUX_MODE: aux_mode})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        temp = max(TEMP_MIN, min(TEMP_MAX, temp))
        await self._set_cloud({AC_TEMPERATURE_TARGET: int(temp * 10)})

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        aux_val = CLOUD_FAN_HA_TO_AUX.get(fan_mode)
        if aux_val is not None:
            await self._set_cloud({AC_FAN_SPEED: aux_val})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        v = 1 if swing_mode in (SWING_VERTICAL, SWING_BOTH) else 0
        h = 1 if swing_mode in (SWING_HORIZONTAL, SWING_BOTH) else 0
        await self._set_cloud({CLOUD_SWING_V: v, CLOUD_SWING_H: h})

    @property
    def preset_mode(self) -> str | None:
        params = self._params()
        for preset, cloud_key in self._enabled_presets.items():
            if params.get(cloud_key, 0):
                return preset
        return PRESET_NONE

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        # Turn off all presets (including disabled ones), then activate selected
        update = {v: 0 for v in CLOUD_PRESET_MAP.values()}
        if preset_mode != PRESET_NONE and preset_mode in self._enabled_presets:
            update[self._enabled_presets[preset_mode]] = 1
        await self._set_cloud(update)

    async def async_turn_on(self) -> None:
        await self._set_cloud({AC_POWER: 1})

    async def async_turn_off(self) -> None:
        await self._set_cloud({AC_POWER: 0})
