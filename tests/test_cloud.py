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
    XiaomiCloudDeviceTokenUnavailableError,
    XiaomiCloudError,
    XiaomiCloudSession,
    XiaomiCloudSessionError,
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
    cloud_session = client.export_session()
    assert cloud_session.username == "user@example.com"
    assert cloud_session.user_id == "123"
    assert cloud_session.service_token == "TOKEN"
    assert cloud_session.ssecurity == base64.b64encode(b"security").decode()
    assert "password" not in cloud_session.as_dict()
    submitted = session.post.await_args.kwargs["data"]
    assert submitted["user"] == "user@example.com"
    assert submitted["hash"] == "5EBE2294ECD0E0F08EAB7690D2A6EE69"
    assert "secret" not in submitted.values()
    login_request = session.get.await_args_list[0].kwargs
    assert login_request["cookies"]["sdkVersion"] == "3.8.6"
    assert "userId" not in login_request["cookies"]
    assert len(login_request["cookies"]["deviceId"]) == 16
    assert login_request["cookies"]["deviceId"].isalnum()
    assert login_request["cookies"]["deviceId"].upper() == login_request["cookies"]["deviceId"]
    assert login_request["cookies"]["deviceId"] in login_request["headers"]["User-Agent"]


@pytest.mark.asyncio
async def test_password_login_does_not_treat_step_one_location_as_authenticated() -> None:
    client, session = client_with_session()
    session.get.side_effect = [
        Response(
            {
                "_sign": "SIGN",
                "location": "https://account.xiaomi.com/fe/service/login",
                "sid": "xiaomiio",
            }
        ),
        Response({}, cookies=cookie(serviceToken="TOKEN", userId="123")),
    ]
    session.post.return_value = Response(
        {
            "location": "https://sts.api.io.mi.com/sts",
            "ssecurity": base64.b64encode(b"security").decode(),
            "userId": "123",
        }
    )

    await client.async_login()

    session.post.assert_awaited_once()
    assert session.post.await_args.kwargs["data"]["_sign"] == "SIGN"
    assert client._serviceToken == "TOKEN"


@pytest.mark.asyncio
async def test_password_login_rejects_credentials_and_malformed_data() -> None:
    client, session = client_with_session()
    session.get.return_value = Response({"_sign": "SIGN"})
    session.post.return_value = Response({"code": 70016})
    with pytest.raises(XiaomiCloudAuthenticationError) as error:
        await client.async_login()
    assert error.value.code == 70016

    client, session = client_with_session()
    session.get.return_value = Response(raw_text="not-json")
    with pytest.raises(XiaomiCloudError, match="malformed"):
        await client.async_login()


@pytest.mark.asyncio
async def test_password_login_requires_usable_cloud_session() -> None:
    client, session = client_with_session()
    session.get.side_effect = [
        Response({"_sign": "SIGN"}),
        Response({}),
    ]
    session.post.return_value = Response(
        {
            "location": "https://sts.api.io.mi.com/sts",
            "ssecurity": base64.b64encode(b"security").decode(),
            "userId": "123",
        }
    )

    with pytest.raises(XiaomiCloudSessionError, match="service token"):
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
            {
                "result": {
                    "device_info": [
                        {
                            "mac": ADDRESS,
                            "did": "blt.123",
                            "name": "Dryer",
                            "token": TOKEN,
                        }
                    ]
                }
            },
        ]
    )
    client.get_beaconkey = AsyncMock()

    assert await client.async_get_device_token(ADDRESS) == TOKEN
    client.get_devices.assert_any_await("cn", "2", "456")


@pytest.mark.asyncio
async def test_device_token_lookup_rejects_beaconkey_substitution() -> None:
    client, _ = client_with_session()
    client._serviceToken = "TOKEN"
    client._ssecurity = base64.b64encode(b"security").decode()
    client.userId = "123"
    client.get_homes = AsyncMock(side_effect=[{"result": {"homelist": [{"id": 1}]}}] + [None] * 7)
    client.get_dev_cnt = AsyncMock(return_value=None)
    client.get_devices = AsyncMock(
        side_effect=[
            {"result": {"device_info": [{"mac": ADDRESS, "did": "blt.123", "name": "Dryer"}]}}
        ]
        + [{"result": {"device_info": []}}] * 7
    )
    client.get_beaconkey = AsyncMock(return_value=None)

    with pytest.raises(XiaomiCloudDeviceTokenUnavailableError, match="BLE key"):
        await client.async_get_device_token(ADDRESS)
    client.get_beaconkey.assert_awaited_once_with("cn", "blt.123")


@pytest.mark.asyncio
async def test_device_token_lookup_uses_beaconkey_for_mesh_device() -> None:
    client, _ = client_with_session()
    client._serviceToken = "TOKEN"
    client._ssecurity = base64.b64encode(b"security").decode()
    client.userId = "123"
    client.get_homes = AsyncMock(side_effect=[{"result": {"homelist": [{"id": 1}]}}] + [None] * 7)
    client.get_dev_cnt = AsyncMock(return_value=None)
    client.get_devices = AsyncMock(
        side_effect=[
            {
                "result": {
                    "device_info": [
                        {
                            "mac": "aabbccddeeff",
                            "did": "blt.mesh-123",
                            "model": "soocare.magic_touch.m14",
                        }
                    ]
                }
            }
        ]
        + [{"result": {"device_info": []}}] * 7
    )
    client.get_beaconkey = AsyncMock(return_value={"result": {"beaconkey": TOKEN.upper()}})

    assert await client.async_get_device_token(ADDRESS) == TOKEN.upper()
    client.get_beaconkey.assert_awaited_once_with("cn", "blt.mesh-123")


@pytest.mark.asyncio
async def test_device_token_lookup_accepts_numeric_mesh_did() -> None:
    client, _ = client_with_session()
    client._serviceToken = "TOKEN"
    client._ssecurity = base64.b64encode(b"security").decode()
    client.userId = "123"
    client.get_homes = AsyncMock(side_effect=[{"result": {"homelist": [{"id": 1}]}}] + [None] * 7)
    client.get_dev_cnt = AsyncMock(return_value=None)
    client.get_devices = AsyncMock(
        side_effect=[
            {
                "result": {
                    "device_info": [
                        {
                            "mac": ADDRESS,
                            "did": "1096129305",
                            "model": "soocare.magic_touch.m14",
                            "pid": 16,
                            "token": TOKEN,
                        }
                    ]
                }
            }
        ]
        + [{"result": {"device_info": []}}] * 7
    )

    assert await client.async_get_device_token(ADDRESS, model="soocare.magic_touch.m14") == TOKEN


@pytest.mark.asyncio
async def test_mesh_ltmk_lookup_returns_cloud_mesh_key() -> None:
    client, _ = client_with_session()
    client._serviceToken = "TOKEN"
    client._ssecurity = base64.b64encode(b"security").decode()
    client.userId = "123"
    client.execute_api_call_encrypted = AsyncMock(return_value={"result": {"gatt_ltmk": "AB" * 32}})

    assert await client.async_get_mesh_ltmk("1096129305") == "ab" * 32
    client.execute_api_call_encrypted.assert_awaited_once()
    request = client.execute_api_call_encrypted.await_args
    assert request.args[0].endswith("/v2/blemesh/query_dev")
    assert '"did":"1096129305"' in request.args[1]["data"]


@pytest.mark.asyncio
async def test_mesh_ltmk_lookup_rejects_invalid_cloud_key() -> None:
    client, _ = client_with_session()
    client._serviceToken = "TOKEN"
    client._ssecurity = base64.b64encode(b"security").decode()
    client.userId = "123"
    client.execute_api_call_encrypted = AsyncMock(return_value={"result": {"gatt_ltmk": "bad"}})

    with pytest.raises(XiaomiCloudDeviceTokenUnavailableError, match="Mesh local key"):
        await client.async_get_mesh_ltmk("1096129305")


@pytest.mark.asyncio
async def test_mesh_device_lookup_matches_address_and_returns_ltmk() -> None:
    client, _ = client_with_session()
    client._serviceToken = "TOKEN"
    client._ssecurity = base64.b64encode(b"security").decode()
    client.userId = "123"
    client.get_homes = AsyncMock(side_effect=[{"result": {"homelist": [{"id": 1}]}}] + [None] * 7)
    client.get_dev_cnt = AsyncMock(return_value=None)
    client.get_devices = AsyncMock(
        side_effect=[
            {"result": {"device_info": [{"mac": ADDRESS, "did": "mesh-1", "model": "wrong"}]}},
        ]
        + [{"result": {"device_info": []}}] * 7
    )
    client.async_get_mesh_ltmk = AsyncMock(return_value="ab" * 32)

    assert await client.async_get_device_mesh_ltmk(ADDRESS, "soocare.magic_touch.m14") == "ab" * 32
    client.async_get_mesh_ltmk.assert_awaited_once_with("mesh-1", "cn")


@pytest.mark.asyncio
async def test_mesh_device_lookup_uses_unique_model_and_skips_invalid_key() -> None:
    client, _ = client_with_session()
    client._serviceToken = "TOKEN"
    client._ssecurity = base64.b64encode(b"security").decode()
    client.userId = "123"
    client.get_homes = AsyncMock(side_effect=[{"result": {"homelist": [{"id": 1}]}}] + [None] * 7)
    client.get_dev_cnt = AsyncMock(return_value=None)
    client.get_devices = AsyncMock(
        side_effect=[
            {
                "result": {
                    "device_info": [
                        {"did": "mesh-2", "model": "soocare.magic_touch.m14"}
                    ]
                }
            },
        ]
        + [{"result": {"device_info": []}}] * 7
    )
    client.async_get_mesh_ltmk = AsyncMock(
        side_effect=XiaomiCloudDeviceTokenUnavailableError("bad key")
    )

    assert await client.async_get_device_mesh_ltmk(ADDRESS, "soocare.magic_touch.m14") is None
    client.async_get_mesh_ltmk.assert_awaited_once_with("mesh-2", "cn")


@pytest.mark.asyncio
async def test_device_token_lookup_uses_unique_model_when_mac_is_not_returned() -> None:
    client, _ = client_with_session()
    client._serviceToken = "TOKEN"
    client._ssecurity = base64.b64encode(b"security").decode()
    client.userId = "123"
    client.get_homes = AsyncMock(side_effect=[{"result": {"homelist": [{"id": 1}]}}] + [None] * 7)
    client.get_dev_cnt = AsyncMock(return_value=None)
    client.get_devices = AsyncMock(
        side_effect=[
            {
                "result": {
                    "device_info": [
                        {
                            "did": "blt.unique-123",
                            "model": "soocare.magic_touch.m14",
                            "token": TOKEN,
                        }
                    ]
                }
            }
        ]
        + [{"result": {"device_info": []}}] * 7
    )

    assert await client.async_get_device_token(ADDRESS, model="soocare.magic_touch.m14") == TOKEN


@pytest.mark.asyncio
async def test_device_token_lookup_does_not_guess_between_same_models() -> None:
    client, _ = client_with_session()
    client._serviceToken = "TOKEN"
    client._ssecurity = base64.b64encode(b"security").decode()
    client.userId = "123"
    client.get_homes = AsyncMock(side_effect=[{"result": {"homelist": [{"id": 1}]}}] + [None] * 7)
    client.get_dev_cnt = AsyncMock(return_value=None)
    client.get_devices = AsyncMock(
        side_effect=[
            {
                "result": {
                    "device_info": [
                        {"did": "blt.one", "model": "soocare.magic_touch.m14"},
                        {"did": "blt.two", "model": "soocare.magic_touch.m14"},
                    ]
                }
            }
        ]
        + [{"result": {"device_info": []}}] * 7
    )

    assert await client.async_get_device_token(ADDRESS, model="soocare.magic_touch.m14") is None


@pytest.mark.asyncio
async def test_device_lookup_requires_complete_session_and_can_miss() -> None:
    client, _ = client_with_session()
    with pytest.raises(XiaomiCloudSessionError):
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


def test_cloud_session_round_trip_and_restored_client() -> None:
    cloud_session = XiaomiCloudSession(
        username="user@example.com",
        user_id="123",
        service_token="SERVICE",
        ssecurity=base64.b64encode(b"security").decode(),
        device_id="ABCDEF1234567890",
    )
    assert cloud_session.account_id == "123"
    assert cloud_session.display_name == "user@example.com (123)"
    assert XiaomiCloudSession.from_dict(cloud_session.as_dict()) == cloud_session

    http_session = SimpleNamespace(detach=MagicMock())
    client = XiaomiCloudClient.from_session(cloud_session, http_session, owns_session=True)
    assert client._username == "user@example.com"
    assert client._password == ""
    assert client.userId == "123"
    assert client._serviceToken == "SERVICE"
    assert client._ssecurity == cloud_session.ssecurity
    assert client._device_id == "ABCDEF1234567890"
    assert client._cookies["deviceId"] == "ABCDEF1234567890"
    assert client.export_session() == cloud_session


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"username": "user"},
        {
            "username": "user",
            "user_id": "123",
            "service_token": "SERVICE",
            "ssecurity": "not-base64",
            "device_id": "ABCDEF1234567890",
        },
        {
            "username": "user",
            "user_id": "123",
            "service_token": "SERVICE",
            "ssecurity": base64.b64encode(b"security").decode(),
            "device_id": "bad id",
        },
    ],
)
def test_cloud_session_rejects_invalid_storage(data: object) -> None:
    with pytest.raises(ValueError):
        XiaomiCloudSession.from_dict(data)


def test_export_rejects_incomplete_cloud_session() -> None:
    client, _ = client_with_session()
    with pytest.raises(XiaomiCloudSessionError, match="incomplete"):
        client.export_session()


def test_cloud_session_display_name_avoids_duplicate_user_id() -> None:
    cloud_session = XiaomiCloudSession(
        username="123",
        user_id="123",
        service_token="SERVICE",
        ssecurity=base64.b64encode(b"security").decode(),
        device_id="ABCDEF",
    )
    assert cloud_session.display_name == "123"
