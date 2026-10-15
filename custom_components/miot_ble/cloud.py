"""Xiaomi Cloud authentication and BLE key retrieval."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp
from xiaomi_ble.cloud import SERVERS, XiaomiCloudConnector, format_mac_upper

ACCOUNT_BASE = "https://account.xiaomi.com"
LOGIN_URL = f"{ACCOUNT_BASE}/pass/serviceLogin"
LOGIN_AUTH_URL = f"{ACCOUNT_BASE}/pass/serviceLoginAuth2"


class XiaomiCloudError(Exception):
    """Raised when Xiaomi Cloud returns an unusable response."""


class XiaomiCloudAuthenticationError(XiaomiCloudError):
    """Raised when Xiaomi rejects the account credentials."""


class XiaomiCloudCaptchaRequiredError(XiaomiCloudError):
    """Raised when Xiaomi requires an image captcha."""


class XiaomiCloudCaptchaError(XiaomiCloudCaptchaRequiredError):
    """Raised when Xiaomi rejects an image captcha answer."""


class XiaomiCloudVerificationRequiredError(XiaomiCloudError):
    """Raised when Xiaomi requires an identity verification ticket."""

    def __init__(self, url: str) -> None:
        super().__init__("Xiaomi identity verification is required")
        self.url = url


class XiaomiCloudVerificationError(XiaomiCloudError):
    """Raised when Xiaomi rejects an identity verification ticket."""


class XiaomiCloudClient(XiaomiCloudConnector):
    """Authenticate interactively and retrieve a BLE beacon key.

    The challenge flow follows the behavior implemented by the MIT-licensed
    al-one/hass-xiaomi-miot project. Account secrets and challenge state remain
    only in this short-lived object owned by the Home Assistant config flow.
    """

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        *,
        owns_session: bool = False,
    ) -> None:
        super().__init__(username, password, session)
        self._cookies = {
            "sdkVersion": "accountsdk-18.8.15",
            "deviceId": self._device_id,
        }
        self._account_cookies = dict(self._cookies)
        self._owns_session = owns_session
        self._login_context: dict[str, Any] = {}
        self.captcha_url: str | None = None
        self.captcha_image: str | None = None
        self._captcha_ick: str | None = None
        self.verification_url: str | None = None
        self._identity_session: str | None = None

    async def async_login(
        self,
        *,
        captcha: str | None = None,
        verification_ticket: str | None = None,
    ) -> None:
        """Complete password login or continue an interactive challenge."""
        if captcha and verification_ticket:
            raise ValueError("Only one Xiaomi login challenge can be submitted")

        if verification_ticket is not None:
            location = await self._async_verify_ticket(verification_ticket)
            await self._async_follow_verification(location)
            auth = await self._async_login_step_1()
            location = str(auth.get("location") or "")
            if not location:
                location = await self._async_login_step_2(auth)
        elif captcha is not None:
            context = self._login_context or await self._async_login_step_1()
            location = await self._async_login_step_2(context, captcha=captcha)
        else:
            self._clear_challenges()
            auth = await self._async_login_step_1()
            location = str(auth.get("location") or "")
            if not location:
                location = await self._async_login_step_2(auth)

        await self._async_login_step_3(location)
        self._clear_challenges()

    async def async_get_device_token(self, mac: str) -> str | None:
        """Return the BLE beacon key for a MAC address from all cloud regions."""
        if not self._serviceToken or not self._ssecurity or not self.userId:
            raise XiaomiCloudAuthenticationError("Xiaomi Cloud session is incomplete")

        formatted_mac = format_mac_upper(mac)
        for server in SERVERS:
            homes: dict[str, str] = {}
            home_info = await self.get_homes(server)
            for home in ((home_info or {}).get("result") or {}).get("homelist") or []:
                if home_id := home.get("id"):
                    homes[str(home_id)] = str(self.userId)

            device_count = await self.get_dev_cnt(server)
            shares = (((device_count or {}).get("result") or {}).get("share") or {}).get(
                "share_family"
            ) or []
            for home in shares:
                if home_id := home.get("home_id"):
                    homes[str(home_id)] = str(home.get("home_owner") or self.userId)

            for home_id, owner_id in homes.items():
                devices = await self.get_devices(server, home_id, owner_id)
                device_info = ((devices or {}).get("result") or {}).get("device_info") or []
                for device in device_info:
                    did = str(device.get("did") or "")
                    if str(device.get("mac") or "").upper() != formatted_mac or "blt" not in did:
                        continue
                    key_result = await self.get_beaconkey(server, did)
                    beacon_key = ((key_result or {}).get("result") or {}).get("beaconkey")
                    if beacon_key:
                        return str(beacon_key)
        return None

    def clear_sensitive_data(self) -> None:
        """Discard account credentials, cookies, and authenticated session data."""
        self._username = ""
        self._password = ""
        self._cookies.clear()
        self._account_cookies.clear()
        self._ssecurity = None
        self._serviceToken = None
        self._passToken = None
        self._cUserId = None
        self._location = None
        self._code = None
        self.userId = None
        self._clear_challenges()
        if self._owns_session:
            self._session.detach()
            self._owns_session = False

    async def _async_login_step_1(self) -> dict[str, Any]:
        cookies = dict(self._account_cookies)
        cookies.setdefault("userId", self._username)
        response = await self._session.get(
            LOGIN_URL,
            params={"sid": "xiaomiio", "_json": "true"},
            headers=self._headers,
            cookies=cookies,
        )
        self._update_cookies(response)
        if response.status != 200:
            raise XiaomiCloudError("Xiaomi login initialization failed")
        auth = self._decode_json(await response.text())
        if auth.get("code") == 0:
            self._store_auth(auth)
        if not auth.get("_sign") and not auth.get("location"):
            raise XiaomiCloudAuthenticationError("Xiaomi rejected the account")
        return auth

    async def _async_login_step_2(
        self,
        auth: dict[str, Any],
        *,
        captcha: str | None = None,
    ) -> str:
        if not self._password:
            raise XiaomiCloudAuthenticationError("Xiaomi account password is missing")
        context = {
            "callback": auth.get("callback") or "",
            "sid": auth.get("sid") or "xiaomiio",
            "qs": auth.get("qs") or "",
            "_sign": auth.get("_sign") or "",
        }
        form = {
            "user": self._username,
            "hash": hashlib.md5(self._password.encode(), usedforsecurity=False).hexdigest().upper(),
            **context,
        }
        cookies: dict[str, str] = {}
        params: dict[str, Any] = {"_json": "true"}
        if captcha is not None:
            form["captCode"] = captcha
            params["_dc"] = int(time.time() * 1000)
            if self._captcha_ick:
                cookies["ick"] = self._captcha_ick

        response = await self._session.post(
            LOGIN_AUTH_URL,
            params=params,
            data=form,
            headers=self._headers,
            cookies={**self._account_cookies, **cookies},
        )
        self._update_cookies(response)
        if response.status != 200:
            raise XiaomiCloudError("Xiaomi password login failed")
        result = self._decode_json(await response.text())
        if location := result.get("location"):
            self._store_auth(result)
            if not self._ssecurity:
                raise XiaomiCloudAuthenticationError("Xiaomi login omitted account security data")
            return str(location)

        if notification_url := result.get("notificationUrl"):
            self._clear_captcha()
            self.verification_url = self._absolute_url(str(notification_url))
            raise XiaomiCloudVerificationRequiredError(self.verification_url)

        if captcha_url := result.get("captchaUrl"):
            self._login_context = context
            await self._async_load_captcha(self._absolute_url(str(captcha_url)))
            if result.get("code") == 87001:
                raise XiaomiCloudCaptchaError("Xiaomi rejected the captcha answer")
            raise XiaomiCloudCaptchaRequiredError("Xiaomi requires a captcha")

        self._clear_captcha()
        if result.get("code") in (20003, 70002, 70016):
            raise XiaomiCloudAuthenticationError("Xiaomi rejected the account credentials")
        if result.get("code") == 81003 and self.verification_url:
            raise XiaomiCloudVerificationRequiredError(self.verification_url)
        raise XiaomiCloudError("Xiaomi returned an unknown login response")

    async def _async_login_step_3(self, location: str) -> None:
        if not location:
            raise XiaomiCloudAuthenticationError("Xiaomi login omitted the service URL")
        response = await self._session.get(
            self._absolute_url(location),
            headers=self._headers,
            cookies=self._account_cookies,
        )
        self._update_cookies(response)
        service_token = self._cookie_value(response, "serviceToken")
        if response.status != 200 or not service_token:
            raise XiaomiCloudAuthenticationError("Xiaomi login omitted the service token")
        self._serviceToken = service_token
        self.userId = self._cookie_value(response, "userId") or self.userId
        self._cUserId = self._cookie_value(response, "cUserId") or self._cUserId

    async def _async_load_captcha(self, url: str) -> None:
        self._clear_captcha()
        response = await self._session.get(
            url,
            headers=self._headers,
            cookies=self._account_cookies,
        )
        self._update_cookies(response)
        image = await response.read()
        ick = self._cookie_value(response, "ick")
        if response.status != 200 or not image or not ick:
            raise XiaomiCloudError("Xiaomi returned an incomplete captcha challenge")
        self.captcha_url = url
        self.captcha_image = base64.b64encode(image).decode()
        self._captcha_ick = ick

    async def _async_verify_ticket(self, ticket: str) -> str:
        if not ticket.strip():
            raise XiaomiCloudVerificationError("Xiaomi verification ticket is empty")
        if not self.verification_url:
            raise XiaomiCloudError("Xiaomi verification URL is missing")

        parsed = urlparse(self.verification_url)
        identity_path = "fe/service/identity/authStart"
        if identity_path not in parsed.path:
            raise XiaomiCloudError("Xiaomi verification method is unsupported")
        identity_url = self.verification_url.replace(identity_path, "identity/list")
        response = await self._session.get(
            identity_url,
            headers=self._headers,
            cookies=self._account_cookies,
        )
        self._update_cookies(response)
        self._identity_session = self._cookie_value(response, "identity_session")
        if response.status != 200 or not self._identity_session:
            raise XiaomiCloudError("Xiaomi identity session is missing")
        result = self._decode_json(await response.text())
        options = result.get("options") or [result.get("flag", 4)]

        attempted = False
        for flag in options:
            api = {4: "/identity/auth/verifyPhone", 8: "/identity/auth/verifyEmail"}.get(flag)
            if not api:
                continue
            attempted = True
            response = await self._session.post(
                self._absolute_url(api),
                params={"_dc": int(time.time() * 1000)},
                data={
                    "_flag": flag,
                    "ticket": ticket.strip(),
                    "trust": "false",
                    "_json": "true",
                },
                headers=self._headers,
                cookies={
                    **self._account_cookies,
                    "identity_session": self._identity_session,
                },
            )
            self._update_cookies(response)
            if response.status != 200:
                raise XiaomiCloudError("Xiaomi verification request failed")
            verified = self._decode_json(await response.text())
            if verified.get("code") == 0 and verified.get("location"):
                self._identity_session = None
                return str(verified["location"])
        if attempted:
            raise XiaomiCloudVerificationError("Xiaomi rejected the verification ticket")
        raise XiaomiCloudError("Xiaomi verification method is unsupported")

    async def _async_follow_verification(self, location: str) -> None:
        response = await self._session.get(
            self._absolute_url(location),
            headers=self._headers,
            cookies=self._account_cookies,
            allow_redirects=True,
        )
        self._update_cookies(response)
        parsed = urlparse(str(response.url))
        if parsed.path.startswith("/fe/"):
            skip_url = (parse_qs(parsed.query).get("skipUrl") or [None])[0]
            if skip_url:
                response = await self._session.get(
                    self._absolute_url(skip_url),
                    headers=self._headers,
                    cookies=self._account_cookies,
                    allow_redirects=True,
                )
                self._update_cookies(response)
        if response.status >= 400:
            raise XiaomiCloudAuthenticationError("Xiaomi verification could not complete login")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._agent,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _store_auth(self, data: dict[str, Any]) -> None:
        self._ssecurity = data.get("ssecurity") or self._ssecurity
        self.userId = str(data.get("userId") or self.userId or "") or None
        self._cUserId = data.get("cUserId") or self._cUserId
        self._passToken = data.get("passToken") or self._passToken

    def _update_cookies(self, response: aiohttp.ClientResponse) -> None:
        for item in (*response.history, response):
            for name, morsel in item.cookies.items():
                self._account_cookies[name] = morsel.value

    def _cookie_value(self, response: aiohttp.ClientResponse, name: str) -> str | None:
        for item in (*response.history, response):
            if morsel := item.cookies.get(name):
                return morsel.value
        return None

    def _clear_captcha(self) -> None:
        self.captcha_url = None
        self.captcha_image = None
        self._captcha_ick = None

    def _clear_challenges(self) -> None:
        self._clear_captcha()
        self._login_context.clear()
        self.verification_url = None
        self._identity_session = None

    @staticmethod
    def _decode_json(text: str) -> dict[str, Any]:
        try:
            result = json.loads(text.removeprefix("&&&START&&&"))
        except (TypeError, ValueError) as error:
            raise XiaomiCloudError("Xiaomi returned malformed login data") from error
        if not isinstance(result, dict):
            raise XiaomiCloudError("Xiaomi returned malformed login data")
        return result

    @staticmethod
    def _absolute_url(url: str) -> str:
        return urljoin(f"{ACCOUNT_BASE}/", url)
