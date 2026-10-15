"""Select entities for Xiaomi MIoT BLE."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
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
    """Set up bounded integer properties as selects."""
    coordinator: MiotBleCoordinator = entry.runtime_data
    async_add_entities(
        MiotBleSelect(coordinator, prop)
        for prop in coordinator.profile.properties
        if prop.entity_kind is EntityKind.SELECT
    )


class MiotBleSelect(MiotBleEntity, SelectEntity):
    """Select one value from a bounded MIoT integer range."""

    def __init__(self, coordinator: MiotBleCoordinator, prop: PropertySpec) -> None:
        super().__init__(coordinator, prop)
        if prop.value_map:
            self._values = tuple(value for value, _ in prop.value_map)
            self._attr_options = [label for _, label in prop.value_map]
            return
        if prop.minimum is None or prop.maximum is None:
            raise ValueError("Select properties require minimum and maximum values")
        step = prop.step or 1
        self._values = tuple(range(prop.minimum, prop.maximum + 1, step))
        suffix = f" {prop.unit}" if prop.unit else ""
        self._attr_options = [f"{value}{suffix}" for value in self._values]

    @property
    def current_option(self) -> str | None:
        """Return the selected display value."""
        value = self.property_value
        if not isinstance(value, int) or value not in self._values:
            return None
        return self.options[self._values.index(value)]

    async def async_select_option(self, option: str) -> None:
        """Write the value represented by an option."""
        try:
            index = self.options.index(option)
        except ValueError as error:
            raise ValueError(f"Unsupported option: {option}") from error
        await self.coordinator.async_set_property(self.property_spec, self._values[index])
