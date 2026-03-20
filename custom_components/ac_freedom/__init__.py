"""AC Freedom custom component for Home Assistant.

Hybrid integration supporting:
  - Local UDP control for classic Broadlink AC modules
  - AUX Cloud API for newer BL1206-P modules
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .broadlink_ac_api import BroadlinkAcApi
from .cloud_api import AuxCloudAPI
from .const import (
    CONF_CLOUD_DEVICES,
    CONF_CLOUD_EMAIL,
    CONF_CLOUD_PASSWORD,
    CONF_CLOUD_REGION,
    CONF_CONN_MODE,
    CONF_MAC,
    CONN_CLOUD,
    CONN_LOCAL,
    DOMAIN,
    POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.FAN, Platform.SWITCH]


class BroadlinkAcCoordinator(DataUpdateCoordinator):
    """Coordinator for polling a local AC Freedom device."""

    def __init__(self, hass: HomeAssistant, api: BroadlinkAcApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self.api = api

    async def _async_update_data(self) -> None:
        success = await self.api.update()
        if not success:
            raise UpdateFailed("Failed to update state from AC Freedom device")


class CloudCoordinator(DataUpdateCoordinator):
    """Coordinator for polling AUX Cloud devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        cloud_api: AuxCloudAPI,
        devices: list[dict],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_cloud",
            update_interval=timedelta(seconds=60),
        )
        self.cloud_api = cloud_api
        self.devices = devices

    async def _async_update_data(self) -> dict:
        try:
            await self.cloud_api.fetch_devices_state(self.devices)
        except Exception as exc:
            raise UpdateFailed(f"Cloud update failed: {exc}") from exc
        return {d["endpointId"]: d for d in self.devices}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AC Freedom from a config entry."""
    conn_mode = entry.data.get(CONF_CONN_MODE, CONN_LOCAL)
    hass.data.setdefault(DOMAIN, {})

    if conn_mode == CONN_CLOUD:
        return await _setup_cloud_entry(hass, entry)
    else:
        return await _setup_local_entry(hass, entry)


async def _setup_local_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one or more local UDP devices from a single config entry."""
    local_devices = entry.data.get("local_devices", [])

    # Backward compatibility: old single-device format
    if not local_devices and CONF_IP_ADDRESS in entry.data:
        local_devices = [{
            CONF_NAME: entry.data.get(CONF_NAME, "AC Freedom"),
            CONF_IP_ADDRESS: entry.data[CONF_IP_ADDRESS],
            CONF_MAC: entry.data[CONF_MAC],
        }]

    device_entries: list[dict] = []
    for dev_info in local_devices:
        ip = dev_info[CONF_IP_ADDRESS]
        mac = dev_info[CONF_MAC]
        name = dev_info.get(CONF_NAME, f"AC Freedom ({ip})")

        api = BroadlinkAcApi(ip, mac)
        connected = await api.connect()
        if not connected:
            _LOGGER.error("Failed to connect to local AC at %s", ip)
            continue

        coordinator = BroadlinkAcCoordinator(hass, api)
        await coordinator.async_config_entry_first_refresh()
        device_entries.append({
            "api": api,
            "coordinator": coordinator,
            "info": dev_info,
        })

    if not device_entries:
        _LOGGER.error("No local devices could be connected")
        return False

    hass.data[DOMAIN][entry.entry_id] = {
        "local_devices": device_entries,
        "mode": CONN_LOCAL,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _setup_cloud_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up cloud-connected devices."""
    email = entry.data[CONF_CLOUD_EMAIL]
    password = entry.data[CONF_CLOUD_PASSWORD]
    region = entry.data[CONF_CLOUD_REGION]
    selected_dids = entry.data.get(CONF_CLOUD_DEVICES, [])

    cloud_api = AuxCloudAPI(region=region)
    try:
        await cloud_api.login(email, password)
    except Exception as exc:
        _LOGGER.error("Cloud login failed: %s", exc)
        return False

    # Fetch families and devices
    try:
        families = await cloud_api.get_families()
        devices = []
        for family in families:
            fam_devices = await cloud_api.get_devices(family["familyid"])
            devices.extend(fam_devices)
    except Exception as exc:
        _LOGGER.error("Failed to fetch cloud devices: %s", exc)
        return False

    # Filter to selected devices only
    if selected_dids:
        devices = [d for d in devices if d.get("endpointId") in selected_dids]

    if not devices:
        _LOGGER.warning("No cloud devices found")
        return False

    coordinator = CloudCoordinator(hass, cloud_api, devices)
    await coordinator.async_config_entry_first_refresh()

    # Initialize WebSocket for real-time updates
    try:
        await cloud_api.initialize_websocket(
            on_state_update=lambda did, params: _handle_cloud_push(hass, entry, did, params)
        )
    except Exception:
        _LOGGER.warning("WebSocket connection failed, will use polling only")

    hass.data[DOMAIN][entry.entry_id] = {
        "cloud_api": cloud_api,
        "coordinator": coordinator,
        "devices": devices,
        "mode": CONN_CLOUD,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


def _handle_cloud_push(
    hass: HomeAssistant, entry: ConfigEntry, did: str, params: dict
) -> None:
    """Handle a real-time state push from the cloud WebSocket."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        return
    coordinator = data.get("coordinator")
    if coordinator:
        # Update the device in the coordinator's device list
        for dev in data.get("devices", []):
            if dev.get("endpointId") == did:
                dev.get("params", {}).update(params)
                break
        coordinator.async_set_updated_data(
            {d["endpointId"]: d for d in data.get("devices", [])}
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data:
            mode = data.get("mode")
            if mode == CONN_LOCAL:
                for dev_entry in data.get("local_devices", []):
                    await dev_entry["api"].disconnect()
            elif mode == CONN_CLOUD:
                cloud_api = data.get("cloud_api")
                if cloud_api:
                    await cloud_api.close()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
