"""Connection control buttons for Xiaomi MIoT BLE."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import MiotBleCoordinator
from .entity import device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up manual BLE connection controls."""
    coordinator: MiotBleCoordinator = entry.runtime_data
    async_add_entities(
        (
            MiotBleConnectButton(coordinator),
            MiotBleDisconnectButton(coordinator),
        )
    )


class MiotBleConnectionButton(CoordinatorEntity[MiotBleCoordinator], ButtonEntity):
    """Base class for manual BLE connection controls."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True

    def __init__(self, coordinator: MiotBleCoordinator, key: str) -> None:
        super().__init__(coordinator)
        address = coordinator.address.replace(":", "").lower()
        self._attr_unique_id = f"{address}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = device_info(coordinator)


class MiotBleConnectButton(MiotBleConnectionButton):
    """Connect the device and resume automatic reconnection."""

    def __init__(self, coordinator: MiotBleCoordinator) -> None:
        super().__init__(coordinator, "connect")

    @property
    def available(self) -> bool:
        """Allow manual connection only while disconnected."""
        return not self.coordinator.connected

    async def async_press(self) -> None:
        """Connect the BLE property channel."""
        await self.coordinator.async_connect()


class MiotBleDisconnectButton(MiotBleConnectionButton):
    """Disconnect the device and pause automatic reconnection."""

    def __init__(self, coordinator: MiotBleCoordinator) -> None:
        super().__init__(coordinator, "disconnect")

    @property
    def available(self) -> bool:
        """Allow manual disconnection only while connected."""
        return self.coordinator.connected

    async def async_press(self) -> None:
        """Disconnect the BLE property channel."""
        await self.coordinator.async_disconnect()
