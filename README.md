# AC Freedom

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Home Assistant custom integration for **AUX** air conditioners. Supports both **local (Broadlink UDP)** and **AUX Cloud API** control.

## Features

- **Local control** via Broadlink UDP protocol — no cloud dependency for supported models
- **Cloud control** via AUX Cloud API — for newer models (e.g. BL1206-P) that don't support local UDP
- Auto-discovery of local devices on the network
- Multiple local devices grouped into a single integration entry
- Climate entity with preset modes (Sleep, Health, Eco, Clean)
- Switch entities (Display, Sleep, Health, Clean, Mildew) — compatible with HomeKit bridge
- Multi-language support (English, Turkish)

## Supported Devices

| Control Mode | Models | Protocol |
|---|---|---|
| Local | AUX ACs with Broadlink module | UDP (LAN) |
| Cloud | AUX ACs with Wi-Fi (e.g. BL1206-P) | AUX Cloud WebSocket |

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click **⋮** (three dots, top right) → **Custom repositories**
3. Add `https://github.com/azadaydinli/ac_freedom` with category **Integration**
4. Search for **AC Freedom** and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/ac_freedom` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for **AC Freedom**
3. Choose from the menu:
   - **Select discovered devices** — pick from auto-discovered local ACs
   - **Rescan network** — scan the network again
   - **Manual entry** — enter IP and MAC address manually
   - **Cloud login** — sign in with AUX Cloud credentials

## Entities

### Climate
- HVAC modes: Off, Cool, Heat, Dry, Fan Only, Auto
- Preset modes: None, Sleep, Health, Eco, Clean
- Fan modes & Swing control
- Temperature step: 1°C

### Switches
- **Display** — screen on/off
- **Sleep Mode** — sleep mode toggle
- **Health / Ionizer** — ionizer function
- **Self Clean** — self-cleaning mode
- **Eco / Mildew Prevention** — mildew prevention mode

> **HomeKit Note:** HomeKit thermostat accessory only supports temperature and HVAC mode. Use the switch entities for Sleep, Health, Clean, etc. — they appear as separate accessories in HomeKit.

## Requirements

- Home Assistant 2024.1.0 or newer
- `pycryptodome >= 3.20.0`
- `aiohttp >= 3.9.0`

## License

MIT
