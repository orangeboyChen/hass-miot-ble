"""Binary sensor entities for Xiaomi Miot BLE."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import MiotBleCoordinator
from .entity import MiotBleEntity, device_info
from .profile import EntityKind


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up read-only binary properties."""
    coordinator: MiotBleCoordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [MiotBleConnectionBinarySensor(coordinator)]
    entities.extend(
        MiotBleBinarySensor(coordinator, prop)
        for prop in coordinator.profile.properties
        if prop.entity_kind is EntityKind.BINARY_SENSOR
    )
    async_add_entities(entities)


class MiotBleConnectionBinarySensor(CoordinatorEntity[MiotBleCoordinator], BinarySensorEntity):
    """Expose the authenticated BLE property-channel status."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "connection"

    def __init__(self, coordinator: MiotBleCoordinator) -> None:
        super().__init__(coordinator)
        address = coordinator.address.replace(":", "").lower()
        self._attr_unique_id = f"{address}_connection"
        self._attr_device_info = device_info(coordinator)

    @property
    def available(self) -> bool:
        """Keep connection diagnostics available while disconnected."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the authenticated property channel is connected."""
        return self.coordinator.connected


class MiotBleBinarySensor(MiotBleEntity, BinarySensorEntity):
    """Expose one Miot property as a binary sensor."""

    @property
    def is_on(self) -> bool | None:
        """Return the latest binary property value."""
        value = self.property_value
        return None if value is None else bool(value)
