"""Tests for the Xiaomi MIoT BLE coordinator."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS, CONF_MODEL
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.miot_ble.const import CONF_TOKEN, DOMAIN
from custom_components.miot_ble.coordinator import MiotBleCoordinator
from custom_components.miot_ble.profile import ValueType
from custom_components.miot_ble.profiles import MUXNW_DRYER_M73
from custom_components.miot_ble.protocol import PropertyValue

ADDRESS = "AA:BB:CC:DD:EE:FF"
TOKEN = "00112233445566778899aabb"


def make_coordinator(hass) -> MiotBleCoordinator:
    """Create a coordinator backed by a mock config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dryer",
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_MODEL: MUXNW_DRYER_M73.model,
            CONF_TOKEN: TOKEN,
        },
    )
    entry.add_to_hass(hass)
    return MiotBleCoordinator(hass, entry)


@pytest.mark.asyncio
async def test_update_and_set_property(hass) -> None:
    coordinator = make_coordinator(hass)
    power = MUXNW_DRYER_M73.property(2, 1)
    session = SimpleNamespace(
        read_properties=AsyncMock(
            return_value={(2, 1): PropertyValue(2, 1, ValueType.UINT8, 1)}
        ),
        set_property=AsyncMock(return_value=PropertyValue(2, 1, ValueType.UINT8, 0)),
    )
    coordinator._ensure_session = AsyncMock(return_value=session)

    data = await coordinator._async_update_data()
    assert data == {(2, 1): 1}
    coordinator.async_set_updated_data(data)
    await coordinator.async_set_property(power, 0)
    assert coordinator.data[(2, 1)] == 0


@pytest.mark.asyncio
async def test_update_failure_drops_connection(hass) -> None:
    coordinator = make_coordinator(hass)
    coordinator._ensure_session = AsyncMock(side_effect=ConnectionError("offline"))
    coordinator._drop_connection = AsyncMock()
    with pytest.raises(UpdateFailed, match="offline"):
        await coordinator._async_update_data()
    coordinator._drop_connection.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_session_reuses_connection(hass) -> None:
    coordinator = make_coordinator(hass)
    existing = MagicMock()
    coordinator._session = existing
    coordinator._client = SimpleNamespace(is_connected=True)
    assert await coordinator._ensure_session() is existing


@pytest.mark.asyncio
async def test_ensure_session_connects_and_authenticates(hass) -> None:
    coordinator = make_coordinator(hass)
    client = SimpleNamespace(is_connected=True, disconnect=AsyncMock())
    session = SimpleNamespace(start=AsyncMock(), login=AsyncMock())
    with (
        patch(
            "custom_components.miot_ble.coordinator.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.miot_ble.coordinator.establish_connection",
            new=AsyncMock(return_value=client),
        ),
        patch(
            "custom_components.miot_ble.coordinator.MiotBleSession",
            return_value=session,
        ),
    ):
        assert await coordinator._ensure_session() is session
    assert coordinator._client is client
    session.start.assert_awaited_once()
    session.login.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_session_handles_offline_and_login_failure(hass) -> None:
    coordinator = make_coordinator(hass)
    with patch(
        "custom_components.miot_ble.coordinator.async_ble_device_from_address",
        return_value=None,
    ), pytest.raises(ConnectionError, match="not in range"):
        await coordinator._ensure_session()

    client = SimpleNamespace(is_connected=True, disconnect=AsyncMock())
    session = SimpleNamespace(start=AsyncMock(), login=AsyncMock(side_effect=RuntimeError("bad")))
    with (
        patch(
            "custom_components.miot_ble.coordinator.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.miot_ble.coordinator.establish_connection",
            new=AsyncMock(return_value=client),
        ),
        patch(
            "custom_components.miot_ble.coordinator.MiotBleSession",
            return_value=session,
        ),pytest.raises(RuntimeError, match="bad")
    ):
        await coordinator._ensure_session()
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_drop_and_shutdown(hass) -> None:
    coordinator = make_coordinator(hass)
    client = SimpleNamespace(is_connected=True, disconnect=AsyncMock())
    coordinator._client = client
    coordinator._session = MagicMock()
    coordinator._disconnected(MagicMock())
    assert coordinator._client is client
    coordinator._disconnected(client)
    assert coordinator._client is None

    coordinator._client = client
    coordinator._session = MagicMock()
    await coordinator._drop_connection()
    client.disconnect.assert_awaited_once()
    assert coordinator._session is None

    coordinator._drop_connection = AsyncMock()
    await coordinator.async_shutdown()
    coordinator._drop_connection.assert_awaited_once()
