"""Switch entities for Xiaomi MIoT BLE."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
    """Set up writable switch properties."""
    coordinator: MiotBleCoordinator = entry.runtime_data
    async_add_entities(
        MiotBleSwitch(coordinator, prop)
        for prop in coordinator.profile.properties
        if prop.entity_kind is EntityKind.SWITCH
    )


class MiotBleSwitch(MiotBleEntity, SwitchEntity):
    """Control a binary MIoT property."""

    @property
    def is_on(self) -> bool | None:
        """Return whether the property is enabled."""
        value = self.property_value
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: object) -> None:
        """Enable the property."""
        await self.coordinator.async_set_property(self.property_spec, 1)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Disable the property."""
        await self.coordinator.async_set_property(self.property_spec, 0)
