"""Tests for the Xiaomi MIoT BLE config flow."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_REAUTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_MODEL, CONF_NAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.miot_ble.cloud import (
    XiaomiCloudAuthenticationError,
    XiaomiCloudCaptchaError,
    XiaomiCloudCaptchaRequiredError,
    XiaomiCloudDeviceTokenUnavailableError,
    XiaomiCloudError,
    XiaomiCloudSessionError,
    XiaomiCloudVerificationError,
    XiaomiCloudVerificationRequiredError,
)
from custom_components.miot_ble.config_flow import (
    CloudDeviceNotFoundError,
    MiotBleConfigFlow,
    _discovered_devices,
    _profile_for_discovery,
    async_fetch_cloud_token,
    async_validate_connection,
    normalize_token,
)
from custom_components.miot_ble.const import CONF_TOKEN, DOMAIN, UUID_MI_SERVICE
from custom_components.miot_ble.profiles import (
    MUXNW_DRYER_M73,
    XIAOMI_MAGIC_TOUCH_YB01,
    XIAOMI_MAGIC_TOUCH_YB11,
)
from custom_components.miot_ble.protocol import AuthenticationError

ADDRESS = "AA:BB:CC:DD:EE:FF"
TOKEN = "00112233445566778899aabb"
MODEL = "muxnw.dryer.m73"


def discovery_info(
    *,
    address: str = ADDRESS,
    product_id: int | None = 0x5A5B,
    name: str | None = "M73",
) -> SimpleNamespace:
    """Build the discovery fields used by the config flow."""
    return SimpleNamespace(
        address=address,
        name=name,
        service_uuids=[UUID_MI_SERVICE],
        service_data=(
            {UUID_MI_SERVICE: b"\x00\x20" + product_id.to_bytes(2, "little") + b"\x00"}
            if product_id is not None
            else {}
        ),
    )


def config_flow(hass, source: str) -> MiotBleConfigFlow:
    """Build a config flow without loading optional platform dependencies."""
    flow = MiotBleConfigFlow()
    flow.hass = hass
    flow.context = {"source": source}
    return flow


def supported_devices() -> dict[str, tuple[str, object]]:
    """Return the supported discovery mapping used by config-flow tests."""
    return {ADDRESS: ("M73", MUXNW_DRYER_M73)}


def test_normalize_token() -> None:
    assert normalize_token(f"  {TOKEN.upper()}  ") == TOKEN
    for invalid in ("", "0011", "g" * 24, "0" * 32):
        with pytest.raises(ValueError, match="24 hexadecimal"):
            normalize_token(invalid)


def test_discovery_uses_product_id_and_hides_unsupported_devices() -> None:
    supported = discovery_info(name="Unexpected name")
    unsupported = discovery_info(
        address="11:22:33:44:55:66",
        product_id=0x0001,
        name="M73",
    )
    missing_data = discovery_info(
        address="22:33:44:55:66:77",
        product_id=None,
    )
    malformed = discovery_info(address="33:44:55:66:77:88")
    malformed.service_data = {UUID_MI_SERVICE: b"\x00\x20\x5b"}
    assert _profile_for_discovery(supported) is MUXNW_DRYER_M73
    assert _profile_for_discovery(unsupported) is None
    assert _profile_for_discovery(missing_data) is None
    assert _profile_for_discovery(malformed) is None
    with patch(
        "custom_components.miot_ble.config_flow.async_discovered_service_info",
        return_value=[supported, unsupported, missing_data, malformed],
    ):
        assert _discovered_devices(MagicMock()) == {ADDRESS: ("Unexpected name", MUXNW_DRYER_M73)}


@pytest.mark.parametrize(
    ("product_id", "profile"),
    [
        (0x46C6, XIAOMI_MAGIC_TOUCH_YB01),
        (0x8FD3, XIAOMI_MAGIC_TOUCH_YB11),
    ],
)
def test_massager_discovery_uses_fe95_product_id(product_id: int, profile: object) -> None:
    assert _profile_for_discovery(discovery_info(product_id=product_id, name=None)) is profile


@pytest.mark.asyncio
async def test_user_flow_aborts_without_supported_devices(hass) -> None:
    flow = config_flow(hass, SOURCE_USER)
    with patch("custom_components.miot_ble.config_flow._discovered_devices", return_value={}):
        result = await flow.async_step_user()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_supported_devices"


@pytest.mark.asyncio
async def test_user_flow_routes_missing_token_to_cloud(hass) -> None:
    flow = config_flow(hass, SOURCE_USER)
    validate = AsyncMock()
    with (
        patch(
            "custom_components.miot_ble.config_flow._discovered_devices",
            return_value=supported_devices(),
        ),
        patch("custom_components.miot_ble.config_flow.async_validate_connection", validate),
    ):
        result = await flow.async_step_user(
            {
                CONF_ADDRESS: ADDRESS,
                CONF_NAME: "Dryer",
            }
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "cloud_auth"
    validate.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloud_flow_fetches_token_without_storing_credentials(hass) -> None:
    flow = config_flow(hass, SOURCE_USER)
    with patch(
        "custom_components.miot_ble.config_flow._discovered_devices",
        return_value=supported_devices(),
    ):
        result = await flow.async_step_user({CONF_ADDRESS: ADDRESS, CONF_NAME: "Dryer"})
    assert result["step_id"] == "cloud_auth"

    client = SimpleNamespace(
        async_login=AsyncMock(),
        async_get_device_token=AsyncMock(return_value=TOKEN),
        clear_sensitive_data=MagicMock(),
        captcha_image=None,
        verification_url=None,
    )
    validate = AsyncMock()
    with (
        patch("custom_components.miot_ble.config_flow.XiaomiCloudClient", return_value=client),
        patch("custom_components.miot_ble.config_flow.async_create_clientsession"),
        patch("custom_components.miot_ble.config_flow.async_validate_connection", validate),
    ):
        result = await flow.async_step_cloud_auth(
            {"username": "user@example.com", "password": "secret"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TOKEN] == TOKEN
    assert "username" not in result["data"]
    assert "password" not in result["data"]
    client.async_login.assert_awaited_once_with()
    client.async_get_device_token.assert_awaited_once_with(ADDRESS)
    client.clear_sensitive_data.assert_called_once_with()
    validate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("validation_error", "expected_error"),
    [
        (AuthenticationError("bad token"), "invalid_auth"),
        (ConnectionError("offline"), "cannot_connect"),
    ],
)
async def test_cloud_flow_reports_device_connection_failure(
    hass,
    validation_error: Exception,
    expected_error: str,
) -> None:
    flow = config_flow(hass, SOURCE_USER)
    flow._pending_setup = {
        CONF_ADDRESS: ADDRESS,
        CONF_MODEL: MODEL,
        CONF_NAME: "Dryer",
    }
    client = SimpleNamespace(
        async_login=AsyncMock(),
        async_get_device_token=AsyncMock(return_value=TOKEN),
        clear_sensitive_data=MagicMock(),
        captcha_image=None,
        verification_url=None,
    )
    with (
        patch("custom_components.miot_ble.config_flow.XiaomiCloudClient", return_value=client),
        patch("custom_components.miot_ble.config_flow.async_create_clientsession"),
        patch(
            "custom_components.miot_ble.config_flow.async_validate_connection",
            new=AsyncMock(side_effect=validation_error),
        ),
    ):
        result = await flow.async_step_cloud_auth(
            {"username": "user@example.com", "password": "secret"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == expected_error
    client.clear_sensitive_data.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("login_error", "expected_error"),
    [
        (
            XiaomiCloudAuthenticationError(code=70016),
            "cloud_invalid_password",
        ),
        (XiaomiCloudSessionError("missing token"), "cloud_session_error"),
    ],
)
async def test_cloud_login_reports_specific_failure(
    hass,
    login_error: Exception,
    expected_error: str,
) -> None:
    flow = config_flow(hass, SOURCE_USER)
    flow._pending_setup = {
        CONF_ADDRESS: ADDRESS,
        CONF_MODEL: MODEL,
        CONF_NAME: "Dryer",
    }
    client = SimpleNamespace(
        async_login=AsyncMock(side_effect=login_error),
        clear_sensitive_data=MagicMock(),
        captcha_image=None,
        verification_url=None,
    )
    with (
        patch("custom_components.miot_ble.config_flow.XiaomiCloudClient", return_value=client),
        patch("custom_components.miot_ble.config_flow.async_create_clientsession"),
    ):
        result = await flow.async_step_cloud_auth(
            {"username": "user@example.com", "password": "secret"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == expected_error
    client.clear_sensitive_data.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cloud_error", "expected_error"),
    [
        (XiaomiCloudAuthenticationError(), "cloud_invalid_auth"),
        (
            XiaomiCloudAuthenticationError(code=70016),
            "cloud_invalid_password",
        ),
        (
            XiaomiCloudAuthenticationError(code=20003),
            "cloud_invalid_account",
        ),
        (
            XiaomiCloudAuthenticationError(code=81003),
            "cloud_verification_required",
        ),
        (XiaomiCloudSessionError(), "cloud_session_error"),
        (CloudDeviceNotFoundError(), "cloud_device_not_found"),
        (XiaomiCloudDeviceTokenUnavailableError(), "cloud_token_unavailable"),
        (XiaomiCloudError(), "cloud_error"),
        (ValueError(), "cloud_key_unsupported"),
    ],
)
async def test_cloud_flow_errors(hass, cloud_error: Exception, expected_error: str) -> None:
    flow = config_flow(hass, SOURCE_USER)
    flow._pending_setup = {
        CONF_ADDRESS: ADDRESS,
        CONF_MODEL: MODEL,
        CONF_NAME: "Dryer",
    }
    client = SimpleNamespace(
        async_login=AsyncMock(),
        async_get_device_token=AsyncMock(return_value=TOKEN),
        clear_sensitive_data=MagicMock(),
        captcha_image=None,
        verification_url=None,
    )
    with (
        patch("custom_components.miot_ble.config_flow.XiaomiCloudClient", return_value=client),
        patch("custom_components.miot_ble.config_flow.async_create_clientsession"),
        patch(
            "custom_components.miot_ble.config_flow.async_fetch_cloud_token",
            new=AsyncMock(side_effect=cloud_error),
        ),
    ):
        result = await flow.async_step_cloud_auth(
            {"username": "user@example.com", "password": "secret"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == expected_error


@pytest.mark.asyncio
async def test_cloud_flow_rejects_missing_setup_state(hass) -> None:
    flow = config_flow(hass, SOURCE_USER)
    result = await flow.async_step_cloud_auth()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "missing_setup_data"


@pytest.mark.asyncio
async def test_fetch_cloud_token() -> None:
    client = SimpleNamespace(async_get_device_token=AsyncMock(return_value=TOKEN.upper()))
    assert await async_fetch_cloud_token(client, ADDRESS) == TOKEN
    client.async_get_device_token.assert_awaited_once_with(ADDRESS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("library_error", "expected_error"),
    [
        ("auth", XiaomiCloudSessionError),
        ("cloud", XiaomiCloudError),
        ("missing", CloudDeviceNotFoundError),
    ],
)
async def test_fetch_cloud_token_errors(
    library_error: str, expected_error: type[Exception]
) -> None:
    from xiaomi_ble import XiaomiCloudException, XiaomiCloudInvalidAuthenticationException

    if library_error == "auth":
        side_effect = XiaomiCloudInvalidAuthenticationException("bad credentials")
    elif library_error == "cloud":
        side_effect = XiaomiCloudException("offline")
    else:
        side_effect = None
    client = SimpleNamespace(
        async_get_device_token=(
            AsyncMock(return_value=None)
            if library_error == "missing"
            else AsyncMock(side_effect=side_effect)
        )
    )
    with pytest.raises(expected_error):
        await async_fetch_cloud_token(client, ADDRESS)


@pytest.mark.asyncio
async def test_cloud_flow_routes_captcha_and_accepts_answer(hass) -> None:
    flow = config_flow(hass, SOURCE_USER)
    flow._pending_setup = {
        CONF_ADDRESS: ADDRESS,
        CONF_MODEL: MODEL,
        CONF_NAME: "Dryer",
    }
    client = SimpleNamespace(
        async_login=AsyncMock(side_effect=XiaomiCloudCaptchaRequiredError()),
        async_get_device_token=AsyncMock(return_value=TOKEN),
        clear_sensitive_data=MagicMock(),
        captcha_image="IMAGE",
        verification_url=None,
    )
    with (
        patch("custom_components.miot_ble.config_flow.XiaomiCloudClient", return_value=client),
        patch("custom_components.miot_ble.config_flow.async_create_clientsession"),
    ):
        result = await flow.async_step_cloud_auth(
            {"username": "user@example.com", "password": "secret"}
        )
    assert result["step_id"] == "cloud_captcha"
    assert result["description_placeholders"]["captcha_image"].endswith("IMAGE")

    client.async_login.side_effect = None
    with patch(
        "custom_components.miot_ble.config_flow.async_validate_connection",
        new=AsyncMock(),
    ):
        result = await flow.async_step_cloud_captcha({"captcha": "1234"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    client.async_login.assert_awaited_with(captcha="1234")


@pytest.mark.asyncio
async def test_cloud_captcha_rejection_shows_refreshed_challenge(hass) -> None:
    flow = config_flow(hass, SOURCE_USER)
    flow._pending_setup = {
        CONF_ADDRESS: ADDRESS,
        CONF_MODEL: MODEL,
        CONF_NAME: "Dryer",
    }
    flow._cloud_client = SimpleNamespace(
        async_login=AsyncMock(side_effect=XiaomiCloudCaptchaError()),
        clear_sensitive_data=MagicMock(),
        captcha_image="REFRESHED",
        verification_url=None,
    )
    result = await flow.async_step_cloud_captcha({"captcha": "wrong"})
    assert result["step_id"] == "cloud_captcha"
    assert result["errors"]["base"] == "cloud_invalid_captcha"
    assert result["description_placeholders"]["captcha_image"].endswith("REFRESHED")


@pytest.mark.asyncio
async def test_cloud_flow_routes_verification_and_retries_ticket(hass) -> None:
    flow = config_flow(hass, SOURCE_USER)
    flow._pending_setup = {
        CONF_ADDRESS: ADDRESS,
        CONF_MODEL: MODEL,
        CONF_NAME: "Dryer",
    }
    challenge = XiaomiCloudVerificationRequiredError("https://account.xiaomi.com/verify")
    client = SimpleNamespace(
        async_login=AsyncMock(side_effect=challenge),
        async_get_device_token=AsyncMock(return_value=TOKEN),
        clear_sensitive_data=MagicMock(),
        captcha_image=None,
        verification_url=challenge.url,
    )
    with (
        patch("custom_components.miot_ble.config_flow.XiaomiCloudClient", return_value=client),
        patch("custom_components.miot_ble.config_flow.async_create_clientsession"),
    ):
        result = await flow.async_step_cloud_auth(
            {"username": "user@example.com", "password": "secret"}
        )
    assert result["step_id"] == "cloud_verify"
    assert result["description_placeholders"]["verification_url"] == challenge.url

    client.async_login.side_effect = XiaomiCloudVerificationError()
    result = await flow.async_step_cloud_verify({"verification_ticket": "bad"})
    assert result["errors"]["base"] == "cloud_invalid_verification"

    client.async_login.side_effect = None
    with patch(
        "custom_components.miot_ble.config_flow.async_validate_connection",
        new=AsyncMock(),
    ):
        result = await flow.async_step_cloud_verify({"verification_ticket": "good"})
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_cloud_challenge_requires_transient_state(hass) -> None:
    flow = config_flow(hass, SOURCE_USER)
    flow._pending_setup = {
        CONF_ADDRESS: ADDRESS,
        CONF_MODEL: MODEL,
        CONF_NAME: "Dryer",
    }
    captcha = await flow.async_step_cloud_captcha()
    verify = await flow.async_step_cloud_verify()
    assert captcha["reason"] == "missing_cloud_challenge"
    assert verify["reason"] == "missing_cloud_challenge"


def test_removing_flow_clears_transient_cloud_state(hass) -> None:
    flow = config_flow(hass, SOURCE_USER)
    client = SimpleNamespace(clear_sensitive_data=MagicMock())
    flow._cloud_client = client
    flow.async_remove()
    client.clear_sensitive_data.assert_called_once_with()
    assert flow._cloud_client is None


@pytest.mark.asyncio
async def test_bluetooth_flow_prefills_discovered_device(hass) -> None:
    flow = config_flow(hass, SOURCE_BLUETOOTH)
    info = discovery_info()
    with patch(
        "custom_components.miot_ble.config_flow._discovered_devices",
        return_value=supported_devices(),
    ):
        result = await flow.async_step_bluetooth(info)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["description_placeholders"] is None


@pytest.mark.asyncio
async def test_bluetooth_flow_rejects_non_miot_service(hass) -> None:
    flow = config_flow(hass, SOURCE_BLUETOOTH)
    result = await flow.async_step_bluetooth(discovery_info(product_id=None))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"

    result = await flow.async_step_bluetooth(discovery_info(product_id=0x0001))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


@pytest.mark.asyncio
async def test_reauth_retrieves_and_updates_local_key(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title="Dryer",
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_MODEL: MODEL,
            CONF_TOKEN: "aabbccddeeff001122334455",
            CONF_NAME: "Dryer",
        },
    )
    entry.add_to_hass(hass)
    flow = config_flow(hass, SOURCE_REAUTH)
    flow.context["entry_id"] = entry.entry_id

    result = await flow.async_step_reauth(entry.data)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "cloud_auth"

    client = SimpleNamespace(
        async_login=AsyncMock(),
        async_get_device_token=AsyncMock(return_value=TOKEN),
        clear_sensitive_data=MagicMock(),
        captcha_image=None,
        verification_url=None,
    )
    with (
        patch("custom_components.miot_ble.config_flow.XiaomiCloudClient", return_value=client),
        patch("custom_components.miot_ble.config_flow.async_create_clientsession"),
        patch(
            "custom_components.miot_ble.config_flow.async_validate_connection",
            new=AsyncMock(),
        ),
    ):
        result = await flow.async_step_cloud_auth(
            {"username": "user@example.com", "password": "secret"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == TOKEN
    assert "username" not in entry.data
    assert "password" not in entry.data


@pytest.mark.asyncio
async def test_validate_connection() -> None:
    with (
        patch(
            "custom_components.miot_ble.config_flow.async_ble_device_from_address",
            return_value=None,
        ),
        pytest.raises(ConnectionError, match="not currently available"),
    ):
        await async_validate_connection(MagicMock(), ADDRESS, bytes.fromhex(TOKEN), MODEL)

    client = SimpleNamespace(is_connected=True, disconnect=AsyncMock())
    session = SimpleNamespace(start=AsyncMock(), login=AsyncMock())
    with (
        patch(
            "custom_components.miot_ble.config_flow.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.miot_ble.config_flow.establish_connection",
            new=AsyncMock(return_value=client),
        ),
        patch(
            "custom_components.miot_ble.config_flow.MiotBleSession",
            return_value=session,
        ),
    ):
        await async_validate_connection(MagicMock(), ADDRESS, bytes.fromhex(TOKEN), MODEL)
    session.start.assert_awaited_once()
    session.login.assert_awaited_once()
    client.disconnect.assert_awaited_once()
