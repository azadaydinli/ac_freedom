"""AUX Cloud HTTP API client.

Communicates with the BroadLink-based SmartHomeCS cloud infrastructure
to control AUX air conditioners remotely.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time

import aiohttp

from .const import AuxProducts
from .util import encrypt_aes_cbc_zero_padding
from .websocket import AuxCloudWebSocket

_LOGGER = logging.getLogger(__name__)

# ── Crypto / auth constants ─────────────────────────────────────────
_TIMESTAMP_KEY = "kdixkdqp54545^#*"
_PASSWORD_SALT = "4969fj#k23#"
_BODY_KEY = "xgx3d*fe3478$ukx"

_AES_IV = bytes(
    [(b + 256) % 256 for b in [
        -22, -86, -86, 58, -69, 88, 98, -94,
        25, 24, -75, 119, 29, 22, 21, -86,
    ]]
)

# pylint: disable=line-too-long
_LICENSE = "PAFbJJ3WbvDxH5vvWezXN5BujETtH/iuTtIIW5CE/SeHN7oNKqnEajgljTcL0fBQQWM0XAAAAAAnBhJyhMi7zIQMsUcwR/PEwGA3uB5HLOnr+xRrci+FwHMkUtK7v4yo0ZHa+jPvb6djelPP893k7SagmffZmOkLSOsbNs8CAqsu8HuIDs2mDQAAAAA="
_LICENSE_ID = "3c015b249dd66ef0f11f9bef59ecd737"
_COMPANY_ID = "48eb1b36cf0202ab2ef07b880ecda60d"

_APP_VERSION = "2.2.10.456537160"
_USER_AGENT = "Dalvik/2.1.0 (Linux; U; Android 12; SM-G991B Build/SP1A.210812.016)"

# ── API server URLs ─────────────────────────────────────────────────
API_URLS = {
    "eu": "https://app-service-deu-f0e9ebbb.smarthomecs.de",
    "usa": "https://app-service-usa-fd7cc04c.smarthomecs.com",
    "cn": "https://app-service-chn-31a93883.ibroadlink.com",
    "rus": "https://app-service-rus-b8bbc3be.smarthomecs.com",
}


class ExpiredTokenError(Exception):
    """Session expired."""


class AuxApiError(Exception):
    """Generic API error."""


class AuxCloudAPI:
    """HTTP client for AUX cloud services."""

    def __init__(self, region: str = "eu") -> None:
        self.url = API_URLS.get(region, API_URLS["eu"])
        self.region = region
        self.families: dict | None = None
        self.email: str | None = None
        self.password: str | None = None
        self.loginsession: str | None = None
        self.userid: str | None = None
        self.ws_api: AuxCloudWebSocket | None = None

    # ── HTTP helpers ────────────────────────────────────────────────
    def _headers(self, **extra: str) -> dict:
        return {
            "Content-Type": "application/x-java-serialized-object",
            "licenseId": _LICENSE_ID,
            "lid": _LICENSE_ID,
            "language": "en",
            "appVersion": _APP_VERSION,
            "User-Agent": _USER_AGENT,
            "system": "android",
            "appPlatform": "android",
            "loginsession": self.loginsession or "",
            "userid": self.userid or "",
            **extra,
        }

    async def _request(
        self, method: str, endpoint: str, *,
        headers: dict | None = None,
        data: dict | None = None,
        data_raw: str | bytes | None = None,
        params: dict | None = None,
    ) -> dict:
        url = f"{self.url}/{endpoint}"
        body = data_raw if data_raw else (
            json.dumps(data, separators=(",", ":")) if data else None
        )
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url, headers=headers, data=body,
                params=params, ssl=False,
            ) as resp:
                text = await resp.text()
                return json.loads(text)

    # ── Auth ────────────────────────────────────────────────────────
    async def login(self, email: str | None = None, password: str | None = None) -> bool:
        email = email or self.email
        password = password or self.password
        if password:
            self.password = password
        self.email = email

        ts = time.time()
        sha_pw = hashlib.sha1(f"{password}{_PASSWORD_SALT}".encode()).hexdigest()
        payload = {
            "email": email,
            "password": sha_pw,
            "companyid": _COMPANY_ID,
            "lid": _LICENSE_ID,
        }
        body_json = json.dumps(payload, separators=(",", ":"))
        token = hashlib.md5(f"{body_json}{_BODY_KEY}".encode()).hexdigest()
        aes_key = hashlib.md5(f"{ts}{_TIMESTAMP_KEY}".encode()).digest()

        result = await self._request(
            "POST", "account/login",
            headers=self._headers(timestamp=f"{ts}", token=token),
            data_raw=encrypt_aes_cbc_zero_padding(_AES_IV, aes_key, body_json.encode()),
        )

        if result.get("status") == 0:
            self.loginsession = result["loginsession"]
            self.userid = result["userid"]
            _LOGGER.debug("Cloud login OK: %s", self.userid)
            return True
        raise AuxApiError(f"Login failed: {result}")

    def is_logged_in(self) -> bool:
        return self.loginsession is not None and self.userid is not None

    # ── Families & Devices ──────────────────────────────────────────
    async def get_families(self) -> list[dict]:
        result = await self._request(
            "POST", "appsync/group/member/getfamilylist",
            headers=self._headers(),
        )
        if result.get("status") == 0:
            self.families = {}
            for fam in result["data"]["familyList"]:
                self.families[fam["familyid"]] = {
                    "id": fam["familyid"],
                    "name": fam["name"],
                }
            return result["data"]["familyList"]
        raise AuxApiError(f"Failed to list families: {result}")

    async def get_devices(
        self, familyid: str, shared: bool = False,
        selected_devices: list[str] | None = None,
    ) -> list[dict]:
        endpoint = (
            "appsync/group/sharedev/querylist?querytype=shared"
            if shared else "appsync/group/dev/query?action=select"
        )
        body = '{"endpointId":""}' if shared else '{"pids":[]}'

        result = await self._request(
            "POST", f"appsync/group/{endpoint}" if "/" not in endpoint else endpoint,
            data_raw=body,
            headers=self._headers(familyid=familyid),
        )

        if result.get("status") != 0:
            raise AuxApiError(f"Failed to query devices: {result}")

        devices = []
        data = result["data"]
        if "endpoints" in data:
            devices = data["endpoints"] or []
        elif "shareFromOther" in data:
            devices = [d["devinfo"] for d in data["shareFromOther"]]

        if selected_devices:
            devices = [d for d in devices if d["endpointId"] in selected_devices]

        # Fetch online/offline state
        states = await self._bulk_query_state(devices)

        tasks = []
        for dev in devices:
            dev["state"] = next(
                (s["state"] for s in states.get("data", [])
                 if s["did"] == dev["endpointId"]),
                0,
            )
            dev["params"] = {}
            # Fetch params
            tasks.append(self._fetch_device_params(dev))

        await asyncio.gather(*tasks, return_exceptions=True)
        return devices

    async def _fetch_device_params(self, dev: dict) -> None:
        """Fetch both normal and special params for a device."""
        try:
            params = await self.get_device_params(dev, params=[])
            dev["params"] = params or {}
        except Exception as exc:
            _LOGGER.warning("Param fetch failed for %s: %s", dev["endpointId"], exc)

        special = AuxProducts.get_special_params_list(dev.get("productId", ""))
        if special:
            try:
                sp = await self.get_device_params(dev, params=special)
                if sp:
                    dev["params"].update(sp)
            except Exception:
                pass

        dev["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    async def _bulk_query_state(self, devices: list[dict]) -> dict:
        ts = int(time.time())
        queried = [
            {"did": d["endpointId"], "devSession": d["devSession"]}
            for d in devices
        ]
        data = {
            "directive": {
                "header": {
                    "namespace": "DNA.QueryState",
                    "name": "queryState",
                    "interfaceVersion": "2",
                    "senderId": "sdk",
                    "messageId": f"{self.userid}-{ts}",
                    "messageType": "controlgw.batch",
                    "timstamp": f"{ts}",
                },
                "payload": {"studata": queried, "msgtype": "batch"},
            }
        }
        result = await self._request(
            "POST", "device/control/v2/querystate",
            data=data, headers=self._headers(),
        )
        evt = result.get("event", {})
        payload = evt.get("payload", {})
        if payload.get("status") == 0:
            return payload
        return {"data": []}

    # ── Device parameter get/set ────────────────────────────────────
    async def _act_device_params(
        self, device: dict, act: str,
        params: list[str] | None = None,
        vals: list | None = None,
    ) -> dict:
        params = params or []
        vals = vals or []

        cookie = json.loads(base64.b64decode(device["cookie"].encode()))
        mapped_cookie = base64.b64encode(json.dumps({
            "device": {
                "id": cookie["terminalid"],
                "key": cookie["aeskey"],
                "devSession": device["devSession"],
                "aeskey": cookie["aeskey"],
                "did": device["endpointId"],
                "pid": device["productId"],
                "mac": device["mac"],
            }
        }, separators=(",", ":")).encode()).decode()

        ts = int(time.time())
        data = {
            "directive": {
                "header": {
                    "namespace": "DNA.KeyValueControl",
                    "name": "KeyValueControl",
                    "interfaceVersion": "2",
                    "senderId": "sdk",
                    "messageId": f"{device['endpointId']}-{ts}",
                },
                "endpoint": {
                    "devicePairedInfo": {
                        "did": device["endpointId"],
                        "pid": device["productId"],
                        "mac": device["mac"],
                        "devicetypeflag": device["devicetypeFlag"],
                        "cookie": mapped_cookie,
                    },
                    "endpointId": device["endpointId"],
                    "cookie": {},
                    "devSession": device["devSession"],
                },
                "payload": {
                    "act": act,
                    "params": params,
                    "vals": vals,
                    "did": device["endpointId"],
                },
            }
        }

        # Special handling for single-param GET
        if len(params) == 1 and act == "get":
            data["directive"]["payload"]["vals"] = [[{"val": 0, "idx": 1}]]

        result = await self._request(
            "POST", "device/control/v2/sdkcontrol",
            data=data,
            params={"license": _LICENSE},
            headers=self._headers(),
        )

        evt = result.get("event", {})
        if (
            evt.get("payload", {}).get("data")
            and evt.get("header", {}).get("name") == "Response"
        ):
            response = json.loads(evt["payload"]["data"])
            return {
                response["params"][i]: response["vals"][i][0]["val"]
                for i in range(len(response["params"]))
            }

        raise AuxApiError(f"Device param {act} failed: {result}")

    async def get_device_params(
        self, device: dict, params: list[str] | None = None,
    ) -> dict:
        return await self._act_device_params(device, "get", params)

    async def set_device_params(self, device: dict, values: dict) -> dict:
        params = list(values.keys())
        vals = [[{"idx": 1, "val": v}] for v in values.values()]
        return await self._act_device_params(device, "set", params, vals)

    # ── Fetch state for coordinator refresh ─────────────────────────
    async def fetch_devices_state(self, devices: list[dict]) -> None:
        """Refresh params for all tracked devices."""
        tasks = [self._fetch_device_params(d) for d in devices]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── WebSocket ───────────────────────────────────────────────────
    async def initialize_websocket(
        self, on_state_update=None,
    ) -> None:
        if not self.is_logged_in():
            raise AuxApiError("Not logged in")

        self.ws_api = AuxCloudWebSocket(
            region=self.region,
            headers=self._headers(CompanyId=_COMPANY_ID, Origin=self.url),
            loginsession=self.loginsession,
            userid=self.userid,
        )
        if on_state_update:
            self.ws_api.add_listener(on_state_update)
        await self.ws_api.initialize_websocket()

        # Wait for init ack
        for _ in range(10):
            if self.ws_api.api_initialized:
                return
            await asyncio.sleep(1)
        _LOGGER.warning("WebSocket init timed out")

    async def close(self) -> None:
        """Shutdown cloud API."""
        if self.ws_api:
            await self.ws_api.close()
