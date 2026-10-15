"""Tests for the Xiaomi MIoT BLE config flow."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_REAUTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_MODEL, CONF_NAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.miot_ble.config_flow import (
    CloudAuthenticationError,
    CloudConnectionError,
    CloudDeviceNotFoundError,
    MiotBleConfigFlow,
    _discovered_devices,
    _supports_miot_service,
    async_fetch_cloud_token,
    async_validate_connection,
    find_existing_token,
    normalize_token,
)
from custom_components.miot_ble.const import CONF_TOKEN, DOMAIN, UUID_MI_SERVICE
from custom_components.miot_ble.protocol import AuthenticationError

ADDRESS = "AA:BB:CC:DD:EE:FF"
TOKEN = "00112233445566778899aabb"
MODEL = "muxnw.dryer.m73"


def discovery_info(*, supported: bool = True, name: str | None = "M73") -> SimpleNamespace:
    """Build the discovery fields used by the config flow."""
    return SimpleNamespace(
        address=ADDRESS,
        name=name,
        service_uuids=[UUID_MI_SERVICE] if supported else [],
        service_data={},
    )


def config_flow(hass, source: str) -> MiotBleConfigFlow:
    """Build a config flow without loading optional platform dependencies."""
    flow = MiotBleConfigFlow()
    flow.hass = hass
    flow.context = {"source": source}
    return flow


def test_normalize_token() -> None:
    assert normalize_token(f"  {TOKEN.upper()}  ") == TOKEN
    for invalid in ("", "0011", "g" * 24):
        with pytest.raises(ValueError, match="24 hexadecimal"):
            normalize_token(invalid)


def test_service_detection_and_discovery_list() -> None:
    assert _supports_miot_service(discovery_info())
    service_data_info = discovery_info(supported=False)
    service_data_info.service_data = {UUID_MI_SERVICE: b"data"}
    assert _supports_miot_service(service_data_info)
    assert not _supports_miot_service(discovery_info(supported=False))

    with patch(
        "custom_components.miot_ble.config_flow.async_discovered_service_info",
        return_value=[discovery_info(), discovery_info(supported=False)],
    ):
        assert _discovered_devices(MagicMock()) == {ADDRESS: "M73"}


def test_find_existing_token(hass) -> None:
    entry = MockConfigEntry(
        domain="xiaomi_ble",
        unique_id=ADDRESS.lower(),
        data={"bindkey": TOKEN.upper()},
    )
    entry.add_to_hass(hass)
    assert find_existing_token(hass, ADDRESS) == TOKEN

    entry = MockConfigEntry(
        domain="xiaomi_ble",
        unique_id="11:22:33:44:55:66",
        data={"bindkey": "invalid"},
    )
    entry.add_to_hass(hass)
    assert find_existing_token(hass, "11:22:33:44:55:66") is None
    assert find_existing_token(hass, "22:33:44:55:66:77") is None


@pytest.mark.asyncio
async def test_user_flow_success(hass) -> None:
    flow = config_flow(hass, SOURCE_USER)
    with (
        patch("custom_components.miot_ble.config_flow._discovered_devices", return_value={}),
        patch(
            "custom_components.miot_ble.config_flow.async_validate_connection",
            new=AsyncMock(),
        ),
    ):
        result = await flow.async_step_user()
        assert result["type"] is FlowResultType.FORM
        result = await flow.async_step_user(
            {
                CONF_ADDRESS: ADDRESS,
                CONF_MODEL: MODEL,
                CONF_TOKEN: TOKEN.upper(),
                CONF_NAME: "Dryer",
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dryer"
    assert result["data"][CONF_TOKEN] == TOKEN
    assert result["data"][CONF_MODEL] == MODEL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("validation_error", "expected_error"),
    [
        (AuthenticationError("bad token"), "invalid_auth"),
        (ConnectionError("offline"), "cannot_connect"),
    ],
)
async def test_user_flow_connection_errors(
    hass,
    validation_error: Exception,
    expected_error: str,
) -> None:
    flow = config_flow(hass, SOURCE_USER)
    with (
        patch("custom_components.miot_ble.config_flow._discovered_devices", return_value={}),
        patch(
            "custom_components.miot_ble.config_flow.async_validate_connection",
            new=AsyncMock(side_effect=validation_error),
        ),
    ):
        result = await flow.async_step_user(
            {
                CONF_ADDRESS: ADDRESS,
                CONF_MODEL: MODEL,
                CONF_TOKEN: TOKEN,
                CONF_NAME: "Dryer",
            },
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == expected_error


@pytest.mark.asyncio
async def test_user_flow_rejects_invalid_token(hass) -> None:
    flow = config_flow(hass, SOURCE_USER)
    validate = AsyncMock()
    with (
        patch("custom_components.miot_ble.config_flow._discovered_devices", return_value={}),
        patch("custom_components.miot_ble.config_flow.async_validate_connection", validate),
    ):
        result = await flow.async_step_user(
            {
                CONF_ADDRESS: ADDRESS,
                CONF_MODEL: MODEL,
                CONF_TOKEN: "invalid",
                CONF_NAME: "",
            },
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"][CONF_TOKEN] == "invalid_token"
    validate.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_flow_reuses_xiaomi_ble_bindkey(hass) -> None:
    entry = MockConfigEntry(
        domain="xiaomi_ble",
        unique_id=ADDRESS,
        data={"bindkey": TOKEN},
    )
    entry.add_to_hass(hass)
    flow = config_flow(hass, SOURCE_USER)
    validate = AsyncMock()
    with (
        patch("custom_components.miot_ble.config_flow._discovered_devices", return_value={}),
        patch("custom_components.miot_ble.config_flow.async_validate_connection", validate),
    ):
        result = await flow.async_step_user(
            {
                CONF_ADDRESS: ADDRESS,
                CONF_MODEL: MODEL,
                CONF_TOKEN: "",
                CONF_NAME: "Dryer",
            }
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TOKEN] == TOKEN
    validate.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_flow_routes_missing_token_to_cloud(hass) -> None:
    flow = config_flow(hass, SOURCE_USER)
    validate = AsyncMock()
    with (
        patch("custom_components.miot_ble.config_flow._discovered_devices", return_value={}),
        patch("custom_components.miot_ble.config_flow.async_validate_connection", validate),
    ):
        result = await flow.async_step_user(
            {
                CONF_ADDRESS: ADDRESS,
                CONF_MODEL: MODEL,
                CONF_TOKEN: "",
                CONF_NAME: "Dryer",
            }
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "cloud_auth"
    validate.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloud_flow_fetches_token_without_storing_credentials(hass) -> None:
    flow = config_flow(hass, SOURCE_USER)
    result = await flow.async_step_user(
        {
            CONF_ADDRESS: ADDRESS,
            CONF_MODEL: MODEL,
            CONF_TOKEN: "",
            CONF_NAME: "Dryer",
        }
    )
    assert result["step_id"] == "cloud_auth"

    fetch = AsyncMock(return_value=TOKEN)
    validate = AsyncMock()
    with (
        patch("custom_components.miot_ble.config_flow.async_fetch_cloud_token", fetch),
        patch("custom_components.miot_ble.config_flow.async_validate_connection", validate),
    ):
        result = await flow.async_step_cloud_auth(
            {"username": "user@example.com", "password": "secret"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TOKEN] == TOKEN
    assert "username" not in result["data"]
    assert "password" not in result["data"]
    fetch.assert_awaited_once_with(hass, ADDRESS, "user@example.com", "secret")
    validate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cloud_error", "expected_error"),
    [
        (CloudAuthenticationError(), "cloud_invalid_auth"),
        (CloudDeviceNotFoundError(), "cloud_device_not_found"),
        (CloudConnectionError(), "cloud_error"),
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
    with patch(
        "custom_components.miot_ble.config_flow.async_fetch_cloud_token",
        new=AsyncMock(side_effect=cloud_error),
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
async def test_fetch_cloud_token(hass) -> None:
    from xiaomi_ble import XiaomiCloudBLEDevice

    fetcher = SimpleNamespace(
        get_device_info=AsyncMock(
            return_value=XiaomiCloudBLEDevice("Dryer", ADDRESS, TOKEN.upper())
        )
    )
    session = MagicMock()
    with (
        patch("xiaomi_ble.XiaomiCloudTokenFetch", return_value=fetcher) as fetch_class,
        patch(
            "custom_components.miot_ble.config_flow.async_get_clientsession",
            return_value=session,
        ),
    ):
        assert await async_fetch_cloud_token(hass, ADDRESS, "user@example.com", "secret") == TOKEN
    fetch_class.assert_called_once_with("user@example.com", "secret", session)
    fetcher.get_device_info.assert_awaited_once_with(ADDRESS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("library_error", "expected_error"),
    [
        ("auth", CloudAuthenticationError),
        ("cloud", CloudConnectionError),
        ("missing", CloudDeviceNotFoundError),
    ],
)
async def test_fetch_cloud_token_errors(
    hass, library_error: str, expected_error: type[Exception]
) -> None:
    from xiaomi_ble import XiaomiCloudException, XiaomiCloudInvalidAuthenticationException

    if library_error == "auth":
        side_effect = XiaomiCloudInvalidAuthenticationException("bad credentials")
    elif library_error == "cloud":
        side_effect = XiaomiCloudException("offline")
    else:
        side_effect = None
    fetcher = SimpleNamespace(
        get_device_info=(
            AsyncMock(return_value=None)
            if library_error == "missing"
            else AsyncMock(side_effect=side_effect)
        )
    )
    with (
        patch("xiaomi_ble.XiaomiCloudTokenFetch", return_value=fetcher),
        patch("custom_components.miot_ble.config_flow.async_get_clientsession"),
        pytest.raises(expected_error),
    ):
        await async_fetch_cloud_token(hass, ADDRESS, "user@example.com", "secret")


@pytest.mark.asyncio
async def test_bluetooth_flow_prefills_discovered_device(hass) -> None:
    flow = config_flow(hass, SOURCE_BLUETOOTH)
    info = discovery_info()
    with patch(
        "custom_components.miot_ble.config_flow._discovered_devices",
        return_value={ADDRESS: "M73"},
    ):
        result = await flow.async_step_bluetooth(info)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["description_placeholders"] is None


@pytest.mark.asyncio
async def test_bluetooth_flow_rejects_non_miot_service(hass) -> None:
    flow = config_flow(hass, SOURCE_BLUETOOTH)
    result = await flow.async_step_bluetooth(discovery_info(supported=False))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


@pytest.mark.asyncio
async def test_reauth_updates_token(hass) -> None:
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
    assert result["step_id"] == "reauth_confirm"

    with patch(
        "custom_components.miot_ble.config_flow.async_validate_connection",
        new=AsyncMock(),
    ):
        result = await flow.async_step_reauth_confirm({CONF_TOKEN: TOKEN.upper()})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == TOKEN


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
