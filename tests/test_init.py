"""Tests for config entry setup and unload."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.miot_ble import async_setup_entry, async_unload_entry
from custom_components.miot_ble.const import PLATFORMS


@pytest.mark.asyncio
async def test_setup_entry() -> None:
    coordinator = SimpleNamespace(async_config_entry_first_refresh=AsyncMock())
    entry = SimpleNamespace(runtime_data=None)
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock())
    )
    with patch(
        "custom_components.miot_ble.coordinator.MiotBleCoordinator",
        return_value=coordinator,
    ):
        assert await async_setup_entry(hass, entry)
    assert entry.runtime_data is coordinator
    coordinator.async_config_entry_first_refresh.assert_awaited_once()
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(entry, PLATFORMS)


@pytest.mark.asyncio
async def test_unload_entry() -> None:
    coordinator = SimpleNamespace(async_shutdown=AsyncMock())
    entry = SimpleNamespace(runtime_data=coordinator)
    unload = AsyncMock(return_value=False)
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_unload_platforms=unload))
    assert not await async_unload_entry(hass, entry)
    coordinator.async_shutdown.assert_not_awaited()

    unload.return_value = True
    assert await async_unload_entry(hass, entry)
    coordinator.async_shutdown.assert_awaited_once()
    unload.assert_awaited_with(entry, PLATFORMS)
