"""Binary sensor entities for Xiaomi MIoT BLE."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MiotBleCoordinator
from .entity import MiotBleEntity
from .profile import EntityKind


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up read-only binary properties."""
    coordinator: MiotBleCoordinator = entry.runtime_data
    async_add_entities(
        MiotBleBinarySensor(coordinator, prop)
        for prop in coordinator.profile.properties
        if prop.entity_kind is EntityKind.BINARY_SENSOR
    )


class MiotBleBinarySensor(MiotBleEntity, BinarySensorEntity):
    """Expose one MIoT property as a binary sensor."""

    @property
    def is_on(self) -> bool | None:
        """Return the latest binary property value."""
        value = self.property_value
        return None if value is None else bool(value)
