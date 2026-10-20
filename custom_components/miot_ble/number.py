"""Number entities for Xiaomi Miot BLE."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MiotBleCoordinator
from .entity import MiotBleEntity
from .profile import EntityKind, PropertySpec


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up writable numeric properties."""
    coordinator: MiotBleCoordinator = entry.runtime_data
    async_add_entities(
        MiotBleNumber(coordinator, prop)
        for prop in coordinator.profile.properties
        if prop.entity_kind is EntityKind.NUMBER
    )


class MiotBleNumber(MiotBleEntity, NumberEntity):
    """Control a bounded numeric Miot property."""

    def __init__(self, coordinator: MiotBleCoordinator, prop: PropertySpec) -> None:
        super().__init__(coordinator, prop)
        if prop.minimum is None or prop.maximum is None:
            raise ValueError("Number properties require minimum and maximum values")
        self._attr_native_min_value = prop.minimum
        self._attr_native_max_value = prop.maximum
        self._attr_native_step = prop.step or 1
        self._attr_native_unit_of_measurement = prop.unit

    @property
    def native_value(self) -> float | None:
        """Return the latest numeric property value."""
        value = self.property_value
        return None if not isinstance(value, int) else value

    async def async_set_native_value(self, value: float) -> None:
        """Write an integer value accepted by the property profile."""
        if not value.is_integer():
            raise ValueError(f"{self.property_spec.name} requires an integer value")
        normalized = self.property_spec.validate_value(int(value))
        await self.coordinator.async_set_property(self.property_spec, normalized)
