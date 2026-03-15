"""AUX Cloud WebSocket client for real-time device state updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

import aiohttp

_LOGGER = logging.getLogger(__name__)

WEBSOCKET_URLS = {
    "eu": "wss://app-relay-deu-f0e9ebbb.smarthomecs.de",
    "usa": "wss://app-relay-usa-fd7cc04c.smarthomecs.com",
    "cn": "wss://app-relay-chn-31a93883.ibroadlink.com",
    "rus": "wss://app-relay-rus-b8bbc3be.smarthomecs.com",
}


class AuxCloudWebSocket:
    """WebSocket client for receiving real-time AUX device updates."""

    def __init__(
        self,
        region: str,
        headers: dict,
        loginsession: str,
        userid: str,
    ) -> None:
        self.websocket_url = WEBSOCKET_URLS.get(region, WEBSOCKET_URLS["eu"])
        self.headers = headers
        self.loginsession = loginsession
        self.userid = userid

        self.websocket: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._listeners: list[Callable] = []
        self._reconnect_task: asyncio.Task | None = None
        self._stop_reconnect = asyncio.Event()
        self.api_initialized = False

    async def initialize_websocket(self) -> None:
        """Initialize the WebSocket connection and authenticate."""
        url = f"{self.websocket_url}/appsync/apprelay/relayconnect"
        try:
            self._session = aiohttp.ClientSession()
            self.websocket = await self._session.ws_connect(
                url, headers=self.headers, ssl=False
            )
            _LOGGER.info("WebSocket connection established")
            asyncio.create_task(self._listen())
            await self._send({
                "data": {"relayrule": "share"},
                "messageid": f"{int(time.time())}000",
                "msgtype": "init",
                "scope": {
                    "loginsession": self.loginsession,
                    "userid": self.userid,
                },
            })
            asyncio.create_task(self._keepalive_loop())
        except Exception as exc:
            _LOGGER.error("WebSocket connect failed: %s", exc)
            await self._schedule_reconnect()

    async def _listen(self) -> None:
        """Listen for incoming WebSocket messages."""
        try:
            async for msg in self.websocket:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    status = data.get("status", -1)
                    msgtype = data.get("msgtype")

                    if status != 0 and msgtype in {"initk", "pingk"}:
                        await self.close()
                        await self._schedule_reconnect()
                        return

                    if msgtype == "initk":
                        self.api_initialized = True
                        continue
                    if msgtype == "pingk":
                        continue

                    await self._notify_listeners(data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        except Exception as exc:
            _LOGGER.error("WebSocket lost: %s", exc)
        finally:
            await self._schedule_reconnect()

    async def _keepalive_loop(self) -> None:
        while self.websocket and not self.websocket.closed:
            try:
                await self._send({
                    "messageid": f"{int(time.time())}000",
                    "msgtype": "ping",
                })
            except Exception:
                await self._schedule_reconnect()
                return
            await asyncio.sleep(10)

    async def _notify_listeners(self, message: dict) -> None:
        for listener in self._listeners:
            try:
                await listener(message)
            except Exception as exc:
                _LOGGER.error("WS listener error: %s", exc)

    def add_listener(self, listener: Callable) -> None:
        self._listeners.append(listener)

    async def _schedule_reconnect(self) -> None:
        if self._reconnect_task is None:
            self._stop_reconnect.clear()
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        while not self._stop_reconnect.is_set():
            try:
                await self.initialize_websocket()
                self._reconnect_task = None
                return
            except Exception:
                await asyncio.sleep(10)

    async def _send(self, data: dict) -> None:
        if not self.websocket or self.websocket.closed:
            raise ConnectionError("WebSocket not connected")
        await self.websocket.send_str(json.dumps(data))

    async def close(self) -> None:
        """Close WebSocket and stop reconnection."""
        self._stop_reconnect.set()
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        if self._session:
            await self._session.close()
            self._session = None
