"""AC Freedom communication protocol implementation.

Ported from the Node.js homebridge-broadlink-heater-cooler plugin.
Handles UDP communication, AES encryption, and device state management.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Callable

from Crypto.Cipher import AES

from .const import (
    DISCOVERY_PORTS,
    KNOWN_PACKET_SIZES,
    PACKET_SIZE_LONG,
    PACKET_SIZE_MEDIUM,
    PACKET_SIZE_SHORT,
    PORT_COMM,
    PORT_DISCOVERY_1,
    PORT_DISCOVERY_2,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_IV = bytes(
    [0x56, 0x2E, 0x17, 0x99, 0x6D, 0x09, 0x3D, 0x28,
     0xDD, 0xB3, 0xBA, 0x69, 0x5A, 0x2E, 0x6F, 0x58]
)

DEFAULT_KEY = bytes(
    [0x09, 0x76, 0x28, 0x34, 0x3F, 0xE9, 0x9E, 0x23,
     0x76, 0x5C, 0x15, 0x13, 0xAC, 0xCF, 0x8B, 0x02]
)

# Command bytes
CMD_AUTH_REQUEST = 0x65
CMD_AUTH_RESPONSE = 0xE9
CMD_REQUEST = 0x6A
CMD_PAYLOAD = 0xEE

# Magic bytes for getState query
STATE_QUERY = bytes(
    [0x0C, 0x00, 0xBB, 0x00, 0x06, 0x80, 0x00, 0x00,
     0x02, 0x00, 0x11, 0x01, 0x2B, 0x7E, 0x00, 0x00]
)

# Magic bytes for getInfo query (ambient temperature)
INFO_QUERY = bytes(
    [0x0C, 0x00, 0xBB, 0x00, 0x06, 0x80, 0x00, 0x00,
     0x02, 0x00, 0x21, 0x01, 0x1B, 0x7E, 0x00, 0x00]
)

HEADER_MAGIC = bytes([0x5A, 0xA5, 0xAA, 0x55, 0x5A, 0xA5, 0xAA, 0x55])


@dataclass
class AcState:
    """Represents the full state of the air conditioner."""

    power: int = 0
    mode: int = 0
    temperature: float = 24.0
    fan_speed: int = 5  # Auto
    vertical_fixation: int = 7  # Off
    horizontal_fixation: int = 7  # Off
    mute: int = 0
    turbo: int = 0
    sleep: int = 0
    health: int = 0
    clean: int = 0
    display: int = 1
    mildew: int = 0
    ambient_temp: float = 0.0


def _checksum(data: bytes | bytearray) -> int:
    """Calculate Broadlink-style checksum (seed 0xBEAF)."""
    cs = 0xBEAF
    for b in data:
        cs = (cs + b) & 0xFFFF
    return cs


def _payload_checksum(data: bytes | bytearray) -> int:
    """Calculate ones-complement checksum for AC command payloads."""
    total = 0
    length = len(data)
    for i in range(0, length, 2):
        if i + 1 < length:
            total += (data[i] << 8) | data[i + 1]
        else:
            total += data[i] << 8
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return 0xFFFF ^ total


def _encrypt(data: bytes, key: bytes) -> bytes:
    """AES-128-CBC encrypt."""
    cipher = AES.new(key, AES.MODE_CBC, iv=DEFAULT_IV)
    # Pad to 16-byte boundary
    pad_len = (16 - len(data) % 16) % 16
    padded = data + bytes(pad_len)
    return cipher.encrypt(padded)


def _decrypt(data: bytes, key: bytes) -> bytes:
    """AES-128-CBC decrypt."""
    cipher = AES.new(key, AES.MODE_CBC, iv=DEFAULT_IV)
    return cipher.decrypt(data)


@dataclass
class DiscoveredDevice:
    """A device found via network discovery."""

    ip: str = ""
    mac: str = ""
    name: str = ""
    devtype: int = 0

    @property
    def unique_id(self) -> str:
        return f"{self.ip}_{self.mac}"

    @property
    def display_name(self) -> str:
        return f"AC Freedom ({self.ip})"


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """UDP protocol that collects broadcast responses."""

    def __init__(self) -> None:
        self.responses: list[tuple[bytes, tuple[str, int]]] = []
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.responses.append((data, addr))

    def error_received(self, exc: Exception) -> None:
        pass


async def discover_devices(timeout: float = 5.0) -> list[DiscoveredDevice]:
    """Broadcast Broadlink discovery on all known ports and return found devices.

    Sends discovery packets to ports 80, 15001, and 2415 simultaneously.
    Handles different response packet sizes (72, 88, 136 bytes).
    """
    loop = asyncio.get_running_loop()
    proto = _DiscoveryProtocol()

    transport, _ = await loop.create_datagram_endpoint(
        lambda: proto,
        local_addr=("0.0.0.0", 0),
        family=socket.AF_INET,
        allow_broadcast=True,
    )

    # --- build discovery packet ---
    local_port = transport.get_extra_info("sockname")[1]
    now = time.localtime()

    pkt = bytearray(0x30)

    tz_offset = int(time.timezone / -3600)
    if tz_offset < 0:
        pkt[0x08] = (256 + tz_offset) & 0xFF
        pkt[0x09] = 0xFF
        pkt[0x0A] = 0xFF
        pkt[0x0B] = 0xFF
    else:
        pkt[0x08] = tz_offset & 0xFF

    pkt[0x0C] = now.tm_year & 0xFF
    pkt[0x0D] = (now.tm_year >> 8) & 0xFF
    pkt[0x0E] = now.tm_min
    pkt[0x0F] = now.tm_hour
    pkt[0x10] = int(str(now.tm_year)[-2:])
    pkt[0x11] = now.tm_wday
    pkt[0x12] = now.tm_mday
    pkt[0x13] = now.tm_mon

    # local IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "0.0.0.0"

    for i, part in enumerate(local_ip.split(".")):
        pkt[0x18 + i] = int(part)

    pkt[0x1C] = local_port & 0xFF
    pkt[0x1D] = (local_port >> 8) & 0xFF

    pkt[0x26] = 0x06  # discovery command

    cs = _checksum(pkt)
    pkt[0x20] = cs & 0xFF
    pkt[0x21] = (cs >> 8) & 0xFF

    # Send broadcast to ALL discovery ports (80, 15001, 2415)
    for port in DISCOVERY_PORTS:
        transport.sendto(bytes(pkt), ("255.255.255.255", port))
        _LOGGER.debug("Discovery broadcast sent to port %d", port)

    # wait for responses
    await asyncio.sleep(timeout)

    transport.close()

    # parse responses
    devices: list[DiscoveredDevice] = []
    seen_macs: set[str] = set()

    for resp_data, (resp_ip, resp_port) in proto.responses:
        resp_len = len(resp_data)

        # Log packet size for diagnostics
        if resp_len in KNOWN_PACKET_SIZES:
            _LOGGER.debug(
                "Known packet size %d from %s:%d", resp_len, resp_ip, resp_port
            )
        else:
            _LOGGER.debug(
                "Unknown packet size %d from %s:%d", resp_len, resp_ip, resp_port
            )

        # Try to parse based on packet size
        device = _parse_discovery_response(resp_data, resp_ip, resp_port)
        if device is None:
            continue

        if device.mac in seen_macs:
            continue
        seen_macs.add(device.mac)
        devices.append(device)

    _LOGGER.info(
        "Discovery found %d device(s) across ports %s",
        len(devices),
        DISCOVERY_PORTS,
    )
    return devices


def _parse_discovery_response(
    data: bytes, ip: str, port: int
) -> DiscoveredDevice | None:
    """Parse a discovery response, handling different packet sizes.

    Known sizes:
      - 72 bytes  : short discovery ack (may lack name)
      - 88 bytes  : medium response (standard Broadlink)
      - 136 bytes : full response (extended info)
      - 0x40+ bytes : classic Broadlink format
    """
    resp_len = len(data)

    # --- Short packet (72 bytes) — minimal discovery ack ---
    if resp_len == PACKET_SIZE_SHORT:
        _LOGGER.debug("Parsing short (72-byte) discovery response from %s:%d", ip, port)
        if resp_len >= 0x34 + 2:
            devtype = struct.unpack_from("<H", data, 0x34)[0]
        else:
            devtype = 0
        if resp_len >= 0x40:
            mac_bytes = data[0x3A:0x40]
            mac_str = ":".join(f"{b:02X}" for b in reversed(mac_bytes))
        else:
            # Try extracting MAC from shorter offset
            mac_str = f"unknown_{ip.replace('.', '_')}"
        return DiscoveredDevice(
            ip=ip, mac=mac_str, name=f"AC ({ip})", devtype=devtype
        )

    # --- Medium packet (88 bytes) — standard response ---
    if resp_len == PACKET_SIZE_MEDIUM:
        _LOGGER.debug("Parsing medium (88-byte) discovery response from %s:%d", ip, port)
        if resp_len < 0x40:
            return None
        devtype = struct.unpack_from("<H", data, 0x34)[0]
        mac_bytes = data[0x3A:0x40]
        mac_str = ":".join(f"{b:02X}" for b in reversed(mac_bytes))
        name_bytes = data[0x40:]
        null_idx = name_bytes.find(0)
        if null_idx >= 0:
            name_bytes = name_bytes[:null_idx]
        name = name_bytes.decode("utf-8", errors="replace").strip() or f"AC ({ip})"
        return DiscoveredDevice(ip=ip, mac=mac_str, name=name, devtype=devtype)

    # --- Long packet (136 bytes) — full extended response ---
    if resp_len == PACKET_SIZE_LONG:
        _LOGGER.debug("Parsing long (136-byte) discovery response from %s:%d", ip, port)
        devtype = struct.unpack_from("<H", data, 0x34)[0]
        mac_bytes = data[0x3A:0x40]
        mac_str = ":".join(f"{b:02X}" for b in reversed(mac_bytes))
        name_bytes = data[0x40:]
        null_idx = name_bytes.find(0)
        if null_idx >= 0:
            name_bytes = name_bytes[:null_idx]
        name = name_bytes.decode("utf-8", errors="replace").strip() or f"AC ({ip})"
        return DiscoveredDevice(ip=ip, mac=mac_str, name=name, devtype=devtype)

    # --- Classic Broadlink format (>= 0x40 bytes) ---
    if resp_len >= 0x40:
        devtype = struct.unpack_from("<H", data, 0x34)[0]
        mac_bytes = data[0x3A:0x40]
        mac_str = ":".join(f"{b:02X}" for b in reversed(mac_bytes))
        name_bytes = data[0x40:]
        null_idx = name_bytes.find(0)
        if null_idx >= 0:
            name_bytes = name_bytes[:null_idx]
        name = name_bytes.decode("utf-8", errors="replace").strip() or f"AC ({ip})"
        return DiscoveredDevice(ip=ip, mac=mac_str, name=name, devtype=devtype)

    _LOGGER.warning(
        "Discovery response too short (%d bytes) from %s:%d — skipping",
        resp_len, ip, port,
    )
    return None


class BroadlinkAcProtocol(asyncio.DatagramProtocol):
    """Asyncio UDP protocol for Broadlink AC communication."""

    def __init__(self, on_response: Callable[[bytes], None]) -> None:
        self._on_response = on_response
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._on_response(data)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.error("UDP error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        _LOGGER.debug("UDP connection lost: %s", exc)


class BroadlinkAcApi:
    """Handles communication with Broadlink-equipped AUX air conditioners."""

    def __init__(self, ip: str, mac: str) -> None:
        self._ip = ip
        self._mac = self._parse_mac(mac)
        self._key = bytearray(DEFAULT_KEY)
        self._id = bytearray(4)
        self._count = 0
        self._authenticated = False
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: BroadlinkAcProtocol | None = None
        self._response_event = asyncio.Event()
        self._last_response: bytes | None = None
        self.state = AcState()

    @staticmethod
    def _parse_mac(mac_str: str) -> bytes:
        """Parse MAC address string (AA:BB:CC:DD:EE:FF) to bytes."""
        return bytes(int(b, 16) for b in mac_str.split(":"))

    async def connect(self) -> bool:
        """Connect and authenticate with the device."""
        loop = asyncio.get_running_loop()

        self._transport, self._protocol = await loop.create_datagram_endpoint(
            lambda: BroadlinkAcProtocol(self._on_response),
            local_addr=("0.0.0.0", 0),
            family=socket.AF_INET,
        )

        try:
            return await self._authenticate()
        except Exception:
            _LOGGER.exception("Failed to authenticate with device %s", self._ip)
            return False

    async def disconnect(self) -> None:
        """Close the UDP connection."""
        if self._transport:
            self._transport.close()
            self._transport = None
            self._protocol = None
        self._authenticated = False

    def _on_response(self, data: bytes) -> None:
        """Handle incoming UDP response."""
        self._last_response = data
        self._response_event.set()

    async def _wait_response(self, timeout: float = 5.0) -> bytes | None:
        """Wait for a UDP response with timeout."""
        self._response_event.clear()
        self._last_response = None
        try:
            await asyncio.wait_for(self._response_event.wait(), timeout)
            return self._last_response
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout waiting for response from %s", self._ip)
            return None

    def _build_packet(self, command: int, payload: bytes) -> bytes:
        """Build a Broadlink protocol packet."""
        encrypted = _encrypt(payload, bytes(self._key))

        packet = bytearray(0x38 + len(encrypted))

        # Magic header
        packet[0:8] = HEADER_MAGIC

        # Device type
        packet[0x24] = 0x2A
        packet[0x25] = 0x27

        # Command
        packet[0x26] = command

        # Counter
        self._count = (self._count + 1) & 0xFFFF
        struct.pack_into("<H", packet, 0x28, self._count)

        # MAC address
        packet[0x2A:0x30] = self._mac

        # Device ID
        packet[0x30:0x34] = self._id

        # Payload checksum
        payload_cs = _checksum(payload)
        struct.pack_into("<H", packet, 0x34, payload_cs)

        # Encrypted payload
        packet[0x38:] = encrypted

        # Full packet checksum
        full_cs = _checksum(packet)
        struct.pack_into("<H", packet, 0x20, full_cs)

        return bytes(packet)

    async def _send(self, command: int, payload: bytes) -> bytes | None:
        """Send a packet and wait for response."""
        if not self._transport:
            _LOGGER.error("Not connected to device")
            return None

        packet = self._build_packet(command, payload)
        self._transport.sendto(packet, (self._ip, PORT_COMM))

        response = await self._wait_response()
        if response is None:
            return None

        # Decrypt the response payload
        if len(response) <= 0x38:
            return None

        encrypted_payload = response[0x38:]
        return _decrypt(encrypted_payload, bytes(self._key))

    async def _authenticate(self) -> bool:
        """Perform authentication handshake."""
        payload = bytearray(0x50)

        # Fill auth padding (ASCII '1')
        for i in range(0x04, 0x13):
            payload[i] = 0x31

        payload[0x1E] = 0x01
        payload[0x2D] = 0x01

        # Device name "Test  1"
        name = b"Test  1"
        payload[0x30:0x30 + len(name)] = name

        result = await self._send(CMD_AUTH_REQUEST, bytes(payload))
        if result is None:
            _LOGGER.error("Auth failed: no response from %s", self._ip)
            return False

        # Extract device ID and new key
        self._id = bytearray(result[0x00:0x04])
        self._key = bytearray(result[0x04:0x14])
        self._authenticated = True

        _LOGGER.info("Authenticated with device %s", self._ip)
        return True

    async def get_state(self) -> bool:
        """Query the device state."""
        if not self._authenticated:
            _LOGGER.warning("Cannot get state: not authenticated")
            return False

        result = await self._send(CMD_REQUEST, STATE_QUERY)
        if result is None:
            return False

        self._parse_state(result)
        return True

    async def get_info(self) -> bool:
        """Query ambient temperature info."""
        if not self._authenticated:
            return False

        result = await self._send(CMD_REQUEST, INFO_QUERY)
        if result is None:
            return False

        self._parse_info(result)
        return True

    async def update(self) -> bool:
        """Full state update: get state + ambient temp."""
        if not self._authenticated:
            success = await self._reauthenticate()
            if not success:
                return False

        try:
            state_ok = await self.get_state()
            if state_ok:
                await self.get_info()
            return state_ok
        except Exception:
            _LOGGER.exception("Error updating state from %s", self._ip)
            self._authenticated = False
            return False

    async def _reauthenticate(self) -> bool:
        """Re-authenticate after connection loss."""
        _LOGGER.info("Re-authenticating with %s", self._ip)
        self._key = bytearray(DEFAULT_KEY)
        self._id = bytearray(4)
        self._count = 0
        try:
            return await self._authenticate()
        except Exception:
            _LOGGER.exception("Re-authentication failed for %s", self._ip)
            return False

    def _parse_state(self, data: bytes) -> None:
        """Parse a 32-byte state response payload."""
        if len(data) < 24:
            _LOGGER.warning("State response too short: %d bytes", len(data))
            return

        # Response payload starts at offset 2 from the command header
        # Byte indices based on the response format
        try:
            self.state.temperature = (data[12] >> 3) + 8
            self.state.vertical_fixation = data[12] & 0b00000111
            self.state.horizontal_fixation = data[13] & 0b00000111
            self.state.fan_speed = (data[15] >> 5) & 0b00000111
            self.state.mute = (data[16] >> 7) & 0b00000001
            self.state.turbo = (data[16] >> 6) & 0b00000001
            self.state.mode = (data[17] >> 5) & 0b00001111
            self.state.sleep = (data[17] >> 2) & 0b00000001
            self.state.power = (data[20] >> 5) & 0b00000001
            self.state.health = (data[20] >> 1) & 0b00000001
            self.state.clean = (data[20] >> 2) & 0b00000001
            self.state.display = (data[22] >> 4) & 0b00000001
            self.state.mildew = (data[22] >> 3) & 0b00000001

            # Check for half-degree temperature
            if len(data) > 14 and (data[14] >> 7) & 1:
                self.state.temperature += 0.5

        except (IndexError, TypeError):
            _LOGGER.exception("Failed to parse state response")

    def _parse_info(self, data: bytes) -> None:
        """Parse a 48-byte info response for ambient temperature."""
        if len(data) < 34:
            _LOGGER.warning("Info response too short: %d bytes", len(data))
            return

        try:
            temp_int = data[17] & 0b00011111
            if data[17] > 63:
                temp_int += 32
            temp_dec = data[33] / 10.0
            self.state.ambient_temp = temp_int + temp_dec
        except (IndexError, TypeError):
            _LOGGER.exception("Failed to parse info response")

    def _build_set_state_payload(self) -> bytes:
        """Build the 23-byte command payload from current state."""
        cmd = bytearray(23)

        cmd[0] = 0xBB
        cmd[1] = 0x00
        cmd[2] = 0x06
        cmd[3] = 0x80
        cmd[4] = 0x00
        cmd[5] = 0x00
        cmd[6] = 0x0F
        cmd[7] = 0x00
        cmd[8] = 0x01
        cmd[9] = 0x01

        # Temperature and vertical fixation
        temp_int = int(self.state.temperature)
        temp_half = 1 if (self.state.temperature % 1) >= 0.5 else 0
        cmd[10] = ((temp_int - 8) << 3) | (self.state.vertical_fixation & 0x07)

        # Horizontal fixation
        cmd[11] = (self.state.horizontal_fixation & 0x07) << 5

        # Half-degree flag
        cmd[12] = (temp_half << 7) | 0x0F

        # Fan speed
        cmd[13] = (self.state.fan_speed & 0x07) << 5

        # Mute and turbo
        cmd[14] = ((self.state.mute & 0x01) << 7) | ((self.state.turbo & 0x01) << 6)

        # Mode and sleep
        cmd[15] = ((self.state.mode & 0x0F) << 5) | ((self.state.sleep & 0x01) << 2)

        cmd[16] = 0x00
        cmd[17] = 0x00

        # Power, clean, health
        cmd[18] = (
            ((self.state.power & 0x01) << 5)
            | ((self.state.clean & 0x01) << 2)
            | ((self.state.health & 0x01) << 1)
        )

        cmd[19] = 0x00

        # Display and mildew
        cmd[20] = ((self.state.display & 0x01) << 4) | ((self.state.mildew & 0x01) << 3)

        cmd[21] = 0x00
        cmd[22] = 0x00

        return bytes(cmd)

    async def set_state(self) -> bool:
        """Send the current state to the device."""
        if not self._authenticated:
            success = await self._reauthenticate()
            if not success:
                return False

        cmd_payload = self._build_set_state_payload()

        # Wrap in request buffer: length byte + 1 pad byte + payload + 2 checksum bytes
        buf = bytearray(2 + len(cmd_payload) + 2)
        buf[0] = len(cmd_payload) + 2
        buf[1] = 0x00
        buf[2:2 + len(cmd_payload)] = cmd_payload

        # Ones-complement checksum of the command payload
        cs = _payload_checksum(cmd_payload)
        buf[2 + len(cmd_payload)] = (cs >> 8) & 0xFF
        buf[2 + len(cmd_payload) + 1] = cs & 0xFF

        try:
            result = await self._send(CMD_REQUEST, bytes(buf))
            return result is not None
        except Exception:
            _LOGGER.exception("Failed to send state to %s", self._ip)
            self._authenticated = False
            return False
