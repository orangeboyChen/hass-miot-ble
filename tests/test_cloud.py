"""Tests for interactive Xiaomi Cloud authentication."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.miot_ble.cloud import (
    XiaomiCloudAuthenticationError,
    XiaomiCloudCaptchaError,
    XiaomiCloudCaptchaRequiredError,
    XiaomiCloudClient,
    XiaomiCloudError,
    XiaomiCloudVerificationError,
    XiaomiCloudVerificationRequiredError,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"
TOKEN = "00112233445566778899aabb"


def cookie(**values: str) -> dict[str, SimpleNamespace]:
    """Build the cookie mapping exposed by an aiohttp response."""
    return {name: SimpleNamespace(value=value) for name, value in values.items()}


class Response:
    """Provide the aiohttp response surface used by the cloud client."""

    def __init__(
        self,
        data: dict | list | None = None,
        *,
        status: int = 200,
        cookies: dict[str, SimpleNamespace] | None = None,
        content: bytes = b"",
        url: str = "https://account.xiaomi.com/result",
        raw_text: str | None = None,
        history: tuple[Response, ...] = (),
    ) -> None:
        import json

        self.status = status
        self.cookies = cookies or {}
        self.history = history
        self.url = url
        self._content = content
        self._text = raw_text if raw_text is not None else "&&&START&&&" + json.dumps(data)

    async def text(self) -> str:
        """Return response text."""
        return self._text

    async def read(self) -> bytes:
        """Return response bytes."""
        return self._content


def client_with_session() -> tuple[XiaomiCloudClient, SimpleNamespace]:
    """Build a cloud client with mock HTTP methods."""
    session = SimpleNamespace(get=AsyncMock(), post=AsyncMock())
    return XiaomiCloudClient("user@example.com", "secret", session), session


@pytest.mark.asyncio
async def test_password_login_success() -> None:
    client, session = client_with_session()
    session.get.side_effect = [
        Response({"_sign": "SIGN", "sid": "xiaomiio"}),
        Response({}, cookies=cookie(serviceToken="TOKEN", userId="123")),
    ]
    session.post.return_value = Response(
        {
            "location": "https://sts.api.io.mi.com/sts",
            "ssecurity": base64.b64encode(b"security").decode(),
            "userId": "123",
            "cUserId": "456",
            "passToken": "PASS",
        }
    )

    await client.async_login()

    assert client.userId == "123"
    assert client._serviceToken == "TOKEN"
    submitted = session.post.await_args.kwargs["data"]
    assert submitted["user"] == "user@example.com"
    assert submitted["hash"] == "5EBE2294ECD0E0F08EAB7690D2A6EE69"
    assert "secret" not in submitted.values()
    assert session.get.await_args_list[0].kwargs["cookies"]["userId"] == "user@example.com"


@pytest.mark.asyncio
async def test_password_login_rejects_credentials_and_malformed_data() -> None:
    client, session = client_with_session()
    session.get.return_value = Response({"_sign": "SIGN"})
    session.post.return_value = Response({"code": 70016})
    with pytest.raises(XiaomiCloudAuthenticationError):
        await client.async_login()

    client, session = client_with_session()
    session.get.return_value = Response(raw_text="not-json")
    with pytest.raises(XiaomiCloudError, match="malformed"):
        await client.async_login()


@pytest.mark.asyncio
async def test_captcha_challenge_and_refresh() -> None:
    client, session = client_with_session()
    session.get.side_effect = [
        Response({"_sign": "SIGN", "callback": "CALLBACK", "qs": "QUERY"}),
        Response(content=b"first", cookies=cookie(ick="ICK-1")),
        Response(content=b"second", cookies=cookie(ick="ICK-2")),
    ]
    session.post.side_effect = [
        Response({"code": 70016, "captchaUrl": "/captcha/first"}),
        Response({"code": 87001, "captchaUrl": "/captcha/second"}),
    ]

    with pytest.raises(XiaomiCloudCaptchaRequiredError):
        await client.async_login()
    assert client.captcha_image == base64.b64encode(b"first").decode()

    with pytest.raises(XiaomiCloudCaptchaError):
        await client.async_login(captcha="wrong")
    assert client.captcha_image == base64.b64encode(b"second").decode()
    retry = session.post.await_args_list[1].kwargs
    assert retry["data"]["captCode"] == "wrong"
    assert retry["data"]["callback"] == "CALLBACK"
    assert retry["cookies"]["ick"] == "ICK-1"


@pytest.mark.asyncio
async def test_captcha_success_reuses_context() -> None:
    client, session = client_with_session()
    client._login_context = {"_sign": "SIGN", "sid": "xiaomiio", "qs": "QUERY"}
    client._captcha_ick = "ICK"
    session.post.return_value = Response(
        {
            "location": "https://sts.api.io.mi.com/sts",
            "ssecurity": base64.b64encode(b"security").decode(),
            "userId": "123",
        }
    )
    session.get.return_value = Response({}, cookies=cookie(serviceToken="TOKEN"))

    await client.async_login(captcha="correct")

    assert client._serviceToken == "TOKEN"
    assert client.captcha_image is None
    assert session.post.await_args.kwargs["cookies"]["ick"] == "ICK"


@pytest.mark.asyncio
async def test_incomplete_captcha_is_rejected() -> None:
    client, session = client_with_session()
    session.get.side_effect = [
        Response({"_sign": "SIGN"}),
        Response(content=b"image"),
    ]
    session.post.return_value = Response({"captchaUrl": "/captcha"})

    with pytest.raises(XiaomiCloudError, match="incomplete"):
        await client.async_login()
    assert client.captcha_image is None


@pytest.mark.asyncio
async def test_verification_ticket_success() -> None:
    client, session = client_with_session()
    client.verification_url = (
        "https://account.xiaomi.com/fe/service/identity/authStart?sid=xiaomiio"
    )
    session.get.side_effect = [
        Response({"options": [4]}, cookies=cookie(identity_session="IDENTITY")),
        Response({}, url="https://account.xiaomi.com/result"),
        Response(
            {
                "code": 0,
                "location": "https://sts.api.io.mi.com/sts",
                "ssecurity": base64.b64encode(b"security").decode(),
                "userId": "123",
            }
        ),
        Response({}, cookies=cookie(serviceToken="TOKEN")),
    ]
    session.post.return_value = Response({"code": 0, "location": "/identity/result/check?id=1"})

    await client.async_login(verification_ticket="TICKET")

    verify_request = session.post.await_args.kwargs
    assert verify_request["data"] == {
        "_flag": 4,
        "ticket": "TICKET",
        "trust": "false",
        "_json": "true",
    }
    assert verify_request["cookies"]["identity_session"] == "IDENTITY"
    assert client._serviceToken == "TOKEN"


@pytest.mark.asyncio
async def test_verification_required_and_rejected_ticket_remains_retryable() -> None:
    client, session = client_with_session()
    session.get.return_value = Response({"_sign": "SIGN"})
    session.post.return_value = Response({"notificationUrl": "/fe/service/identity/authStart"})
    with pytest.raises(XiaomiCloudVerificationRequiredError) as challenge:
        await client.async_login()
    assert challenge.value.url.startswith("https://account.xiaomi.com/")

    session.get.return_value = Response(
        {"options": [8]}, cookies=cookie(identity_session="IDENTITY")
    )
    session.post.return_value = Response({"code": 87001})
    with pytest.raises(XiaomiCloudVerificationError):
        await client.async_login(verification_ticket="BAD")
    assert client.verification_url == challenge.value.url


@pytest.mark.asyncio
async def test_verification_rejects_empty_and_unsupported_method() -> None:
    client, session = client_with_session()
    client.verification_url = "https://account.xiaomi.com/fe/service/identity/authStart"
    with pytest.raises(XiaomiCloudVerificationError):
        await client.async_login(verification_ticket=" ")

    session.get.return_value = Response(
        {"options": [99]}, cookies=cookie(identity_session="IDENTITY")
    )
    with pytest.raises(XiaomiCloudError, match="unsupported"):
        await client.async_login(verification_ticket="TICKET")


@pytest.mark.asyncio
async def test_device_token_lookup_checks_owned_and_shared_homes() -> None:
    client, _ = client_with_session()
    client._serviceToken = "TOKEN"
    client._ssecurity = base64.b64encode(b"security").decode()
    client.userId = "123"
    client.get_homes = AsyncMock(side_effect=[{"result": {"homelist": [{"id": 1}]}}] + [None] * 7)
    client.get_dev_cnt = AsyncMock(
        side_effect=[{"result": {"share": {"share_family": [{"home_id": 2, "home_owner": 456}]}}}]
        + [None] * 7
    )
    client.get_devices = AsyncMock(
        side_effect=[
            {"result": {"device_info": []}},
            {"result": {"device_info": [{"mac": ADDRESS, "did": "blt.123", "name": "Dryer"}]}},
        ]
    )
    client.get_beaconkey = AsyncMock(return_value={"result": {"beaconkey": TOKEN}})

    assert await client.async_get_device_token(ADDRESS) == TOKEN
    client.get_devices.assert_any_await("cn", "2", "456")
    client.get_beaconkey.assert_awaited_once_with("cn", "blt.123")


@pytest.mark.asyncio
async def test_device_lookup_requires_complete_session_and_can_miss() -> None:
    client, _ = client_with_session()
    with pytest.raises(XiaomiCloudAuthenticationError):
        await client.async_get_device_token(ADDRESS)

    client._serviceToken = "TOKEN"
    client._ssecurity = base64.b64encode(b"security").decode()
    client.userId = "123"
    client.get_homes = AsyncMock(return_value=None)
    client.get_dev_cnt = AsyncMock(return_value=None)
    assert await client.async_get_device_token(ADDRESS) is None

    client.clear_sensitive_data()
    assert client._username == ""
    assert client._password == ""
    assert client._serviceToken is None
    assert client.userId is None


def test_owned_session_is_detached_when_secrets_are_cleared() -> None:
    session = SimpleNamespace(detach=MagicMock())
    client = XiaomiCloudClient("user@example.com", "secret", session, owns_session=True)
    client.clear_sensitive_data()
    client.clear_sensitive_data()
    session.detach.assert_called_once_with()
