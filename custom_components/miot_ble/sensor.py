"""Sensor entities for Xiaomi MIoT BLE."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
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
    """Set up read-only MIoT sensor properties."""
    coordinator: MiotBleCoordinator = entry.runtime_data
    async_add_entities(
        MiotBleSensor(coordinator, prop)
        for prop in coordinator.profile.properties
        if prop.entity_kind is EntityKind.SENSOR
    )


class MiotBleSensor(MiotBleEntity, SensorEntity):
    """Expose one MIoT property as a sensor."""

    def __init__(self, coordinator: MiotBleCoordinator, prop: PropertySpec) -> None:
        super().__init__(coordinator, prop)
        if prop.unit == "s":
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.SECONDS
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | bool | None:
        """Return the latest native property value."""
        return self.property_value
