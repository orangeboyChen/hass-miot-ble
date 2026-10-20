"""Tests for the Xiaomi Miot BLE coordinator."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS, CONF_MODEL
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.miot_ble.const import CONF_TOKEN, DOMAIN
from custom_components.miot_ble.coordinator import MiotBleCoordinator
from custom_components.miot_ble.profile import ValueType
from custom_components.miot_ble.profiles import MUXNW_DRYER_M73
from custom_components.miot_ble.protocol import AuthenticationError, PropertyValue

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
        property_values={(2, 1): PropertyValue(2, 1, ValueType.UINT8, 1)},
        set_property=AsyncMock(return_value=PropertyValue(2, 1, ValueType.UINT8, 0)),
    )
    coordinator._ensure_session = AsyncMock(return_value=session)

    data = await coordinator._async_update_data()
    assert data == {(2, 1): 1}
    coordinator.async_set_updated_data(data)
    await coordinator.async_set_property(power, 0)
    assert coordinator.data[(2, 1)] == 0


def test_connected_requires_authenticated_receiver(hass) -> None:
    coordinator = make_coordinator(hass)
    coordinator._client = SimpleNamespace(is_connected=True)
    coordinator._session = SimpleNamespace(authenticated=True, receiver_running=True)

    assert coordinator.connected
    assert not coordinator.manually_disconnected

    coordinator._session.receiver_running = False
    assert not coordinator.connected


@pytest.mark.asyncio
async def test_update_failure_drops_connection(hass) -> None:
    coordinator = make_coordinator(hass)
    coordinator._ensure_session = AsyncMock(side_effect=ConnectionError("offline"))
    coordinator._drop_connection = AsyncMock()
    with pytest.raises(UpdateFailed, match="offline"):
        await coordinator._async_update_data()
    coordinator._drop_connection.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_failure_drops_connection(hass) -> None:
    coordinator = make_coordinator(hass)
    power = MUXNW_DRYER_M73.property(2, 1)
    coordinator._ensure_session = AsyncMock(side_effect=ConnectionError("offline"))
    coordinator._drop_connection = AsyncMock()

    with pytest.raises(ConnectionError, match="offline"):
        await coordinator.async_set_property(power, 1)

    assert coordinator._ensure_session.await_count == 2
    assert coordinator._drop_connection.await_count == 2


@pytest.mark.asyncio
async def test_set_reconnects_and_retries_once(hass) -> None:
    coordinator = make_coordinator(hass)
    power = MUXNW_DRYER_M73.property(2, 1)
    failed_session = SimpleNamespace(set_property=AsyncMock(side_effect=ConnectionError("lost")))
    recovered_session = SimpleNamespace(
        set_property=AsyncMock(return_value=PropertyValue(2, 1, ValueType.UINT8, 1))
    )
    coordinator._ensure_session = AsyncMock(side_effect=[failed_session, recovered_session])
    coordinator._drop_connection = AsyncMock()

    await coordinator.async_set_property(power, 1)

    coordinator._drop_connection.assert_awaited_once()
    recovered_session.set_property.assert_awaited_once_with(power, 1)
    assert coordinator.data == {(2, 1): 1}


@pytest.mark.asyncio
async def test_authentication_failure_requests_reauth(hass) -> None:
    coordinator = make_coordinator(hass)
    coordinator._ensure_session = AsyncMock(side_effect=AuthenticationError("bad token"))
    coordinator._drop_connection = AsyncMock()
    with pytest.raises(ConfigEntryAuthFailed, match="rejected"):
        await coordinator._async_update_data()
    coordinator._drop_connection.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_session_reuses_connection(hass) -> None:
    coordinator = make_coordinator(hass)
    existing = SimpleNamespace(
        authenticated=True,
        receiver_running=True,
        raise_if_receiver_failed=MagicMock(),
    )
    coordinator._session = existing
    coordinator._client = SimpleNamespace(is_connected=True)
    assert await coordinator._ensure_session() is existing
    existing.raise_if_receiver_failed.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_session_connects_and_authenticates(hass) -> None:
    coordinator = make_coordinator(hass)
    client = SimpleNamespace(is_connected=True, disconnect=AsyncMock())
    session = SimpleNamespace(
        start=AsyncMock(),
        login=AsyncMock(),
        subscribe_properties=AsyncMock(),
        async_close=AsyncMock(),
    )
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
    session.subscribe_properties.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_session_handles_offline_and_login_failure(hass) -> None:
    coordinator = make_coordinator(hass)
    stale_client = SimpleNamespace(is_connected=True, disconnect=AsyncMock())
    stale_session = SimpleNamespace(
        authenticated=False,
        receiver_running=False,
        async_close=AsyncMock(),
    )
    coordinator._client = stale_client
    coordinator._session = stale_session
    with (
        patch(
            "custom_components.miot_ble.coordinator.async_ble_device_from_address",
            return_value=None,
        ),
        pytest.raises(ConnectionError, match="not in range"),
    ):
        await coordinator._ensure_session()
    stale_session.async_close.assert_awaited_once()
    stale_client.disconnect.assert_awaited_once()

    client = SimpleNamespace(is_connected=True, disconnect=AsyncMock())
    session = SimpleNamespace(
        start=AsyncMock(),
        login=AsyncMock(side_effect=RuntimeError("bad")),
        async_close=AsyncMock(),
    )
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
        pytest.raises(RuntimeError, match="bad"),
    ):
        await coordinator._ensure_session()
    session.async_close.assert_awaited_once()
    client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_drop_and_shutdown(hass) -> None:
    coordinator = make_coordinator(hass)
    client = SimpleNamespace(is_connected=True, disconnect=AsyncMock())
    session = SimpleNamespace(cancel_background_tasks=MagicMock(), async_close=AsyncMock())
    coordinator.async_refresh = AsyncMock()
    coordinator._client = client
    coordinator._session = session
    coordinator._disconnected(MagicMock())
    assert coordinator._client is client
    coordinator._disconnected(client)
    assert coordinator._client is None
    session.cancel_background_tasks.assert_called_once()
    await hass.async_block_till_done()
    coordinator.async_refresh.assert_awaited_once()

    coordinator.async_refresh.reset_mock()
    coordinator._manually_disconnected = True
    coordinator._client = client
    coordinator._session = session
    coordinator._disconnected(client)
    await hass.async_block_till_done()
    coordinator.async_refresh.assert_not_awaited()

    coordinator._client = client
    coordinator._session = session
    await coordinator._drop_connection()
    session.async_close.assert_awaited_once()
    client.disconnect.assert_awaited_once()
    assert coordinator._session is None

    coordinator._drop_connection = AsyncMock()
    await coordinator.async_shutdown()
    coordinator._drop_connection.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_connection_controls(hass) -> None:
    coordinator = make_coordinator(hass)
    coordinator.async_set_updated_data({(2, 1): 0})
    coordinator._manually_disconnected = True

    assert await coordinator._async_update_data() == {(2, 1): 0}
    with pytest.raises(ConnectionError, match="manually disconnected"):
        await coordinator._ensure_session()

    session = SimpleNamespace(property_values={(2, 1): PropertyValue(2, 1, ValueType.UINT8, 1)})
    coordinator._ensure_session = AsyncMock(return_value=session)
    await coordinator.async_connect()
    assert not coordinator.manually_disconnected
    assert coordinator.data == {(2, 1): 1}

    coordinator._drop_connection = AsyncMock()
    await coordinator.async_disconnect()
    assert coordinator.manually_disconnected
    coordinator._drop_connection.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_connect_reports_failure(hass) -> None:
    coordinator = make_coordinator(hass)
    coordinator._manually_disconnected = True
    coordinator._ensure_session = AsyncMock(side_effect=ConnectionError("offline"))
    coordinator.async_update_listeners = MagicMock()

    with pytest.raises(ConnectionError, match="offline"):
        await coordinator.async_connect()

    assert not coordinator.manually_disconnected
    coordinator.async_update_listeners.assert_called_once()


def test_property_notifications_update_only_active_session(hass) -> None:
    coordinator = make_coordinator(hass)
    active_session = MagicMock()
    coordinator._session = active_session

    coordinator._handle_property_values(
        active_session,
        (PropertyValue(2, 1, ValueType.UINT8, 1),),
    )
    assert coordinator.data == {(2, 1): 1}

    coordinator._handle_property_values(
        MagicMock(),
        (PropertyValue(2, 1, ValueType.UINT8, 0),),
    )
    assert coordinator.data == {(2, 1): 1}
