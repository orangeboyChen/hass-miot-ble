"""Sensor entities for Xiaomi Miot BLE."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import MiotBleCoordinator
from .entity import MiotBleEntity, device_info
from .profile import EntityKind, PropertySpec, StateSensorSpec


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up read-only Miot sensor properties."""
    coordinator: MiotBleCoordinator = entry.runtime_data
    property_sensors = (
        MiotBleSensor(coordinator, prop)
        for prop in coordinator.profile.properties
        if prop.entity_kind is EntityKind.SENSOR
    )
    state_sensors = (
        MiotBleStateSensor(coordinator, spec) for spec in coordinator.profile.state_sensors
    )
    async_add_entities((*property_sensors, *state_sensors))


class MiotBleSensor(MiotBleEntity, SensorEntity):
    """Expose one Miot property as a sensor."""

    def __init__(self, coordinator: MiotBleCoordinator, prop: PropertySpec) -> None:
        super().__init__(coordinator, prop)
        if prop.unit == "s":
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.SECONDS
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif prop.unit == "min":
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif prop.unit == "%":
            self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | bool | None:
        """Return the latest native property value."""
        value = self.property_value
        if isinstance(value, bool) or not isinstance(value, int):
            return value
        return value * self.property_spec.value_multiplier


class MiotBleStateSensor(CoordinatorEntity[MiotBleCoordinator], SensorEntity):
    """Expose a mapped device state derived from a Miot property."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: MiotBleCoordinator, spec: StateSensorSpec) -> None:
        super().__init__(coordinator)
        self.state_sensor_spec = spec
        address = coordinator.address.replace(":", "").lower()
        self._attr_unique_id = f"{address}_{spec.name}"
        self._attr_name = spec.name.replace("_", " ").title()
        self._attr_device_info = device_info(coordinator)
        self._value_map = dict(spec.value_map)
        self._attr_options = list(self._value_map.values())

    @property
    def available(self) -> bool:
        """Return whether the device property channel is connected."""
        return super().available and self.coordinator.connected

    @property
    def native_value(self) -> str | None:
        """Return the mapped state for the latest source property value."""
        value = (self.coordinator.data or {}).get(self.state_sensor_spec.source)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return self._value_map.get(value)
